from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain import AnalysisResult, UserFilters


def ui_button(
    text: str,
    callback_data: str,
    *,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Build buttons with the visual style used by the reference payout bot."""
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        style=style,
    )


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [ui_button("💎 Получить выплату", "worker:payout", style="success")],
        [
            ui_button("👤 Профиль", "worker:profile", style="primary"),
            ui_button("📜 История", "worker:history", style="primary"),
        ],
        [
            ui_button("⚙️ Статус", "worker:status", style="danger"),
            ui_button("💬 Помощь", "worker:help", style="primary"),
        ],
        [ui_button("🧑‍💼 Наставники", "worker:mentors", style="primary")],
    ]
    if is_admin:
        rows.append([ui_button("🛠 Админ-панель", "admin:home", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payout_form_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ui_button("👤 Мой профиль", "worker:profile", style="primary")],
        [ui_button("⚙️ Проверить статус", "worker:status", style="primary")],
        [ui_button("❌ Отмена", "menu:home", style="danger")],
    ])


def payout_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ui_button("✅ Подтвердить", "worker:payout_confirm", style="success")],
        [ui_button("❌ Отмена", "menu:home", style="danger")],
    ])


def worker_cabinet_keyboard(has_wallet: bool, approved: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Изменить кошелёк" if has_wallet else "💳 Привязать кошелёк",
        callback_data="worker:bind_wallet",
    )
    if not approved:
        builder.button(text="📝 Подать заявку", callback_data="worker:apply")
    builder.button(text="🔄 Обновить статус", callback_data="worker:cabinet")
    builder.button(text="◀️ Главное меню", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def cancel_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖️ Отмена", callback_data="worker:cancel")
    ]])


def admin_cancel_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:cancel_input")
    ]])


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ui_button("💎 Получить выплату", "worker:payout", style="success")],
        [ui_button("⬅️ Назад в меню", "menu:home", style="danger")],
    ])


def filters_keyboard(filters: UserFilters) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔴 Выключить" if filters.monitoring_enabled else "🟢 Включить",
        callback_data="filter:toggle",
    )
    builder.button(text="🔄 Режим поиска", callback_data="filter:mode")
    builder.button(text="💰 Макс. цена", callback_data="filter:max_price")
    builder.button(text="✅ Мин. плюс", callback_data="filter:min_profit")
    builder.button(text="📈 Мин. ROI", callback_data="filter:min_roi")
    builder.button(text="🎯 Мин. оценка", callback_data="filter:min_score")
    builder.button(text="🧠 Уверенность", callback_data="filter:min_confidence")
    builder.button(text="↩️ Сбросить", callback_data="filter:reset")
    builder.button(text="◀️ Главное меню", callback_data="menu:home")
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def listing_keyboard(result: AnalysisResult) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if result.listing.url:
        builder.button(text="🛒 Открыть на MRKT", url=result.listing.url)
    builder.button(text="🔄 Обновить анализ", callback_data=f"listing:refresh:{result.listing.id}")
    builder.button(text="⭐ В избранное", callback_data=f"listing:favorite:{result.listing.id}")
    builder.button(text="✅ Полезный", callback_data=f"listing:good:{result.listing.id}")
    builder.button(text="❌ Плохой", callback_data=f"listing:bad:{result.listing.id}")
    builder.adjust(1, 2, 2)
    return builder.as_markup()


def admin_keyboard(monitor_running: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            ui_button("📥 Заявки", "admin:applications", style="primary"),
            ui_button("👥 Воркеры", "admin:workers", style="primary"),
        ],
        [
            ui_button("💸 Выплаты", "admin:payouts", style="success"),
            ui_button("📜 История", "admin:payout_history", style="primary"),
        ],
        [
            ui_button("📣 Группа выплат", "admin:choose_profit_group", style="primary"),
            ui_button("💳 Кошелёк выплат", "admin:set_payout_wallet", style="primary"),
        ],
        [
            ui_button("🧑‍💼 Наставники", "admin:mentors", style="primary"),
            ui_button("⚙️ Настройки", "admin:payout_settings", style="primary"),
        ],
        [ui_button("🧪 Проверка сервиса", "admin:service_check", style="primary")],
        [ui_button("⬅️ Назад в меню", "menu:home", style="danger")],
    ])


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Админ-панель", callback_data="admin:home")
    ]])


def payout_request_list_keyboard(rows) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        icon = "✅" if str(row["status"]) == "ready_for_approval" else "⏳"
        builder.button(
            text=f"{icon} Заявка #{row['id']}",
            callback_data=f"admin:payout_request:{row['id']}",
        )
    builder.button(text="◀️ Админ-панель", callback_data="admin:home")
    builder.adjust(1)
    return builder.as_markup()


def payout_request_review_keyboard(request_id: int, ready: bool) -> InlineKeyboardMarkup:
    rows = []
    if ready:
        rows.append([
            ui_button("✅ Одобрить", f"admin:payout_approve:{request_id}", style="success"),
            ui_button("❌ Отклонить", f"admin:payout_reject:{request_id}", style="danger"),
        ])
    rows.append([ui_button("◀️ К заявкам", "admin:applications", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_transfer_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Открыть кошелёк и подтвердить", url=url)],
        [ui_button("◀️ К заявкам", "admin:applications", style="primary")],
    ])


def admin_payout_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📣 Выбрать группу", callback_data="admin:choose_profit_group")
    builder.button(text="💳 Указать кошелёк", callback_data="admin:set_payout_wallet")
    builder.button(text="🔄 Обновить", callback_data="admin:payout_settings")
    builder.button(text="◀️ Админ-панель", callback_data="admin:home")
    builder.adjust(1)
    return builder.as_markup()


def group_picker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="📣 Выбрать группу выплат",
                request_chat=KeyboardButtonRequestChat(
                    request_id=7001,
                    chat_is_channel=False,
                    bot_is_member=True,
                    request_title=True,
                    request_username=True,
                ),
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери группу, где уже добавлен бот",
    )
