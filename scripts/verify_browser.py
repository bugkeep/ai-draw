import argparse
import asyncio
import os
import socket
from pathlib import Path

import uvicorn

from server.app import create_app


async def verify(args):
    root = Path(__file__).resolve().parent.parent
    database = args.database.resolve()
    if database == (root / "runs/studio.sqlite3").resolve():
        raise ValueError("Use a separate browser evaluation database")
    os.environ["DRAW_AGENT_DB"] = str(database)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning"))
        worker = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if worker.done():
                    await worker
                    raise RuntimeError("Browser test server did not start")
                await asyncio.sleep(0.05)
            environment = {**os.environ, "DRAW_AGENT_URL": f"http://127.0.0.1:{port}"}
            for script in args.scripts:
                path = (root / "frontend-app/scripts" / script).resolve()
                if path.parent != (root / "frontend-app/scripts").resolve() or not path.is_file():
                    raise ValueError("Choose an existing frontend browser script")
                process = await asyncio.create_subprocess_exec("node", str(path), cwd=root / "frontend-app", env=environment)
                code = await process.wait()
                if code:
                    raise RuntimeError(f"Browser check failed: {script} (exit {code})")
        finally:
            server.should_exit = True
            await worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("runs/browser-verification.sqlite3"))
    parser.add_argument("--scripts", nargs="+", default=[
        "deletion_smoke.js", "browser_smoke.js", "export_smoke.js", "settings_smoke.js", "sessions_smoke.js",
        "scene_sync_smoke.js",
    ])
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
