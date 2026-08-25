from __future__ import annotations

import html
import os
import shutil
from datetime import datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.config import Settings
from app.db import Database
from app.keyboards import (
    admin_back_keyboard,
    admin_cancel_input_keyboard,
    admin_keyboard,
    admin_payout_keyboard,
    group_picker_keyboard,
    payout_request_list_keyboard,
    payout_request_review_keyboard,
)
from app.services.monitor import MarketMonitor
from app.services.payouts import PayoutService
from app.services.visual import VisualEngine

router = Router(name="admin")


class AdminButtonFlow(StatesGroup):
    waiting_payout_wallet = State()


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


async def _status_text(settings: Settings, db: Database) -> str:
    stats = await db.portal_admin_stats()
    session_ready = any(Path(settings.mrkt_session_dir).glob(settings.mrkt_session_name + "*.session"))
    wallet_ready = bool(os.getenv("TON_MNEMONIC")) or Path(settings.ton_mnemonic_file or "data/ton_mnemonic.txt").exists()
    group_ready = bool(await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id))
    return (
        "🛠 <b>АДМИН-ПАНЕЛЬ ВЫПЛАТ</b>\n\n"
        f"Воркеров: <b>{stats['users']}</b>\n"
        f"Профилей: <b>{stats['applications']}</b> · одобрено: <b>{stats['approved']}</b>\n"
        f"Заявок на выплату: <b>{stats['payout_requests']}</b> · готовы: <b>{stats['payout_ready']}</b>\n"
        f"Выплат: <b>{stats['payouts']}</b> · отправлено: <b>{stats['sent']}</b> · ошибок: <b>{stats['failed']}</b>\n\n"
        f"Аккаунт проверки: {'✅ подключён' if session_ready else '❌ не подключён'}\n"
        f"Кошелёк выплат: {'✅ подключён' if wallet_ready else '❌ не подключён'}\n"
        f"Группа выплат: {'✅ подключена' if group_ready else '❌ не подключена'}\n"
        f"Режим выплат: <code>{html.escape(settings.payout_mode)}</code>\n"
        f"Вычет от флора: <b>{settings.payout_hold_percent:g}%</b>\n\n"
        "Управление доступно кнопками ниже."
    )


@router.message(Command("admin"))
async def admin_home(message: Message, settings: Settings, db: Database) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return
    await message.answer(
        await _status_text(settings, db),
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:home")
async def admin_home_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        await _status_text(settings, db),
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:status")
async def admin_status(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    await admin_home_callback(callback, settings, db)


@router.callback_query(F.data == "admin:toggle_monitor")
async def toggle_monitor(callback: CallbackQuery, settings: Settings, db: Database, monitor: MarketMonitor) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if monitor.status.running:
        await monitor.stop()
        await callback.answer("Монитор остановлен")
    else:
        await monitor.start()
        await callback.answer("Монитор запущен")
    users = await db.count_users()
    await callback.message.edit_text(
        _status_text(monitor, monitor.provider.name, users),
        reply_markup=admin_keyboard(monitor.status.running),
    )


@router.callback_query(F.data == "admin:reload_rules")
async def reload_rules(callback: CallbackQuery, settings: Settings, visual: VisualEngine) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    visual.reload()
    await callback.answer("Правила перезагружены")


@router.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    count = await db.count_users()
    await callback.answer(f"Всего пользователей: {count}", show_alert=True)


def _profile_line(row) -> str:
    username = "@" + str(row["username"]) if row["username"] else str(row["worker_key"])
    status = {
        "approved": "✅",
        "pending": "⏳",
        "draft": "📝",
        "rejected": "❌",
    }.get(str(row["status"]), "•")
    return f"{status} <b>{html.escape(username)}</b> · <code>{html.escape(str(row['status']))}</code>"


@router.callback_query(F.data == "admin:applications")
async def admin_applications(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = await db.list_payout_requests(15)
    pending = [row for row in rows if str(row["status"]) in {"checking", "ready_for_approval"}]
    lines = ["📥 <b>ЗАЯВКИ НА ВЫПЛАТУ</b>\n"]
    if not pending:
        lines.append("Заявок, требующих действия, нет.")
    else:
        for row in pending:
            amount = f"{float(row['amount_ton']):g} TON" if row["amount_ton"] is not None else "проверяется"
            lines.append(
                f"#{row['id']} · <b>{html.escape(str(row['worker']))}</b> · "
                f"{amount} · <code>{html.escape(str(row['status']))}</code>"
            )
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=payout_request_list_keyboard(pending),
    )


def _request_text(row) -> str:
    floor = f"{float(row['floor_ton']):g} TON" if row["floor_ton"] is not None else "—"
    amount = f"{float(row['amount_ton']):g} TON" if row["amount_ton"] is not None else "—"
    return (
        "💎 <b>ЗАЯВКА НА ВЫПЛАТУ</b>\n\n"
        f"ID: <code>#{row['id']}</code>\n"
        f"Воркер: <b>{html.escape(str(row['worker']))}</b>\n"
        f"NFT: {html.escape(str(row['nft_link']))}\n"
        f"Кошелёк: <code>{html.escape(str(row['wallet']))}</code>\n"
        f"Floor: <b>{floor}</b>\n"
        f"К выплате: <b>{amount}</b>\n"
        f"Источник: <code>{html.escape(str(row['price_source'] or '—'))}</code>\n"
        f"Проверка: <code>{html.escape(str(row['check_details'] or '—'))}</code>\n"
        f"Статус: <code>{html.escape(str(row['status']))}</code>"
    )


@router.callback_query(F.data.startswith("admin:payout_request:"))
async def admin_payout_request(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        request_id = int(str(callback.data).rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    row = await db.get_payout_request(request_id)
    if row is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _request_text(row),
        reply_markup=payout_request_review_keyboard(
            request_id, str(row["status"]) == "ready_for_approval"
        ),
    )


@router.callback_query(F.data.startswith("admin:payout_approve:"))
async def admin_payout_approve(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    payout_service: PayoutService,
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        request_id = int(str(callback.data).rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    bot_info = await callback.bot.get_me()
    bot_name = "@" + (bot_info.username or str(bot_info.id))
    mentor = await db.get_app_setting("default_mentor")
    row = await db.approve_payout_request(request_id, callback.from_user.id, bot_name, mentor)
    if row is None:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    await callback.answer("Заявка одобрена, отправляю перевод")
    await callback.message.edit_text(
        _request_text(row) + "\n\n⏳ Заявка одобрена. Бот отправляет TON автоматически...",
        reply_markup=None,
    )

    amount_ton = float(row["amount_ton"] or 0)
    comment = f"payout request {request_id}"
    result = await payout_service.send(str(row["wallet"]), amount_ton, comment)
    final_status = "sent" if result.status in {"sent", "dry_run"} else "failed"
    row = await db.finish_payout_request(
        request_id,
        final_status,
        result.tx_hash,
        result.raw_output,
    ) or row

    if final_status == "sent":
        tx_line = f"\nTX: <code>{html.escape(result.tx_hash)}</code>" if result.tx_hash else ""
        await callback.message.edit_text(
            _request_text(row)
            + f"\n\n✅ Одобрено и автоматически отправлено: <b>{amount_ton:g} TON</b>{tx_line}",
            reply_markup=payout_request_review_keyboard(request_id, False),
        )
        try:
            await callback.bot.send_message(
                int(row["user_id"]),
                f"✅ Заявка <code>#{request_id}</code> одобрена. "
                f"Выплата <b>{amount_ton:g} TON</b> отправлена автоматически.{tx_line}",
            )
        except Exception:
            pass
        chat_id_raw = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
        if chat_id_raw:
            try:
                await callback.bot.send_message(
                    int(chat_id_raw),
                    _format_profit_message(str(row["worker"]), bot_name, f"{amount_ton:g} TON", mentor),
                )
            except Exception:
                pass
    else:
        await callback.message.edit_text(
            _request_text(row)
            + "\n\n❌ Заявка одобрена, но автоотправка не прошла. "
            f"Причина: <code>{html.escape(result.raw_output[:900] or result.status)}</code>",
            reply_markup=payout_request_review_keyboard(request_id, False),
        )
        try:
            await callback.bot.send_message(
                int(row["user_id"]),
                f"⚠️ Заявка <code>#{request_id}</code> одобрена, но выплата пока не отправлена.",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("admin:payout_reject:"))
async def admin_payout_reject(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        request_id = int(str(callback.data).rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    row = await db.reject_payout_request(request_id, callback.from_user.id)
    if row is None:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    await callback.answer("Заявка отклонена")
    await callback.message.edit_text(
        _request_text(row), reply_markup=payout_request_review_keyboard(request_id, False)
    )
    try:
        await callback.bot.send_message(
            int(row["user_id"]), f"❌ Заявка <code>#{request_id}</code> отклонена администратором."
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin:workers")
async def admin_workers(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = await db.list_worker_profiles(20)
    lines = ["👥 <b>ВОРКЕРЫ</b>\n"]
    if not rows:
        lines.append("Профилей пока нет.")
    else:
        lines.extend(_profile_line(row) for row in rows)
    await callback.answer()
    await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_keyboard())


def _payout_line(row) -> str:
    status_icon = "✅" if str(row["status"]) in {"sent", "dry_run"} else "❌" if str(row["status"]) == "failed" else "⏳"
    return (
        f"{status_icon} #{row['id']} · <b>{float(row['amount_ton']):g} TON</b> · "
        f"{html.escape(str(row['worker']))} · <code>{html.escape(str(row['status']))}</code>"
    )


@router.callback_query(F.data == "admin:payouts")
async def admin_payouts(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    stats = await db.portal_admin_stats()
    rows = await db.recent_payouts(8)
    lines = [
        "💸 <b>ВЫПЛАТЫ</b>\n",
        f"Всего: <b>{stats['payouts']}</b> · отправлено: <b>{stats['sent']}</b> · ошибок: <b>{stats['failed']}</b>\n",
    ]
    lines.extend((_payout_line(row) for row in rows),)
    if not rows:
        lines.append("Выплат пока нет.")
    await callback.answer()
    await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_keyboard())


@router.callback_query(F.data == "admin:payout_history")
async def admin_payout_history(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    rows = await db.recent_payouts(20)
    lines = ["📜 <b>ИСТОРИЯ ВЫПЛАТ</b>\n"]
    if not rows:
        lines.append("История пока пустая.")
    else:
        lines.extend(_payout_line(row) for row in rows)
    await callback.answer()
    await callback.message.edit_text("\n".join(lines), reply_markup=admin_back_keyboard())


@router.callback_query(F.data == "admin:mentors")
async def admin_mentors(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mentor = await db.get_app_setting("default_mentor")
    await callback.answer()
    await callback.message.edit_text(
        "🧑‍🏫 <b>НАСТАВНИКИ</b>\n\n"
        f"Наставник по умолчанию: <b>{html.escape(mentor or 'не назначен')}</b>\n\n"
        "Наставник отображается в сообщении о выплате только когда он указан в профиле или заявке.",
        reply_markup=admin_back_keyboard(),
    )


@router.callback_query(F.data == "admin:service_check")
async def admin_service_check(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    session_ready = any(Path(settings.mrkt_session_dir).glob(settings.mrkt_session_name + "*.session"))
    wallet_ready = bool(os.getenv("TON_MNEMONIC")) or Path(settings.ton_mnemonic_file or "data/ton_mnemonic.txt").exists()
    group_id = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    public_wallet = await _effective_setting(db, "payout_wallet", settings.payout_wallet)
    await callback.answer("Проверка завершена")
    await callback.message.edit_text(
        "🧪 <b>ПРОВЕРКА СЕРВИСА</b>\n\n"
        f"Аккаунт проверки NFT: {'✅' if session_ready else '❌'}\n"
        f"Seed-файл кошелька: {'✅' if wallet_ready else '❌'}\n"
        f"Публичный кошелёк: {'✅' if public_wallet else '❌'}\n"
        f"Группа выплат: {'✅' if group_id else '❌'}\n"
        f"Режим: <code>{html.escape(settings.payout_mode)}</code>\n"
        f"Сеть: <code>{html.escape(settings.ton_network)}</code>",
        reply_markup=admin_back_keyboard(),
    )


@router.callback_query(F.data == "admin:payout_settings")
async def admin_payout_settings(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    group_id = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    group_title = await db.get_app_setting("profit_group_title") or "не выбрана"
    wallet = await _effective_setting(db, "payout_wallet", settings.payout_wallet)
    masked = "не указан"
    if wallet:
        masked = wallet[:6] + "…" + wallet[-6:]
    await callback.answer()
    await callback.message.edit_text(
        "💸 <b>ВЫПЛАТЫ И ГРУППА</b>\n\n"
        f"Группа: <b>{html.escape(group_title)}</b>\n"
        f"ID группы: <code>{html.escape(group_id or '—')}</code>\n"
        f"Кошелёк: <code>{html.escape(masked)}</code>\n"
        f"Режим: <code>{html.escape(settings.payout_mode)}</code>\n"
        f"Вычет: <b>{settings.payout_hold_percent:g}%</b>",
        reply_markup=admin_payout_keyboard(),
    )


@router.callback_query(F.data == "admin:choose_profit_group")
async def admin_choose_profit_group(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "Нажми кнопку ниже и выбери группу, в которую уже добавлен бот.",
        reply_markup=group_picker_keyboard(),
    )


@router.message(F.chat_shared)
async def admin_profit_group_shared(message: Message, settings: Settings, db: Database) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return
    shared = message.chat_shared
    if not shared or shared.request_id != 7001:
        return
    await db.set_app_setting("profit_group_chat_id", str(shared.chat_id))
    await db.set_app_setting("profit_group_title", shared.title or shared.username or str(shared.chat_id))
    await message.answer(
        "✅ Группа выплат привязана кнопкой.\n"
        f"ID: <code>{shared.chat_id}</code>",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(F.data == "admin:set_payout_wallet")
async def admin_set_payout_wallet_button(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminButtonFlow.waiting_payout_wallet)
    await callback.answer()
    await callback.message.edit_text(
        "💳 Отправь публичный адрес выплачивающего кошелька одним сообщением.",
        reply_markup=admin_cancel_input_keyboard(),
    )


@router.callback_query(F.data == "admin:cancel_input")
async def admin_cancel_input(callback: CallbackQuery, settings: Settings, state: FSMContext, db: Database) -> None:
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await admin_payout_settings(callback, settings, db)


@router.message(AdminButtonFlow.waiting_payout_wallet)
async def admin_payout_wallet_input(
    message: Message, settings: Settings, state: FSMContext, db: Database
) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return
    wallet = (message.text or "").strip()
    if not PayoutService.validate_wallet(wallet):
        await message.answer("Проверь публичный адрес и отправь его ещё раз.")
        return
    await db.set_app_setting("payout_wallet", wallet)
    await state.clear()
    await message.answer(
        "✅ Публичный кошелёк выплат сохранён.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("addbg"))
async def add_background(message: Message, settings: Settings, visual: VisualEngine) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return
    payload = (message.text or "").partition(" ")[2]
    try:
        name, weight = [part.strip() for part in payload.split("|", 1)]
        visual.add_premium_backdrop(name, int(weight))
    except Exception:
        await message.answer("Формат: <code>/addbg Onyx Black|100</code>")
        return
    await message.answer(f"✅ Фон <b>{name}</b> сохранён с весом {weight}.")


@router.message(Command("addcombo"))
async def add_combo(message: Message, settings: Settings, visual: VisualEngine) -> None:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return
    payload = (message.text or "").partition(" ")[2]
    try:
        parts = [part.strip() for part in payload.split("|")]
        model, backdrop, score, mono = parts[:4]
        note = parts[4] if len(parts) > 4 else ""
        visual.add_combination(
            model, backdrop, int(score),
            mono.casefold() in {"yes", "да", "true", "1"}, note,
        )
    except Exception:
        await message.answer(
            "Формат: <code>/addcombo Black|Onyx Black|100|yes|Сильная комбинация</code>"
        )
        return
    await message.answer(f"✅ Комбинация <b>{model} + {backdrop}</b> сохранена.")



def _payload(message: Message) -> str:
    return (message.text or "").partition(" ")[2].strip()


async def _require_admin_message(message: Message, settings: Settings) -> bool:
    if not message.from_user or not _is_admin(message.from_user.id, settings):
        return False
    return True


async def _effective_setting(db: Database, key: str, fallback: object | None = None) -> str | None:
    value = await db.get_app_setting(key)
    if value not in (None, ""):
        return value
    if fallback not in (None, ""):
        return str(fallback)
    return None


def _format_profit_message(worker: str, bot_name: str, amount: str, mentor: str | None = None) -> str:
    lines = [
        "<b>💎НОВЫЙ ПРОФИТ:</b>",
        "",
        f"<b>😀Воркер: {html.escape(worker)}</b>",
        f"<b>🤖Бот: {html.escape(bot_name)}</b>",
        f"<b>💎Сумма: {html.escape(amount)}</b>",
    ]
    if mentor:
        lines.extend(["", f"<b>🎓Наставник: {html.escape(mentor)}</b>"])
    return "\n".join(lines)


def _parse_profit_payload(payload: str) -> tuple[str, str, str, str | None]:
    if "|" in payload:
        parts = [part.strip() for part in payload.split("|")]
    else:
        parts = payload.split()
    if len(parts) < 3:
        raise ValueError
    amount, worker, bot_name = parts[:3]
    mentor = parts[3] if len(parts) > 3 and parts[3].strip() else None
    return amount, worker, bot_name, mentor


@router.message(Command("bindprofitgroup"))
async def bind_profit_group(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    await db.set_app_setting("profit_group_chat_id", str(message.chat.id))
    title = message.chat.title or getattr(message.chat, "full_name", None) or str(message.chat.id)
    await db.set_app_setting("profit_group_title", title)
    await message.answer(
        "✅ Группа выплат привязана.\n"
        f"Чат: <b>{html.escape(title)}</b>\n"
        f"ID: <code>{message.chat.id}</code>"
    )


@router.message(Command("setprofitgroup"))
async def set_profit_group(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    payload = _payload(message)
    try:
        chat_id = int(payload)
    except ValueError:
        await message.answer("Формат: <code>/setprofitgroup -1001234567890</code>")
        return
    await db.set_app_setting("profit_group_chat_id", str(chat_id))
    await db.set_app_setting("profit_group_title", f"chat {chat_id}")
    await message.answer(f"✅ Группа выплат привязана: <code>{chat_id}</code>")


@router.message(Command("profitgroup"))
async def show_profit_group(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    chat_id = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    title = await db.get_app_setting("profit_group_title") or "—"
    await message.answer(
        "📣 <b>Группа выплат</b>\n\n"
        f"ID: <code>{html.escape(chat_id or 'не привязана')}</code>\n"
        f"Название: <b>{html.escape(title)}</b>\n\n"
        "Чтобы привязать текущую группу: добавь бота в группу и отправь там <code>/bindprofitgroup</code>."
    )


@router.message(Command("setpayoutwallet"))
async def set_payout_wallet(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    wallet = _payload(message)
    if len(wallet) < 8:
        await message.answer("Формат: <code>/setpayoutwallet UQ...</code>")
        return
    await db.set_app_setting("payout_wallet", wallet)
    await message.answer(f"✅ Выплачивающий кошелёк привязан:\n<code>{html.escape(wallet)}</code>")


@router.message(Command("payoutwallet"))
async def show_payout_wallet(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    wallet = await _effective_setting(db, "payout_wallet", settings.payout_wallet)
    await message.answer(
        "💳 <b>Выплачивающий кошелёк</b>\n\n"
        f"<code>{html.escape(wallet or 'не привязан')}</code>\n\n"
        "Привязать: <code>/setpayoutwallet UQ...</code>"
    )


@router.message(Command("profit"))
async def publish_profit(message: Message, settings: Settings, db: Database, bot: Bot) -> None:
    if not await _require_admin_message(message, settings):
        return
    try:
        amount, worker, bot_name, mentor = _parse_profit_payload(_payload(message))
    except ValueError:
        await message.answer(
            "Формат: <code>/profit сумма|воркер|бот|наставник</code>\n"
            "Пример: <code>/profit 12.5|@worker|@bot|@mentor</code>\n"
            "Без наставника: <code>/profit 12.5|@worker|@bot</code>"
        )
        return
    chat_id_raw = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    if not chat_id_raw:
        await message.answer("Сначала привяжи группу выплат командой <code>/bindprofitgroup</code> в нужной группе.")
        return
    text = _format_profit_message(worker, bot_name, amount, mentor)
    sent = await bot.send_message(chat_id=int(chat_id_raw), text=text)
    await message.answer(f"✅ Профит отправлен в группу. Message ID: <code>{sent.message_id}</code>")


@router.message(Command("testprofit"))
async def test_profit(message: Message, settings: Settings, db: Database, bot: Bot) -> None:
    if not await _require_admin_message(message, settings):
        return
    chat_id_raw = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    if not chat_id_raw:
        await message.answer("Сначала привяжи группу выплат командой <code>/bindprofitgroup</code> в нужной группе.")
        return
    text = _format_profit_message("@worker", "@bot", "123 TON", "@mentor")
    sent = await bot.send_message(chat_id=int(chat_id_raw), text=text)
    await message.answer(f"✅ Тестовый профит отправлен. Message ID: <code>{sent.message_id}</code>")


def _parse_worker_wallet_payload(payload: str) -> tuple[str, str]:
    parts = payload.split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError
    worker, wallet = parts[0].strip(), parts[1].strip()
    if not worker or not wallet:
        raise ValueError
    return worker, wallet


@router.message(Command("sessionstatus"))
async def session_status(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    session_dir = Path(settings.mrkt_session_dir)
    session_files = sorted(p.name for p in session_dir.glob(settings.mrkt_session_name + "*") if p.is_file())
    mnemonic_file = Path(settings.ton_mnemonic_file or "data/ton_mnemonic.txt")
    profit_group = await _effective_setting(db, "profit_group_chat_id", settings.profit_group_chat_id)
    payout_wallet = await _effective_setting(db, "payout_wallet", settings.payout_wallet)
    await message.answer(
        "🔐 <b>Статус привязок</b>\n\n"
        f"MRKT-сессия: <b>{'есть' if session_files else 'нет'}</b>\n"
        f"Файлы: <code>{html.escape(', '.join(session_files) or '—')}</code>\n"
        f"TON seed-файл: <b>{'есть' if mnemonic_file.exists() else 'нет'}</b>\n"
        f"TON режим: <code>{html.escape(settings.payout_mode)}</code>\n"
        f"Группа профитов: <code>{html.escape(profit_group or '—')}</code>\n"
        f"Публичный кошелёк выплат: <code>{html.escape(payout_wallet or '—')}</code>\n\n"
        "Локально: <code>python scripts/bind_mrkt_session.py</code> и <code>python scripts/bind_ton_wallet.py</code>"
    )


@router.message(Command("setworkerwallet"))
async def set_worker_wallet(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    try:
        worker, wallet = _parse_worker_wallet_payload(_payload(message))
    except ValueError:
        await message.answer("Формат: <code>/setworkerwallet @worker UQ...</code>")
        return
    if not PayoutService.validate_name(worker) or not PayoutService.validate_wallet(wallet):
        await message.answer("Проверь формат: <code>/setworkerwallet @worker UQ...</code>")
        return
    await db.set_worker_wallet(worker, wallet)
    await message.answer(f"✅ Кошелёк воркера <b>{html.escape(worker)}</b> сохранён:\n<code>{html.escape(wallet)}</code>")


@router.message(Command("workerwallet"))
async def show_worker_wallet(message: Message, settings: Settings, db: Database) -> None:
    if not await _require_admin_message(message, settings):
        return
    worker = _payload(message)
    if not worker:
        await message.answer("Формат: <code>/workerwallet @worker</code>")
        return
    wallet = await db.get_worker_wallet(worker)
    await message.answer(
        f"👤 <b>{html.escape(worker)}</b>\n"
        f"Кошелёк: <code>{html.escape(wallet or 'не привязан')}</code>"
    )


@router.message(Command("autopayout"))
async def auto_payout(
    message: Message,
    settings: Settings,
) -> None:
    if not await _require_admin_message(message, settings):
        return
    await message.answer(
        "💸 Заявки обрабатываются в админ-панели кнопками.",
        reply_markup=admin_keyboard(),
    )


@router.message(Command("resetmrktsession"))
async def reset_mrkt_session(message: Message, settings: Settings) -> None:
    if not await _require_admin_message(message, settings):
        return
    session_dir = Path(settings.mrkt_session_dir)
    pattern = f"{settings.mrkt_session_name}*"
    files = [p for p in session_dir.glob(pattern) if p.is_file()]
    if not files:
        await message.answer("MRKT session-файлы не найдены. При следующем запуске live-режима будет создана новая сессия.")
        return
    backup_dir = session_dir / ("invalid_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for file in files:
        target = backup_dir / file.name
        shutil.move(str(file), str(target))
        moved.append(file.name)
    await message.answer(
        "✅ Старая MRKT-сессия убрана в бэкап.\n"
        f"Папка: <code>{html.escape(str(backup_dir))}</code>\n"
        f"Файлы: <code>{html.escape(', '.join(moved))}</code>\n\n"
        "Перезапусти бота в консоли — Pyrogram попросит заново ввести телефон/код."
    )
