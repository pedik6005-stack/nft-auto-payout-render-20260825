from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import Settings
from app.db import Database
from app.domain import AnalysisResult, Listing, SearchMode, UserFilters
from app.providers.base import MarketProvider
from app.services.analytics import MarketAnalyzer
from app.services.notifier import Notifier

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MonitorStatus:
    running: bool = False
    last_poll_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    polls: int = 0
    received: int = 0
    analyzed: int = 0
    alerts: int = 0


class MarketMonitor:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        provider: MarketProvider,
        analyzer: MarketAnalyzer,
        notifier: Notifier,
    ):
        self.settings = settings
        self.db = db
        self.provider = provider
        self.analyzer = analyzer
        self.notifier = notifier
        self.status = MonitorStatus()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._analysis_lock = asyncio.Semaphore(3)
        self._initialized = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self.status.running = True
        self._task = asyncio.create_task(self._run(), name="market-monitor")

    async def stop(self) -> None:
        self.status.running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            self.status.last_poll_at = datetime.now(timezone.utc)
            self.status.polls += 1
            try:
                listings = await self.provider.fetch_recent(self.settings.recent_limit)
                self.status.received += len(listings)
                fresh: list[Listing] = []
                for listing in listings:
                    if not await self.db.is_seen(listing.id):
                        await self.db.save_listing(listing)
                        fresh.append(listing)
                    else:
                        await self.db.save_listing(listing)
                if fresh and (self._initialized or self.settings.alert_existing_on_start):
                    await asyncio.gather(*(self._handle_listing(item) for item in fresh))
                self._initialized = True
                self.status.last_success_at = datetime.now(timezone.utc)
                self.status.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.last_error = str(exc)
                logger.exception("Market monitor iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.settings.monitor_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _handle_listing(self, listing: Listing) -> None:
        async with self._analysis_lock:
            try:
                result = await self.analyze_listing(listing)
                self.status.analyzed += 1
                if not result.is_profitable or result.score < 55:
                    return
                users = await self.db.active_users()
                for filters in users:
                    if not self._matches(filters, result):
                        continue
                    if await self.db.notifications_last_hour(filters.user_id) >= filters.max_alerts_per_hour:
                        continue
                    await self._deliver(filters, result)
            except Exception:
                logger.exception("Could not analyze listing %s", listing.id)

    async def analyze_listing(self, listing: Listing) -> AnalysisResult:
        exact_task = self.provider.fetch_comparables(
            collection=listing.collection, model=listing.model,
            backdrop=listing.backdrop, limit=self.settings.comparable_limit,
        )
        backdrop_task = self.provider.fetch_comparables(
            collection=listing.collection, backdrop=listing.backdrop,
            limit=self.settings.comparable_limit,
        )
        model_task = self.provider.fetch_comparables(
            collection=listing.collection, model=listing.model,
            limit=self.settings.comparable_limit,
        )
        collection_task = self.provider.fetch_comparables(
            collection=listing.collection, limit=self.settings.comparable_limit,
        )
        exact, backdrop, model, collection = await asyncio.gather(
            exact_task, backdrop_task, model_task, collection_task
        )
        scope_key = listing.combo_key
        previous = await self.db.get_previous_snapshot(scope_key)
        previous_floor = float(previous["cleaned_floor"]) if previous and previous["cleaned_floor"] else None
        result = self.analyzer.analyze(
            listing, exact, backdrop, model, collection,
            previous_exact_floor=previous_floor,
        )
        await self.db.save_snapshot(
            scope_key,
            result.exact.cleaned_floor,
            result.exact.quick_sale,
            result.exact.confidence,
            result.exact.cleaned_count,
        )
        return result

    async def refresh_listing(self, listing_id: str) -> AnalysisResult | None:
        verified = await self.provider.verify_listing(listing_id)
        if not verified:
            return None
        await self.db.save_listing(verified)
        return await self.analyze_listing(verified)

    async def _deliver(self, filters: UserFilters, initial: AnalysisResult) -> None:
        preliminary_message = None
        if self.settings.fast_alerts and initial.score >= max(70, filters.min_score):
            preliminary_message = await self.notifier.send_analysis(filters.user_id, initial, preliminary=True)

        # Recheck availability and floors immediately before final signal.
        verified = await self.provider.verify_listing(initial.listing.id)
        if not verified or abs(verified.price_ton - initial.listing.price_ton) > 1e-6:
            if preliminary_message:
                try:
                    await preliminary_message.edit_text("❌ Лот больше неактуален или цена изменилась.")
                except Exception:
                    pass
            return
        final = await self.analyze_listing(verified)
        if not self._matches(filters, final):
            if preliminary_message:
                try:
                    if preliminary_message.photo:
                        await preliminary_message.edit_caption("❌ После повторной проверки потенциальная выгода исчезла.")
                    else:
                        await preliminary_message.edit_text("❌ После повторной проверки потенциальная выгода исчезла.")
                except Exception:
                    pass
            return

        if preliminary_message:
            await self.notifier.update_analysis(preliminary_message, final)
            message_id = preliminary_message.message_id
        else:
            message = await self.notifier.send_analysis(filters.user_id, final)
            message_id = message.message_id
        inserted = await self.db.record_notification(
            filters.user_id, final.listing.id, message_id,
            final.score, final.confidence, final.net_profit_ton, final.roi_percent,
        )
        if inserted:
            self.status.alerts += 1

    @staticmethod
    def _matches(filters: UserFilters, result: AnalysisResult) -> bool:
        if filters.max_price_ton > 0 and result.listing.price_ton > filters.max_price_ton:
            return False
        if result.net_profit_ton < filters.min_profit_ton:
            return False
        if result.roi_percent < filters.min_roi_percent:
            return False
        if result.score < filters.min_score or result.confidence < filters.min_confidence:
            return False
        if filters.search_mode == SearchMode.MONOCHROME and not result.visual.is_monochrome:
            return False
        if filters.search_mode == SearchMode.BLACK:
            backdrop = result.listing.backdrop.casefold()
            if backdrop not in {"black", "onyx black"}:
                return False
        return True
