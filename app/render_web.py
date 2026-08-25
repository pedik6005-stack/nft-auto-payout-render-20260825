from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app import __version__
from app.admin import router as admin_router
from app.config import load_settings
from app.db import Database
from app.handlers import router as user_router
from app.logging_setup import setup_logging
from app.providers.demo import DemoProvider
from app.services.analytics import AnalyticsConfig, MarketAnalyzer
from app.services.monitor import MarketMonitor
from app.services.nft_verifier import NftVerifier
from app.services.notifier import Notifier
from app.services.payouts import PayoutService
from app.services.visual import VisualEngine

logger = logging.getLogger(__name__)


def _public_url() -> str:
    explicit = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if explicit:
        return explicit.rstrip("/")
    return "https://nft-auto-payout-render-20260825.onrender.com"


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok\n")


async def build_app() -> web.Application:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger.info("Starting payout portal webhooks v%s", __version__)

    db = Database(settings.database_path)
    await db.init()

    visual = VisualEngine(settings.rules_path)
    analyzer = MarketAnalyzer(
        visual,
        AnalyticsConfig(
            sale_fee_percent=settings.sale_fee_percent,
            base_reserve_percent=settings.base_reserve_percent,
            low_outlier_gap_percent=settings.low_outlier_gap_percent,
            high_outlier_multiplier=settings.high_outlier_multiplier,
        ),
    )
    provider = DemoProvider()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    notifier = Notifier(bot)
    payout_service = PayoutService(settings)
    nft_verifier = NftVerifier(settings)
    monitor = MarketMonitor(settings, db, provider, analyzer, notifier)

    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(user_router)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)

    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram/webhook")
    webhook_secret = os.getenv("WEBHOOK_SECRET_TOKEN") or None
    webhook_url = f"{_public_url()}{webhook_path}"

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=webhook_secret,
        settings=settings,
        db=db,
        visual=visual,
        analyzer=analyzer,
        provider=provider,
        notifier=notifier,
        payout_service=payout_service,
        nft_verifier=nft_verifier,
        monitor=monitor,
    ).register(app, path=webhook_path)

    async def on_startup(_: web.Application) -> None:
        await bot.set_my_commands([BotCommand(command="start", description="Открыть главное меню")])
        await bot.set_webhook(
            webhook_url,
            secret_token=webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Webhook set to %s", webhook_url)

    async def on_cleanup(_: web.Application) -> None:
        if monitor.status.running:
            await monitor.stop()
        await provider.close()
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.session.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    setup_application(app, dp, bot=bot)
    return app


async def main() -> None:
    app = await build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("HTTP server started on port %s", port)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
