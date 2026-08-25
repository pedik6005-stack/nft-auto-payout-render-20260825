from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app import __version__
from app.admin import router as admin_router
from app.config import Settings, load_settings
from app.db import Database
from app.handlers import router as user_router
from app.logging_setup import setup_logging
from app.providers.base import MarketProvider
from app.providers.demo import DemoProvider
from app.providers.mrkt import MrktProvider
from app.services.analytics import AnalyticsConfig, MarketAnalyzer
from app.services.monitor import MarketMonitor
from app.services.nft_verifier import NftVerifier
from app.services.notifier import Notifier
from app.services.payouts import PayoutService
from app.services.visual import VisualEngine

logger = logging.getLogger(__name__)


def build_provider(settings: Settings) -> MarketProvider:
    if settings.market_mode == "mrkt":
        return MrktProvider(settings)
    return DemoProvider()


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger.info("Starting payout portal v%s", __version__)

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
    # The payout portal does not scan MRKT. A local provider object is kept only
    # for compatibility with the legacy analytics handlers, which are not shown.
    provider = DemoProvider()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier = Notifier(bot)
    payout_service = PayoutService(settings)
    nft_verifier = NftVerifier(settings)
    monitor = MarketMonitor(settings, db, provider, analyzer, notifier)

    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Открыть главное меню"),
    ])

    try:
        await dp.start_polling(
            bot,
            settings=settings,
            db=db,
            visual=visual,
            analyzer=analyzer,
            provider=provider,
            notifier=notifier,
            payout_service=payout_service,
            nft_verifier=nft_verifier,
            monitor=monitor,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        if monitor.status.running:
            await monitor.stop()
        await provider.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
