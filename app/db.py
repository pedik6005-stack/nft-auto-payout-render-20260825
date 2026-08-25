from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.domain import Listing, SearchMode, UserFilters

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_blocked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_filters (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    max_price_ton REAL NOT NULL DEFAULT 50,
    min_profit_ton REAL NOT NULL DEFAULT 2,
    min_roi_percent REAL NOT NULL DEFAULT 10,
    min_score INTEGER NOT NULL DEFAULT 70,
    min_confidence INTEGER NOT NULL DEFAULT 55,
    search_mode TEXT NOT NULL DEFAULT 'all',
    max_alerts_per_hour INTEGER NOT NULL DEFAULT 20
);

CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    collection_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    backdrop_name TEXT NOT NULL,
    symbol_name TEXT NOT NULL,
    gift_number INTEGER,
    price_ton REAL NOT NULL,
    image_url TEXT,
    animation_url TEXT,
    listing_url TEXT,
    listed_at TEXT NOT NULL,
    seller_id TEXT,
    is_on_sale INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_combo
ON listings(collection_name, model_name, backdrop_name, price_ton);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    telegram_message_id INTEGER,
    score INTEGER NOT NULL,
    confidence INTEGER NOT NULL,
    net_profit_ton REAL NOT NULL,
    roi_percent REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    sent_at TEXT NOT NULL,
    UNIQUE(user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    user_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, listing_id)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    cleaned_floor REAL,
    quick_sale REAL,
    confidence INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_scope_time
ON market_snapshots(scope_key, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_wallets (
    worker TEXT PRIMARY KEY,
    wallet TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    worker_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    checks_json TEXT NOT NULL DEFAULT '{}',
    applied_at TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payout_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker TEXT NOT NULL,
    worker_wallet TEXT NOT NULL,
    bot_name TEXT NOT NULL,
    mentor TEXT,
    floor_ton REAL NOT NULL,
    hold_percent REAL NOT NULL,
    amount_ton REAL NOT NULL,
    status TEXT NOT NULL,
    tx_hash TEXT,
    raw_output TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payout_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    worker TEXT NOT NULL,
    nft_link TEXT NOT NULL,
    nft_slug TEXT NOT NULL UNIQUE,
    wallet TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'checking',
    floor_ton REAL,
    hold_percent REAL,
    amount_ton REAL,
    price_source TEXT,
    check_details TEXT,
    reviewed_by INTEGER,
    payout_record_id INTEGER REFERENCES payout_records(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payout_requests_status
ON payout_requests(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payout_requests_user
ON payout_requests(user_id, created_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_worker_key(worker: str) -> str:
    value = str(worker).strip()
    if value.startswith("@"):
        return "@" + value[1:].casefold()
    if value.startswith("id:"):
        return "id:" + value.partition(":")[2].strip()
    return value


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def init(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_user_sync, user_id, username, full_name)

    def _upsert_user_sync(self, user_id: int, username: str | None, full_name: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, username, full_name, created_at, last_seen_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (user_id, username, full_name, now, now),
            )
            conn.execute("INSERT OR IGNORE INTO user_filters(user_id) VALUES(?)", (user_id,))

    async def get_filters(self, user_id: int) -> UserFilters:
        return await asyncio.to_thread(self._get_filters_sync, user_id)

    def _get_filters_sync(self, user_id: int) -> UserFilters:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_filters WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                now = _now()
                conn.execute(
                    "INSERT OR IGNORE INTO users(user_id, username, full_name, created_at, last_seen_at) VALUES(?,?,?,?,?)",
                    (user_id, None, str(user_id), now, now),
                )
                conn.execute("INSERT OR IGNORE INTO user_filters(user_id) VALUES(?)", (user_id,))
                row = conn.execute("SELECT * FROM user_filters WHERE user_id=?", (user_id,)).fetchone()
        assert row is not None
        return UserFilters(
            user_id=user_id,
            monitoring_enabled=bool(row["monitoring_enabled"]),
            max_price_ton=float(row["max_price_ton"]),
            min_profit_ton=float(row["min_profit_ton"]),
            min_roi_percent=float(row["min_roi_percent"]),
            min_score=int(row["min_score"]),
            min_confidence=int(row["min_confidence"]),
            search_mode=SearchMode(row["search_mode"]),
            max_alerts_per_hour=int(row["max_alerts_per_hour"]),
        )

    async def update_filters(self, user_id: int, **changes: Any) -> UserFilters:
        allowed = {
            "monitoring_enabled", "max_price_ton", "min_profit_ton",
            "min_roi_percent", "min_score", "min_confidence",
            "search_mode", "max_alerts_per_hour",
        }
        safe = {k: v for k, v in changes.items() if k in allowed}
        if not safe:
            return await self.get_filters(user_id)
        if isinstance(safe.get("search_mode"), SearchMode):
            safe["search_mode"] = safe["search_mode"].value
        if "monitoring_enabled" in safe:
            safe["monitoring_enabled"] = int(bool(safe["monitoring_enabled"]))
        await self.get_filters(user_id)
        async with self._lock:
            await asyncio.to_thread(self._update_filters_sync, user_id, safe)
        return await self.get_filters(user_id)

    def _update_filters_sync(self, user_id: int, safe: dict[str, Any]) -> None:
        assignments = ", ".join(f"{k}=?" for k in safe)
        params = list(safe.values()) + [user_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE user_filters SET {assignments} WHERE user_id=?", params)

    async def active_users(self) -> list[UserFilters]:
        return await asyncio.to_thread(self._active_users_sync)

    def _active_users_sync(self) -> list[UserFilters]:
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute(
                """SELECT f.user_id FROM user_filters f JOIN users u ON u.user_id=f.user_id
                   WHERE f.monitoring_enabled=1 AND u.is_blocked=0"""
            ).fetchall()]
        return [self._get_filters_sync(uid) for uid in ids]


    async def set_app_setting(self, key: str, value: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_app_setting_sync, key, value)

    def _set_app_setting_sync(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, _now()),
            )

    async def get_app_setting(self, key: str) -> str | None:
        return await asyncio.to_thread(self._get_app_setting_sync, key)

    def _get_app_setting_sync(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    async def get_app_settings(self) -> dict[str, str]:
        return await asyncio.to_thread(self._get_app_settings_sync)

    def _get_app_settings_sync(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    async def set_worker_wallet(self, worker: str, wallet: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_worker_wallet_sync, worker, wallet)

    def _set_worker_wallet_sync(self, worker: str, wallet: str) -> None:
        worker = _normalize_worker_key(worker)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO worker_wallets(worker, wallet, updated_at) VALUES(?,?,?)
                   ON CONFLICT(worker) DO UPDATE SET wallet=excluded.wallet, updated_at=excluded.updated_at""",
                (worker, wallet, _now()),
            )

    async def get_worker_wallet(self, worker: str) -> str | None:
        return await asyncio.to_thread(self._get_worker_wallet_sync, worker)

    def _get_worker_wallet_sync(self, worker: str) -> str | None:
        worker = _normalize_worker_key(worker)
        with self._connect() as conn:
            row = conn.execute("SELECT wallet FROM worker_wallets WHERE worker=?", (worker,)).fetchone()
            return str(row["wallet"]) if row else None

    async def set_worker_profile(
        self,
        user_id: int,
        worker_key: str,
        status: str,
        checks: dict[str, Any],
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_worker_profile_sync, user_id, worker_key, status, checks
            )

    def _set_worker_profile_sync(
        self,
        user_id: int,
        worker_key: str,
        status: str,
        checks: dict[str, Any],
    ) -> None:
        now = _now()
        approved_at = now if status == "approved" else None
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO worker_profiles(
                    user_id, worker_key, status, checks_json, applied_at, approved_at, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    worker_key=excluded.worker_key,
                    status=excluded.status,
                    checks_json=excluded.checks_json,
                    applied_at=COALESCE(worker_profiles.applied_at, excluded.applied_at),
                    approved_at=COALESCE(worker_profiles.approved_at, excluded.approved_at),
                    updated_at=excluded.updated_at""",
                (
                    user_id, _normalize_worker_key(worker_key), status,
                    json.dumps(checks, ensure_ascii=False), now, approved_at, now,
                ),
            )

    async def get_worker_profile(self, user_id: int) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._get_worker_profile_sync, user_id)

    def _get_worker_profile_sync(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM worker_profiles WHERE user_id=?", (user_id,)
            ).fetchone()

    async def record_payout(
        self,
        worker: str,
        worker_wallet: str,
        bot_name: str,
        mentor: str | None,
        floor_ton: float,
        hold_percent: float,
        amount_ton: float,
        status: str,
        tx_hash: str | None,
        raw_output: str | None,
    ) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._record_payout_sync,
                worker, worker_wallet, bot_name, mentor, floor_ton, hold_percent,
                amount_ton, status, tx_hash, raw_output,
            )

    def _record_payout_sync(
        self,
        worker: str,
        worker_wallet: str,
        bot_name: str,
        mentor: str | None,
        floor_ton: float,
        hold_percent: float,
        amount_ton: float,
        status: str,
        tx_hash: str | None,
        raw_output: str | None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO payout_records(
                    worker, worker_wallet, bot_name, mentor, floor_ton, hold_percent,
                    amount_ton, status, tx_hash, raw_output, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (worker, worker_wallet, bot_name, mentor, floor_ton, hold_percent,
                 amount_ton, status, tx_hash, raw_output, _now()),
            )
            return int(cur.lastrowid)

    async def create_payout_request(
        self,
        user_id: int,
        worker: str,
        nft_link: str,
        nft_slug: str,
        wallet: str,
    ) -> tuple[sqlite3.Row, bool]:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_payout_request_sync,
                user_id, worker, nft_link, nft_slug, wallet,
            )

    def _create_payout_request_sync(
        self,
        user_id: int,
        worker: str,
        nft_link: str,
        nft_slug: str,
        wallet: str,
    ) -> tuple[sqlite3.Row, bool]:
        now = _now()
        slug = nft_slug.strip().casefold()
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """INSERT INTO payout_requests(
                        user_id, worker, nft_link, nft_slug, wallet, status, created_at, updated_at
                    ) VALUES(?,?,?,?,?,'checking',?,?)""",
                    (user_id, _normalize_worker_key(worker), nft_link, slug, wallet, now, now),
                )
                request_id = int(cur.lastrowid)
                created = True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM payout_requests WHERE nft_slug=?", (slug,)
                ).fetchone()
                assert row is not None
                return row, False
            row = conn.execute("SELECT * FROM payout_requests WHERE id=?", (request_id,)).fetchone()
            assert row is not None
            return row, created

    async def set_payout_request_verification(
        self,
        request_id: int,
        *,
        status: str,
        floor_ton: float | None,
        hold_percent: float | None,
        amount_ton: float | None,
        price_source: str | None,
        check_details: str,
    ) -> sqlite3.Row | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._set_payout_request_verification_sync,
                request_id, status, floor_ton, hold_percent, amount_ton,
                price_source, check_details,
            )

    def _set_payout_request_verification_sync(
        self,
        request_id: int,
        status: str,
        floor_ton: float | None,
        hold_percent: float | None,
        amount_ton: float | None,
        price_source: str | None,
        check_details: str,
    ) -> sqlite3.Row | None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE payout_requests SET
                    status=?, floor_ton=?, hold_percent=?, amount_ton=?,
                    price_source=?, check_details=?, updated_at=?
                   WHERE id=? AND status='checking'""",
                (status, floor_ton, hold_percent, amount_ton, price_source,
                 check_details[:2000], _now(), request_id),
            )
            return conn.execute("SELECT * FROM payout_requests WHERE id=?", (request_id,)).fetchone()

    async def get_payout_request(self, request_id: int) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._get_payout_request_sync, request_id)

    def _get_payout_request_sync(self, request_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM payout_requests WHERE id=?", (request_id,)).fetchone()

    async def latest_user_payout_request(self, user_id: int) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._latest_user_payout_request_sync, user_id)

    def _latest_user_payout_request_sync(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM payout_requests WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()

    async def list_payout_requests(self, limit: int = 15) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._list_payout_requests_sync, limit)

    def _list_payout_requests_sync(self, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM payout_requests ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()

    async def approve_payout_request(
        self, request_id: int, admin_id: int, bot_name: str, mentor: str | None
    ) -> sqlite3.Row | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._approve_payout_request_sync, request_id, admin_id, bot_name, mentor
            )

    def _approve_payout_request_sync(
        self, request_id: int, admin_id: int, bot_name: str, mentor: str | None
    ) -> sqlite3.Row | None:
        with self._connect() as conn:
            request = conn.execute(
                "SELECT * FROM payout_requests WHERE id=? AND status='ready_for_approval'",
                (request_id,),
            ).fetchone()
            if request is None:
                return None
            cur = conn.execute(
                """INSERT INTO payout_records(
                    worker, worker_wallet, bot_name, mentor, floor_ton, hold_percent,
                    amount_ton, status, tx_hash, raw_output, created_at
                ) VALUES(?,?,?,?,?,?,?,'approved',NULL,?,?)""",
                (
                    request["worker"], request["wallet"], bot_name, mentor,
                    request["floor_ton"], request["hold_percent"], request["amount_ton"],
                    "Prepared for confirmation in wallet", _now(),
                ),
            )
            payout_record_id = int(cur.lastrowid)
            cur = conn.execute(
                """UPDATE payout_requests SET status='approved', reviewed_by=?,
                   payout_record_id=?, updated_at=?
                   WHERE id=? AND status='ready_for_approval'""",
                (admin_id, payout_record_id, _now(), request_id),
            )
            if cur.rowcount != 1:
                return None
            return conn.execute("SELECT * FROM payout_requests WHERE id=?", (request_id,)).fetchone()

    async def reject_payout_request(self, request_id: int, admin_id: int) -> sqlite3.Row | None:
        async with self._lock:
            return await asyncio.to_thread(self._reject_payout_request_sync, request_id, admin_id)

    def _reject_payout_request_sync(self, request_id: int, admin_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE payout_requests SET status='rejected', reviewed_by=?, updated_at=?
                   WHERE id=? AND status='ready_for_approval'""",
                (admin_id, _now(), request_id),
            )
            if cur.rowcount != 1:
                return None
            return conn.execute("SELECT * FROM payout_requests WHERE id=?", (request_id,)).fetchone()

    async def recent_worker_payouts(self, worker_keys: list[str], limit: int = 10) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._recent_worker_payouts_sync, worker_keys, limit)

    def _recent_worker_payouts_sync(self, worker_keys: list[str], limit: int) -> list[sqlite3.Row]:
        normalized = [_normalize_worker_key(key) for key in worker_keys if key]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            return conn.execute(
                f"SELECT * FROM payout_records WHERE worker IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*normalized, max(1, min(int(limit), 50))),
            ).fetchall()

    async def count_users(self) -> int:
        return await asyncio.to_thread(self._scalar_sync, "SELECT COUNT(*) FROM users", ())

    async def portal_admin_stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._portal_admin_stats_sync)

    def _portal_admin_stats_sync(self) -> dict[str, int]:
        with self._connect() as conn:
            def count(sql: str, params: tuple[Any, ...] = ()) -> int:
                row = conn.execute(sql, params).fetchone()
                return int(row[0]) if row else 0
            return {
                "users": count("SELECT COUNT(*) FROM users"),
                "applications": count("SELECT COUNT(*) FROM worker_profiles"),
                "approved": count("SELECT COUNT(*) FROM worker_profiles WHERE status='approved'"),
                "pending": count("SELECT COUNT(*) FROM worker_profiles WHERE status IN ('pending','draft')"),
                "payouts": count("SELECT COUNT(*) FROM payout_records"),
                "sent": count("SELECT COUNT(*) FROM payout_records WHERE status IN ('sent','dry_run')"),
                "failed": count("SELECT COUNT(*) FROM payout_records WHERE status='failed'"),
                "payout_requests": count("SELECT COUNT(*) FROM payout_requests"),
                "payout_ready": count("SELECT COUNT(*) FROM payout_requests WHERE status='ready_for_approval'"),
            }

    async def list_worker_profiles(self, limit: int = 15) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._list_worker_profiles_sync, limit)

    def _list_worker_profiles_sync(self, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """SELECT p.*, u.username, u.full_name
                   FROM worker_profiles p JOIN users u ON u.user_id=p.user_id
                   ORDER BY p.updated_at DESC LIMIT ?""",
                (max(1, min(int(limit), 50)),),
            ).fetchall()

    async def recent_payouts(self, limit: int = 15) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._recent_payouts_sync, limit)

    def _recent_payouts_sync(self, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM payout_records ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()

    async def is_seen(self, listing_id: str) -> bool:
        value = await asyncio.to_thread(self._scalar_sync, "SELECT COUNT(*) FROM listings WHERE id=?", (listing_id,))
        return bool(value)

    def _scalar_sync(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row else 0

    async def save_listing(self, listing: Listing) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_listing_sync, listing)

    def _save_listing_sync(self, listing: Listing) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO listings(
                    id, collection_name, model_name, backdrop_name, symbol_name,
                    gift_number, price_ton, image_url, animation_url, listing_url,
                    listed_at, seller_id, is_on_sale, raw_json, first_seen_at, last_seen_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    price_ton=excluded.price_ton,
                    is_on_sale=excluded.is_on_sale,
                    last_seen_at=excluded.last_seen_at,
                    raw_json=excluded.raw_json
                """,
                (
                    listing.id, listing.collection, listing.model, listing.backdrop,
                    listing.symbol, listing.number, listing.price_ton, listing.image_url,
                    listing.animation_url, listing.url, listing.listed_at.isoformat(),
                    listing.seller_id, int(listing.is_on_sale),
                    json.dumps(listing.raw, ensure_ascii=False, default=str), now, now,
                ),
            )

    async def get_listing(self, listing_id: str) -> Listing | None:
        return await asyncio.to_thread(self._get_listing_sync, listing_id)

    def _get_listing_sync(self, listing_id: str) -> Listing | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return None
        return Listing(
            id=row["id"], collection=row["collection_name"], model=row["model_name"],
            backdrop=row["backdrop_name"], symbol=row["symbol_name"],
            number=row["gift_number"], price_ton=row["price_ton"], image_url=row["image_url"],
            animation_url=row["animation_url"], url=row["listing_url"],
            listed_at=datetime.fromisoformat(row["listed_at"]), seller_id=row["seller_id"],
            is_on_sale=bool(row["is_on_sale"]), raw=json.loads(row["raw_json"] or "{}"),
        )

    async def record_notification(
        self, user_id: int, listing_id: str, message_id: int | None,
        score: int, confidence: int, net_profit_ton: float, roi_percent: float,
    ) -> bool:
        return await asyncio.to_thread(
            self._record_notification_sync, user_id, listing_id, message_id,
            score, confidence, net_profit_ton, roi_percent,
        )

    def _record_notification_sync(self, *args) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO notifications(
                        user_id, listing_id, telegram_message_id, score, confidence,
                        net_profit_ton, roi_percent, sent_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (*args, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    async def notifications_last_hour(self, user_id: int) -> int:
        return await asyncio.to_thread(self._notifications_last_hour_sync, user_id)

    def _notifications_last_hour_sync(self, user_id: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id=? AND sent_at>=?",
                (user_id, cutoff),
            ).fetchone()
            return int(row[0])

    async def recent_notifications(self, user_id: int, limit: int = 5) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._recent_notifications_sync, user_id, limit)

    def _recent_notifications_sync(self, user_id: int, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """SELECT n.*, l.collection_name, l.model_name, l.backdrop_name,
                          l.price_ton, l.listing_url
                   FROM notifications n JOIN listings l ON l.id=n.listing_id
                   WHERE n.user_id=? ORDER BY n.sent_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()

    async def toggle_favorite(self, user_id: int, listing_id: str) -> bool:
        return await asyncio.to_thread(self._toggle_favorite_sync, user_id, listing_id)

    def _toggle_favorite_sync(self, user_id: int, listing_id: str) -> bool:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM favorites WHERE user_id=? AND listing_id=?",
                (user_id, listing_id),
            ).fetchone()
            if exists:
                conn.execute("DELETE FROM favorites WHERE user_id=? AND listing_id=?", (user_id, listing_id))
                return False
            conn.execute(
                "INSERT INTO favorites(user_id, listing_id, created_at) VALUES(?,?,?)",
                (user_id, listing_id, _now()),
            )
            return True

    async def list_favorites(self, user_id: int, limit: int = 10) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._list_favorites_sync, user_id, limit)

    def _list_favorites_sync(self, user_id: int, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """SELECT f.created_at, l.* FROM favorites f
                   JOIN listings l ON l.id=f.listing_id
                   WHERE f.user_id=? ORDER BY f.created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()

    async def save_feedback(self, user_id: int, listing_id: str, value: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_feedback_sync, user_id, listing_id, value)

    def _save_feedback_sync(self, user_id: int, listing_id: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO feedback(user_id, listing_id, value, created_at)
                   VALUES(?,?,?,?) ON CONFLICT(user_id, listing_id)
                   DO UPDATE SET value=excluded.value, created_at=excluded.created_at""",
                (user_id, listing_id, value, _now()),
            )


    async def user_stats(self, user_id: int) -> dict[str, float | int]:
        return await asyncio.to_thread(self._user_stats_sync, user_id)

    def _user_stats_sync(self, user_id: int) -> dict[str, float | int]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS signals,
                          COALESCE(AVG(net_profit_ton), 0) AS avg_profit,
                          COALESCE(MAX(net_profit_ton), 0) AS best_profit,
                          COALESCE(AVG(score), 0) AS avg_score
                   FROM notifications WHERE user_id=?""",
                (user_id,),
            ).fetchone()
            fav = conn.execute("SELECT COUNT(*) FROM favorites WHERE user_id=?", (user_id,)).fetchone()[0]
            good = conn.execute("SELECT COUNT(*) FROM feedback WHERE user_id=? AND value='good'", (user_id,)).fetchone()[0]
            bad = conn.execute("SELECT COUNT(*) FROM feedback WHERE user_id=? AND value='bad'", (user_id,)).fetchone()[0]
        return {
            "signals": int(row["signals"]),
            "avg_profit": float(row["avg_profit"]),
            "best_profit": float(row["best_profit"]),
            "avg_score": float(row["avg_score"]),
            "favorites": int(fav),
            "good": int(good),
            "bad": int(bad),
        }

    async def save_snapshot(self, scope_key: str, floor: float | None, quick_sale: float | None, confidence: int, sample_count: int) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_snapshot_sync, scope_key, floor, quick_sale, confidence, sample_count
            )

    def _save_snapshot_sync(self, scope_key: str, floor: float | None, quick_sale: float | None, confidence: int, sample_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO market_snapshots(scope_key, cleaned_floor, quick_sale, confidence, sample_count, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (scope_key, floor, quick_sale, confidence, sample_count, _now()),
            )

    async def get_previous_snapshot(self, scope_key: str) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._get_previous_snapshot_sync, scope_key)

    def _get_previous_snapshot_sync(self, scope_key: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                """SELECT * FROM market_snapshots WHERE scope_key=?
                   ORDER BY created_at DESC LIMIT 1""",
                (scope_key,),
            ).fetchone()
