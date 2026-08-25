from __future__ import annotations

from html import escape

from app.domain import AnalysisResult, UserFilters


def welcome_text(first_name: str | None = None) -> str:
    name = escape(first_name or "рад вас видеть")
    return (
        f"💎 <b>Добро пожаловать, {name}!</b>\n\n"
        "Данный бот предназначен для управления вашими операциями.\n\n"
        "<b>Основные возможности:</b>\n"
        "• 💰 Выплаты — отправка заявки на получение средств\n"
        "• 👤 Профиль — данные кошелька и статистика выплат\n"
        "• 🧑‍💼 Наставники — информация о вашем кураторе\n"
        "• 📜 История — просмотр обработанных выплат\n"
        "• ⚙️ Статус — проверка готовности сервиса\n\n"
        "📊 <b>Условия расчёта:</b> выплата по настроенному проценту от floor NFT"
    )


def payout_intro_text() -> str:
    return (
        "💎 <b>Получение выплаты</b>\n\n"
        "Для оформления заявки отправьте данные одним сообщением в следующем формате:\n\n"
        "🖼 Ссылка для NFT: <code>https://t.me/nft/DurovsCap-12345</code> 👛\n\n"
        "TON-кошелёк: <code>UQ...</code>\n\n"
        "После отправки бот проверит данные и отобразит кнопку подтверждения."
    )


def payout_invalid_form_text() -> str:
    return (
        "Данные заявки не распознаны.\n\n"
        "Отправьте одним сообщением:\n\n"
        "Ссылка для NFT: <code>https://t.me/nft/...</code>\n"
        "TON-кошелёк: <code>UQ...</code>"
    )


def payout_confirm_text(nft_link: str, wallet_address: str) -> str:
    return (
        "💎 <b>Проверьте заявку</b>\n\n"
        f"Ссылка для NFT: {escape(nft_link)}\n"
        f"TON-кошелёк: <code>{escape(wallet_address)}</code>\n\n"
        "Нажмите «Подтвердить», чтобы передать заявку на автоматическую проверку."
    )


def fmt_ton(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{text} TON"


def filters_text(filters: UserFilters) -> str:
    mode_names = {
        "all": "Все выгодные сочетания",
        "monochrome": "Только monochrome",
        "black": "Black / Onyx Black",
    }
    return (
        "🎯 <b>МОИ ФИЛЬТРЫ</b>\n\n"
        f"Мониторинг: {'🟢 включён' if filters.monitoring_enabled else '🔴 выключен'}\n"
        f"Режим: <b>{mode_names[filters.search_mode.value]}</b>\n"
        f"Максимальная цена: <b>{fmt_ton(filters.max_price_ton) if filters.max_price_ton else 'без ограничения'}</b>\n"
        f"Минимальный чистый плюс: <b>{fmt_ton(filters.min_profit_ton)}</b>\n"
        f"Минимальная доходность: <b>{filters.min_roi_percent:.0f}%</b>\n"
        f"Минимальная оценка: <b>{filters.min_score}/100</b>\n"
        f"Минимальная уверенность: <b>{filters.min_confidence}/100</b>\n\n"
        "Нажимай кнопки ниже — настройки сохраняются сразу."
    )


def analysis_text(result: AnalysisResult, preliminary: bool = False) -> str:
    item = result.listing
    reasons = "\n".join(f"• {escape(reason)}" for reason in result.reasons[:6]) or "• Данных пока недостаточно"
    warnings = ""
    if result.warnings:
        warnings = "\n\n⚠️ <b>Риски:</b>\n" + "\n".join(f"• {escape(w)}" for w in result.warnings)
    status = "⚡ <b>ПРЕДВАРИТЕЛЬНЫЙ СИГНАЛ</b>" if preliminary else f"<b>{result.label}</b>"
    exact_floor = result.exact.cleaned_floor or result.backdrop.cleaned_floor or result.model.cleaned_floor
    return (
        f"{status}\n\n"
        f"🎁 <b>{escape(item.collection)}</b>"
        f"{f' #{item.number}' if item.number is not None else ''}\n"
        f"🎨 Модель: <b>{escape(item.model)}</b>\n"
        f"🌑 Фон: <b>{escape(item.backdrop)}</b>\n"
        f"🔣 Символ: <b>{escape(item.symbol)}</b>\n\n"
        f"💰 Цена покупки: <b>{fmt_ton(item.price_ton)}</b>\n"
        f"📊 Очищенный флор сочетания: <b>{fmt_ton(exact_floor)}</b>\n"
        f"⚡ Быстрый слив: <b>{fmt_ton(result.fast_sale_ton)}</b>\n"
        f"📈 Обычная продажа: <b>{fmt_ton(result.normal_sale_ton)}</b>\n"
        f"🚀 Оптимистичная продажа: <b>{fmt_ton(result.optimistic_sale_ton)}</b>\n\n"
        f"💸 Комиссия: <b>{fmt_ton(result.fees_ton)}</b>\n"
        f"🛡 Запас на погрешность: <b>{fmt_ton(result.reserve_ton)}</b>\n"
        f"✅ Возможный чистый плюс: <b>{fmt_ton(result.net_profit_ton)}</b>\n"
        f"📈 Доходность: <b>{result.roi_percent:.1f}%</b>\n\n"
        f"🎯 Выгода: <b>{result.score}/100</b>\n"
        f"🧠 Уверенность: <b>{result.confidence}/100</b>\n"
        f"⚠️ Риск: <b>{escape(result.risk)}</b>\n"
        f"📚 Аналогов: <b>{result.exact.cleaned_count or result.backdrop.cleaned_count}</b>\n"
        f"🧱 Глубина флора: <b>{result.exact.depth_count or result.backdrop.depth_count}</b>\n\n"
        f"<b>Почему найден:</b>\n{reasons}{warnings}\n\n"
        f"🕒 Анализ обновлён: <b>{result.calculated_at.strftime('%H:%M:%S UTC')}</b>\n\n"
        "<i>Бот оценивает рынок, но не гарантирует прибыль. Перед покупкой проверь лот вручную.</i>"
    )
