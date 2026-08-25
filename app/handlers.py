from __future__ import annotations

import html
import logging
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db import Database
from app.domain import SearchMode
from app.keyboards import (
    back_menu,
    cancel_input_keyboard,
    filters_keyboard,
    main_menu,
    payout_confirm_keyboard,
    payout_form_keyboard,
    worker_cabinet_keyboard,
)
from app.services.monitor import MarketMonitor
from app.services.nft_verifier import NftVerifier, gift_slug
from app.services.payouts import PayoutService
from app.texts import (
    filters_text,
    fmt_ton,
    payout_confirm_text,
    payout_intro_text,
    payout_invalid_form_text,
    welcome_text,
)

logger = logging.getLogger(__name__)
router = Router(name="user")


class WorkerFlow(StatesGroup):
    waiting_wallet = State()
    waiting_payout_details = State()


_NFT_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:(?:https?://)?(?:www\.)?)t\.me/nft/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_TON_ADDRESS_RE = re.compile(r"\b(?:EQ|UQ)[A-Za-z0-9_-]{6,126}\b")


def _parse_payout_form(text: str) -> tuple[str, str] | None:
    link = _NFT_LINK_RE.search(text)
    wallet = _TON_ADDRESS_RE.search(text)
    if not link or not wallet:
        return None
    nft_link = link.group(0)
    if not nft_link.casefold().startswith(("http://", "https://")):
        nft_link = "https://" + nft_link
    return nft_link, wallet.group(0)


def _allowed(user_id: int, settings: Settings) -> bool:
    return (
        user_id in settings.admin_ids
        or not settings.allowed_user_ids
        or user_id in settings.allowed_user_ids
    )


async def _deny(event: Message | CallbackQuery) -> None:
    text = "⛔ Бот закрытый. Ваш Telegram ID не добавлен владельцем."
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)


async def _edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if not callback.message:
        return
    try:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=reply_markup)
        else:
            await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        logger.debug("Could not edit message, sending a new one", exc_info=True)
        await callback.message.answer(text, reply_markup=reply_markup)


@router.message(CommandStart())
async def start(message: Message, db: Database, settings: Settings) -> None:
    if not message.from_user:
        return
    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    mnemonic_ready = Path(settings.ton_mnemonic_file or "data/ton_mnemonic.txt").exists()
    group_ready = bool(await db.get_app_setting("profit_group_chat_id") or settings.profit_group_chat_id)
    ready = mnemonic_ready and group_ready
    status_line = (
        "\n\n✅ Сервис выплат настроен и готов к работе."
        if ready else
        "\n\n⚠️ Некоторые компоненты сервиса ещё настраиваются."
    )
    await message.answer(
        welcome_text(message.from_user.first_name) + status_line,
        reply_markup=main_menu(message.from_user.id in settings.admin_ids),
    )


@router.callback_query(F.data == "worker:payout")
async def worker_payout_form(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkerFlow.waiting_payout_details)
    await callback.answer()
    await _edit(callback, payout_intro_text(), payout_form_keyboard())


@router.message(WorkerFlow.waiting_payout_details)
async def worker_payout_details(message: Message, state: FSMContext) -> None:
    parsed = _parse_payout_form(message.text or "")
    if parsed is None:
        await message.answer(payout_invalid_form_text(), reply_markup=payout_form_keyboard())
        return
    nft_link, wallet = parsed
    if not PayoutService.validate_wallet(wallet):
        await message.answer(payout_invalid_form_text(), reply_markup=payout_form_keyboard())
        return
    await state.update_data(payout_nft_link=nft_link, payout_wallet=wallet)
    await message.answer(payout_confirm_text(nft_link, wallet), reply_markup=payout_confirm_keyboard())


@router.callback_query(F.data == "worker:payout_confirm")
async def worker_payout_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    payout_service: PayoutService,
    nft_verifier: NftVerifier,
) -> None:
    data = await state.get_data()
    nft_link = str(data.get("payout_nft_link") or "")
    wallet = str(data.get("payout_wallet") or "")
    if not nft_link or not PayoutService.validate_wallet(wallet):
        await callback.answer("Сначала отправьте данные заявки", show_alert=True)
        await state.set_state(WorkerFlow.waiting_payout_details)
        return
    await db.upsert_user(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
    keys = _worker_keys_from_callback(callback)
    for key in keys:
        await db.set_worker_wallet(key, wallet)
    request, created = await db.create_payout_request(
        callback.from_user.id, keys[0], nft_link, gift_slug(nft_link), wallet
    )
    if not created:
        await state.clear()
        await callback.answer("Этот NFT уже есть в заявках", show_alert=True)
        await _edit(
            callback,
            "ℹ️ <b>NFT уже обработан</b>\n\n"
            f"Заявка: <code>#{request['id']}</code>\n"
            f"Статус: <code>{html.escape(str(request['status']))}</code>",
            back_menu(),
        )
        return
    await db.set_worker_profile(
        callback.from_user.id,
        keys[0],
        "pending",
        {"wallet_bound": True, "wallet_format": True, "nft_link_received": True},
    )
    verification = await nft_verifier.verify(nft_link)
    amount_ton = payout_service.calculate_amount(verification.floor_ton) if verification.floor_ton else None
    ready = bool(
        verification.ok
        and amount_ton is not None
        and settings.payout_min_ton <= amount_ton <= settings.payout_max_ton
    )
    details = verification.details
    if verification.ok and not ready:
        details = "calculated_amount_outside_limits"
    request = await db.set_payout_request_verification(
        int(request["id"]),
        status="ready_for_approval" if ready else "verification_failed",
        floor_ton=verification.floor_ton,
        hold_percent=settings.payout_hold_percent if verification.floor_ton else None,
        amount_ton=amount_ton,
        price_source=verification.source,
        check_details=details,
    )
    await db.set_worker_profile(
        callback.from_user.id,
        keys[0],
        "approved" if ready else "pending",
        {
            "wallet_bound": True,
            "wallet_format": True,
            "nft_link_received": True,
            "nft_owned_by_account": verification.owned_by_account,
            "floor_received": verification.floor_ton is not None,
        },
    )
    await state.clear()
    if not ready:
        await callback.answer("Проверка завершена")
        await _edit(
            callback,
            "⚠️ <b>Проверка заявки завершена</b>\n\n"
            f"Заявка: <code>#{request['id'] if request else '—'}</code>\n"
            f"Результат: <code>{html.escape(details)}</code>\n\n"
            "Перевод не подготовлен.",
            back_menu(),
        )
        return

    assert request is not None and amount_ton is not None and verification.floor_ton is not None
    admin_text = (
        "💎 <b>ЗАЯВКА ГОТОВА К ПРОВЕРКЕ</b>\n\n"
        f"ID: <code>#{request['id']}</code>\n"
        f"Воркер: <b>{html.escape(keys[0])}</b>\n"
        f"NFT: {html.escape(nft_link)}\n"
        f"Floor: <b>{verification.floor_ton:g} TON</b>\n"
        f"Вычет: <b>{settings.payout_hold_percent:g}%</b>\n"
        f"К выплате: <b>{amount_ton:g} TON</b>\n"
        f"Источник: <code>{html.escape(verification.source or '—')}</code>"
    )
    from app.keyboards import payout_request_review_keyboard
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=payout_request_review_keyboard(int(request["id"]), True),
            )
        except Exception:
            logger.warning("Could not notify admin %s about payout request", admin_id, exc_info=True)
    await callback.answer("NFT и floor проверены")
    await _edit(
        callback,
        "✅ <b>Заявка проверена</b>\n\n"
        f"ID: <code>#{request['id']}</code>\n"
        f"NFT: {html.escape(nft_link)}\n"
        f"Кошелёк: <code>{html.escape(wallet)}</code>\n\n"
        f"Floor: <b>{verification.floor_ton:g} TON</b>\n"
        f"К выплате после вычета: <b>{amount_ton:g} TON</b>\n\n"
        "Ожидается подтверждение администратора.",
        back_menu(),
    )


@router.callback_query(F.data == "worker:profile")
async def worker_profile(callback: CallbackQuery, db: Database) -> None:
    await worker_cabinet(callback, db)


@router.callback_query(F.data == "worker:history")
async def worker_history(callback: CallbackQuery, db: Database) -> None:
    keys = _worker_keys_from_callback(callback)
    rows = await db.recent_worker_payouts(keys, 10)
    lines = ["📜 <b>ИСТОРИЯ ВЫПЛАТ</b>\n"]
    if not rows:
        lines.append("Выплат пока нет.")
    for row in rows:
        lines.append(
            f"#{row['id']} · <b>{float(row['amount_ton']):g} TON</b> · "
            f"<code>{html.escape(str(row['status']))}</code>"
        )
    await callback.answer()
    await _edit(callback, "\n".join(lines), back_menu())


@router.callback_query(F.data == "worker:status")
async def worker_service_status(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    keys = _worker_keys_from_callback(callback)
    wallet, _ = await _own_wallet(db, keys)
    profile = await db.get_worker_profile(callback.from_user.id)
    request = await db.latest_user_payout_request(callback.from_user.id)
    session_ready = any(Path(settings.mrkt_session_dir).glob(settings.mrkt_session_name + "*.session"))
    payout_ready = Path(settings.ton_mnemonic_file or "data/ton_mnemonic.txt").exists()
    group_ready = bool(await db.get_app_setting("profit_group_chat_id") or settings.profit_group_chat_id)
    await callback.answer()
    await _edit(
        callback,
        "⚙️ <b>СТАТУС СЕРВИСА</b>\n\n"
        f"Аккаунт проверки: {'✅' if session_ready else '❌'}\n"
        f"Кошелёк выплат: {'✅' if payout_ready else '❌'}\n"
        f"Группа уведомлений: {'✅' if group_ready else '❌'}\n"
        f"Ваш TON-адрес: {'✅' if wallet else '❌'}\n"
        f"Профиль: <b>{_status_label(str(profile['status']) if profile else None)}</b>\n"
        f"Последняя заявка: <code>{html.escape(str(request['status']) if request else 'нет')}</code>",
        back_menu(),
    )


@router.callback_query(F.data == "worker:help")
async def worker_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit(
        callback,
        "💬 <b>ПОМОЩЬ</b>\n\n"
        "1. Откройте «Получить выплату».\n"
        "2. Отправьте ссылку NFT и публичный TON-адрес одним сообщением.\n"
        "3. Проверьте результат в разделе «Статус».\n"
        "4. Выплата появится в разделе «История».",
        back_menu(),
    )


@router.callback_query(F.data == "worker:mentors")
async def worker_mentors(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit(
        callback,
        "🧑‍🏫 <b>НАСТАВНИКИ</b>\n\n"
        "Информация о наставнике отображается в профиле, если он привязан к аккаунту.",
        back_menu(),
    )


@router.message(Command("id"))
async def show_id(message: Message) -> None:
    if message.from_user:
        await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")

def _worker_keys_from_user(message: Message) -> list[str]:
    if not message.from_user:
        return []
    keys: list[str] = []
    if message.from_user.username:
        keys.append("@" + message.from_user.username.casefold())
    keys.append(f"id:{message.from_user.id}")
    return keys


def _worker_keys_from_callback(callback: CallbackQuery) -> list[str]:
    keys: list[str] = []
    if callback.from_user.username:
        keys.append("@" + callback.from_user.username.casefold())
    keys.append(f"id:{callback.from_user.id}")
    return keys


async def _own_wallet(db: Database, keys: list[str]) -> tuple[str | None, str]:
    for key in keys:
        wallet = await db.get_worker_wallet(key)
        if wallet:
            return wallet, key
    return None, keys[0]


def _status_label(status: str | None) -> str:
    return {
        "approved": "✅ одобрена автоматически",
        "rejected": "❌ отклонена",
        "pending": "⏳ проверяется",
        "draft": "📝 не завершена",
    }.get(status or "", "📝 не подана")


@router.callback_query(F.data == "worker:cabinet")
async def worker_cabinet(callback: CallbackQuery, db: Database) -> None:
    keys = _worker_keys_from_callback(callback)
    wallet, worker_key = await _own_wallet(db, keys)
    profile = await db.get_worker_profile(callback.from_user.id)
    status = str(profile["status"]) if profile else None
    masked = "не привязан"
    if wallet:
        masked = wallet[:6] + "…" + wallet[-6:]
    await callback.answer()
    await _edit(
        callback,
        "👤 <b>МОЙ КАБИНЕТ</b>\n\n"
        f"Профиль: <code>{html.escape(worker_key)}</code>\n"
        f"Кошелёк: <code>{html.escape(masked)}</code>\n"
        f"Заявка: <b>{_status_label(status)}</b>\n\n"
        "Привязка и проверка выполняются внутри бота.",
        worker_cabinet_keyboard(bool(wallet), status == "approved"),
    )


@router.callback_query(F.data == "worker:bind_wallet")
async def worker_bind_wallet_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkerFlow.waiting_wallet)
    await callback.answer()
    await _edit(
        callback,
        "💳 <b>ПРИВЯЗКА КОШЕЛЬКА</b>\n\n"
        "Отправь адрес TON-кошелька одним сообщением.\n"
        "Seed-фразу сюда не отправляй — нужен только публичный адрес.",
        cancel_input_keyboard(),
    )


@router.callback_query(F.data == "worker:cancel")
async def worker_cancel(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await worker_cabinet(callback, db)


@router.message(WorkerFlow.waiting_wallet)
async def worker_wallet_input(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    wallet = (message.text or "").strip()
    if not PayoutService.validate_wallet(wallet):
        await message.answer(
            "Адрес не прошёл проверку формата. Отправь публичный адрес вида <code>UQ…</code>.",
            reply_markup=cancel_input_keyboard(),
        )
        return
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    keys = _worker_keys_from_user(message)
    for key in keys:
        await db.set_worker_wallet(key, wallet)
    await state.clear()
    await message.answer(
        "✅ Кошелёк сохранён. Теперь нажми «Подать заявку» — проверки пройдут автоматически.",
        reply_markup=worker_cabinet_keyboard(True, False),
    )


@router.callback_query(F.data == "worker:apply")
async def worker_apply(callback: CallbackQuery, db: Database) -> None:
    await db.upsert_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )
    keys = _worker_keys_from_callback(callback)
    wallet, worker_key = await _own_wallet(db, keys)
    checks = {
        "telegram_id": callback.from_user.id > 0,
        "profile_name": bool(callback.from_user.full_name.strip()),
        "wallet_bound": bool(wallet),
        "wallet_format": bool(wallet and PayoutService.validate_wallet(wallet)),
    }
    approved = all(checks.values())
    await db.set_worker_profile(
        callback.from_user.id,
        worker_key,
        "approved" if approved else "draft",
        checks,
    )
    await callback.answer("Проверка завершена")
    if not approved:
        await _edit(
            callback,
            "📝 <b>ЗАЯВКА ПРОВЕРЕНА</b>\n\n"
            "Для завершения осталось привязать публичный TON-кошелёк.",
            worker_cabinet_keyboard(bool(wallet), False),
        )
        return
    await _edit(
        callback,
        "✅ <b>ЗАЯВКА ОДОБРЕНА</b>\n\n"
        "Профиль и адрес кошелька проверены автоматически. Кабинет активирован.",
        worker_cabinet_keyboard(True, True),
    )


@router.message(Command("bindwallet"))
async def bind_own_worker_wallet(message: Message, db: Database, settings: Settings) -> None:
    if not message.from_user:
        return
    wallet = (message.text or "").partition(" ")[2].strip()
    if not wallet:
        await message.answer("Формат: <code>/bindwallet UQ...</code>")
        return
    if not PayoutService.validate_wallet(wallet):
        await message.answer("Проверь формат кошелька: <code>/bindwallet UQ...</code>")
        return
    await db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    keys = _worker_keys_from_user(message)
    for key in keys:
        await db.set_worker_wallet(key, wallet)
    public_name = keys[0] if keys else f"id:{message.from_user.id}"
    await message.answer(
        "✅ Кошелёк привязан к твоему профилю.\n"
        f"Воркер: <b>{html.escape(public_name)}</b>\n"
        f"Кошелёк: <code>{html.escape(wallet)}</code>\n\n"
        "Админ может указывать тебя в выплате как "
        f"<code>{html.escape(public_name)}</code>"
    )


@router.message(Command("mywallet"))
async def show_own_worker_wallet(message: Message, db: Database, settings: Settings) -> None:
    if not message.from_user:
        return
    keys = _worker_keys_from_user(message)
    wallet = None
    matched_key = None
    for key in keys:
        wallet = await db.get_worker_wallet(key)
        if wallet:
            matched_key = key
            break
    await message.answer(
        "💳 <b>Твой кошелёк</b>\n\n"
        f"Воркер: <code>{html.escape(matched_key or (keys[0] if keys else str(message.from_user.id)))}</code>\n"
        f"Кошелёк: <code>{html.escape(wallet or 'не привязан')}</code>\n\n"
        "Привязать/обновить: <code>/bindwallet UQ...</code>"
    )



@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not callback.from_user:
        return
    await state.clear()
    await callback.answer()
    await _edit(
        callback,
        welcome_text(callback.from_user.first_name),
        main_menu(callback.from_user.id in settings.admin_ids),
    )


@router.callback_query(F.data == "menu:filters")
async def menu_filters(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    await callback.answer()
    filters = await db.get_filters(callback.from_user.id)
    await _edit(callback, filters_text(filters), filters_keyboard(filters))


def _next(current, choices):
    try:
        idx = choices.index(current)
    except ValueError:
        idx = -1
    return choices[(idx + 1) % len(choices)]


@router.callback_query(F.data.startswith("filter:"))
async def change_filter(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    action = callback.data.split(":", 1)[1]
    f = await db.get_filters(callback.from_user.id)
    changes = {}
    if action == "toggle":
        changes["monitoring_enabled"] = not f.monitoring_enabled
    elif action == "mode":
        changes["search_mode"] = _next(f.search_mode, [SearchMode.ALL, SearchMode.MONOCHROME, SearchMode.BLACK])
    elif action == "max_price":
        changes["max_price_ton"] = _next(f.max_price_ton, [10.0, 25.0, 50.0, 100.0, 0.0])
    elif action == "min_profit":
        changes["min_profit_ton"] = _next(f.min_profit_ton, [1.0, 2.0, 3.0, 5.0, 10.0])
    elif action == "min_roi":
        changes["min_roi_percent"] = _next(f.min_roi_percent, [5.0, 10.0, 15.0, 20.0, 30.0])
    elif action == "min_score":
        changes["min_score"] = _next(f.min_score, [60, 70, 80, 90])
    elif action == "min_confidence":
        changes["min_confidence"] = _next(f.min_confidence, [40, 55, 70, 85])
    elif action == "reset":
        changes = {
            "monitoring_enabled": True,
            "max_price_ton": 50.0,
            "min_profit_ton": 2.0,
            "min_roi_percent": 10.0,
            "min_score": 70,
            "min_confidence": 55,
            "search_mode": SearchMode.ALL,
        }
    updated = await db.update_filters(callback.from_user.id, **changes)
    await callback.answer("Сохранено")
    await _edit(callback, filters_text(updated), filters_keyboard(updated))


@router.callback_query(F.data == "menu:signals")
async def recent_signals(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    rows = await db.recent_notifications(callback.from_user.id, 8)
    if not rows:
        text = "🔥 <b>ВЫГОДНЫЕ ЛОТЫ</b>\n\nСигналов пока нет. Бот продолжает мониторинг."
    else:
        lines = ["🔥 <b>ПОСЛЕДНИЕ СИГНАЛЫ</b>\n"]
        for row in rows:
            lines.append(
                f"• <b>{row['collection_name']}</b> — {row['model_name']} / {row['backdrop_name']}\n"
                f"  Цена {fmt_ton(row['price_ton'])} · плюс {fmt_ton(row['net_profit_ton'])} · {row['score']}/100"
            )
        text = "\n".join(lines)
    await callback.answer()
    await _edit(callback, text, back_menu())


@router.callback_query(F.data == "menu:favorites")
async def favorites(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    rows = await db.list_favorites(callback.from_user.id)
    lines = ["⭐ <b>ИЗБРАННОЕ</b>\n"]
    if not rows:
        lines.append("Здесь пока пусто.")
    for row in rows:
        lines.append(
            f"• <b>{row['collection_name']}</b> — {row['model_name']} / {row['backdrop_name']}\n"
            f"  {fmt_ton(row['price_ton'])}"
        )
    await callback.answer()
    await _edit(callback, "\n".join(lines), back_menu())


@router.callback_query(F.data == "menu:stats")
async def stats(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    data = await db.user_stats(callback.from_user.id)
    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"Получено сигналов: <b>{data['signals']}</b>\n"
        f"Средний прогноз плюса: <b>{fmt_ton(data['avg_profit'])}</b>\n"
        f"Лучший прогноз: <b>{fmt_ton(data['best_profit'])}</b>\n"
        f"Средняя оценка: <b>{data['avg_score']:.1f}/100</b>\n"
        f"В избранном: <b>{data['favorites']}</b>\n"
        f"Полезных отметок: <b>{data['good']}</b>\n"
        f"Плохих отметок: <b>{data['bad']}</b>"
    )
    await callback.answer()
    await _edit(callback, text, back_menu())


@router.callback_query(F.data == "menu:notifications")
async def notifications(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    f = await db.get_filters(callback.from_user.id)
    text = (
        "🔔 <b>УВЕДОМЛЕНИЯ</b>\n\n"
        f"Статус: {'🟢 включены' if f.monitoring_enabled else '🔴 выключены'}\n"
        f"Лимит: <b>{f.max_alerts_per_hour} в час</b>\n\n"
        "В первой версии бот присылает только сигналы, прошедшие твои фильтры."
    )
    await callback.answer()
    await _edit(callback, text, back_menu())


@router.callback_query(F.data == "menu:help")
async def help_menu(callback: CallbackQuery, settings: Settings) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    text = (
        "ℹ️ <b>КАК РАБОТАЕТ БОТ</b>\n\n"
        "1. Получает новые объявления MRKT.\n"
        "2. Сравнивает модель + фон, фон, модель и общий флор.\n"
        "3. Исключает одиночные сливы и завышенные объявления.\n"
        "4. Распознаёт массовый слив как возможное падение рынка.\n"
        "5. Учитывает Black, Onyx Black и monochrome.\n"
        "6. Вычитает комиссию и динамический запас.\n"
        "7. Перепроверяет лот перед финальным сигналом.\n\n"
        "Команда /id показывает твой Telegram ID."
    )
    await callback.answer()
    await _edit(callback, text, back_menu())


@router.callback_query(F.data.startswith("listing:"))
async def listing_action(
    callback: CallbackQuery,
    db: Database,
    monitor: MarketMonitor,
    settings: Settings,
) -> None:
    if not _allowed(callback.from_user.id, settings):
        await _deny(callback)
        return
    _, action, listing_id = callback.data.split(":", 2)
    if action == "favorite":
        active = await db.toggle_favorite(callback.from_user.id, listing_id)
        await callback.answer("Добавлено в избранное" if active else "Удалено из избранного")
        return
    if action in {"good", "bad"}:
        await db.save_feedback(callback.from_user.id, listing_id, action)
        await callback.answer("Спасибо, оценка сохранена")
        return
    if action == "refresh":
        await callback.answer("Обновляю рынок…")
        result = await monitor.refresh_listing(listing_id)
        if not result:
            if callback.message:
                try:
                    if callback.message.photo:
                        await callback.message.edit_caption("❌ Лот уже продан или снят с продажи.")
                    else:
                        await callback.message.edit_text("❌ Лот уже продан или снят с продажи.")
                except Exception:
                    pass
            return
        from app.keyboards import listing_keyboard
        from app.texts import analysis_text
        if callback.message:
            if callback.message.photo:
                await callback.message.edit_caption(analysis_text(result), reply_markup=listing_keyboard(result))
            else:
                await callback.message.edit_text(analysis_text(result), reply_markup=listing_keyboard(result))
