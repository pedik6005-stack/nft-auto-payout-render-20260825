from __future__ import annotations

import asyncio
import os

from aiohttp import web

from app.main import main as bot_main


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok\n")


async def run_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await asyncio.Event().wait()


async def main() -> None:
    await asyncio.gather(run_health_server(), bot_main())


if __name__ == "__main__":
    asyncio.run(main())
