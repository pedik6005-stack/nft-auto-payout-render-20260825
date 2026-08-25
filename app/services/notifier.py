from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.domain import AnalysisResult
from app.keyboards import listing_keyboard
from app.texts import analysis_text

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_analysis(self, user_id: int, result: AnalysisResult, preliminary: bool = False) -> Message:
        text = analysis_text(result, preliminary=preliminary)
        markup = listing_keyboard(result)
        if result.listing.image_url:
            try:
                return await self.bot.send_photo(
                    chat_id=user_id,
                    photo=result.listing.image_url,
                    caption=text,
                    reply_markup=markup,
                )
            except TelegramBadRequest:
                logger.warning("Could not send listing image, falling back to text", exc_info=True)
        return await self.bot.send_message(chat_id=user_id, text=text, reply_markup=markup)

    async def update_analysis(self, message: Message, result: AnalysisResult) -> None:
        text = analysis_text(result, preliminary=False)
        markup = listing_keyboard(result)
        try:
            if message.photo:
                await message.edit_caption(caption=text, reply_markup=markup)
            else:
                await message.edit_text(text=text, reply_markup=markup)
        except TelegramBadRequest as exc:
            # Message may be unchanged or too old; send a fresh card instead.
            if "message is not modified" not in str(exc).lower():
                logger.warning("Could not update preliminary alert: %s", exc)
