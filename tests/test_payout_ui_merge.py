from app.handlers import _parse_payout_form
from app.keyboards import main_menu, payout_confirm_keyboard


def test_reference_main_menu_layout_is_preserved() -> None:
    markup = main_menu(is_admin=False)
    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["💎 Получить выплату"],
        ["👤 Профиль", "📜 История"],
        ["⚙️ Статус", "💬 Помощь"],
        ["🧑‍💼 Наставники"],
    ]
    assert markup.inline_keyboard[0][0].style == "success"


def test_payout_form_parses_reference_format() -> None:
    parsed = _parse_payout_form(
        "Ссылка для NFT: https://t.me/nft/DurovsCap-12345\n"
        "TON-кошелёк: UQabcdefgh12345678"
    )
    assert parsed == (
        "https://t.me/nft/DurovsCap-12345",
        "UQabcdefgh12345678",
    )


def test_payout_form_accepts_telegram_link_without_scheme() -> None:
    parsed = _parse_payout_form(
        "Ссылка для NFT:\n"
        "t.me/nft/LoveCandle-22454\n"
        "TON-кошелёк:\n"
        "UQAGAv3QkiCr906krRd87v3zb9aGPUuOjdwPn7SMwkNCCXSI"
    )
    assert parsed == (
        "https://t.me/nft/LoveCandle-22454",
        "UQAGAv3QkiCr906krRd87v3zb9aGPUuOjdwPn7SMwkNCCXSI",
    )


def test_confirmation_has_confirm_and_cancel() -> None:
    markup = payout_confirm_keyboard()
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["worker:payout_confirm", "menu:home"]
