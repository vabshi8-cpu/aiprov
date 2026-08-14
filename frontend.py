#!/usr/bin/env python3

import asyncio
import json
import os
import platform
import sys
import shlex

try:
    import websockets
except ImportError:
    sys.exit("run:  pip install websockets")

BACKEND_WS = "wss://jm9ygl-8080.csb.app/ws"
HEARTBEAT  = 25


def prompt(msg: str) -> str:
    try:
        return input(msg)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


async def run_cmd(cmd: str, use_root: bool, cwd: str) -> dict:
    # we let the shell handle pipes/&&/etc; the AI will write real shell.
    if use_root and os.geteuid() != 0:
        cmd = f"sudo -n bash -lc {shlex.quote(cmd)}"
    else:
        cmd = f"bash -lc {shlex.quote(cmd)}"

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return {"stdout": "", "stderr": "[frontend] command timed out (5m)", "exit": 124}
        return {
            "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"),
            "exit":   proc.returncode,
        }
    except Exception as e:
        return {"stdout": "", "stderr": f"[frontend] {type(e).__name__}: {e}", "exit": 1}


async def session():
    key = prompt("Please enter you key: ").strip()
    if not key:
        print("no key given.")
        return

    try:
        ws = await websockets.connect(BACKEND_WS, ping_interval=HEARTBEAT, max_size=8 * 1024 * 1024)
    except Exception as e:
        print(f"cannot reach backend: {e}")
        return

    async with ws:
        await ws.send(json.dumps({
            "type": "auth",
            "key":  key,
            "host": {
                "hostname": platform.node(),
                "os":       f"{platform.system()} {platform.release()}",
                "user":     os.environ.get("USER") or os.environ.get("USERNAME") or "?",
                "root":     os.geteuid() == 0,
            },
        }))
        reply = json.loads(await ws.recv())

        if reply.get("status") != "ok":
            print("Invalid key.")
            return

        print("Done! For the agent to work, do you want to give root access? "
              "so that the agent will work. Dont worry, we will keep things on ourside encrypted (y/n)")
        ans = prompt("> ").strip().lower()

        if ans != "y":
            print("we cannot work without root, sorry.")
            await ws.send(json.dumps({"type": "abort"}))
            return

        await ws.send(json.dumps({"type": "provision", "root": True}))
        print("PROVISION DONE! Dm the bot with /aiwork so that it will do the work for you")

        cwd = os.path.expanduser("~")

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")

            if kind == "cmd":
                # allow the agent to persist directory changes
                cmd = msg["cmd"]
                if cmd.strip().startswith("cd "):
                    target = cmd.strip()[3:].strip().strip('"').strip("'")
                    target = os.path.expanduser(target)
                    if not os.path.isabs(target):
                        target = os.path.join(cwd, target)
                    exists = os.path.isdir(target)
                    if exists:
                        cwd = os.path.abspath(target)
                        result = {"stdout": cwd, "stderr": "", "exit": 0}
                    else:
                        result = {"stdout": "", "stderr": f"no such dir: {target}", "exit": 1}
                else:
                    result = await run_cmd(cmd, use_root=True, cwd=cwd)

                await ws.send(json.dumps({
                    "type": "result",
                    "id":   msg.get("id"),
                    "cwd":  cwd,
                    **result,
                }))

            elif kind == "bye":
                print("backend closed session.")
                return


def main():
    try:
        asyncio.run(session())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
