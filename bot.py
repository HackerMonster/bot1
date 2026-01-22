import asyncio
import logging
import aiohttp

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAGJynndMV16Z_Rr0YgbnTd6nWahzkw221g"
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

CHANNEL_URL = "https://t.me/script_f"

# ===============================================

logging.basicConfig(level=logging.INFO)

router = Router()


# ---------- SubGram API ----------
async def get_subgram_sponsors(user_id: int, chat_id: int) -> dict | None:
    headers = {
        "Auth": SUBGRAM_API_KEY
    }

    payload = {
        "user_id": user_id,
        "chat_id": chat_id
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SUBGRAM_URL,
                headers=headers,
                json=payload,
                timeout=10
            ) as response:
                return await response.json()
    except Exception as e:
        logging.error(f"SubGram API error: {e}")
        return None


# ---------- /start ----------
@router.message(CommandStart())
async def start_handler(message: types.Message):
    response = await get_subgram_sponsors(
        user_id=message.from_user.id,
        chat_id=message.chat.id
    )

    # ❌ Если не подписан — SubGram сам отправит сообщение
    if response:
        status = response.get("status")

        if status == "warning":
            return

        if status == "error":
            logging.warning(
                f"SubGram error: {response.get('message')}"
            )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 Наш канал",
                    url=CHANNEL_URL
                )
            ]
        ]
    )

    text = (
        "<b>👋 Приветствуем {nick}</b>\n\n"
        "<b>Добро пожаловать в Secret Link — место, где ты можешь быстро и безопасно "
        "получить свой скрипт для Roblox.</b>\n\n"
        "<b>🔹 Что тебя ждёт:</b>\n"
        "<b>• ⚡️ Только лучшие скрипты — без вирусов, рекламы и переходников</b>\n"
        "<b>• 🛡 Проверены вручную — гарантированная безопасность</b>\n"
        "<b>• 🔁 Постоянные обновления — всё актуально и стабильно работает</b>\n\n"
        "<b>❗️ Важно:</b>\n"
        "<b>Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку "
        "«Получить скрипт 🚀»</b>\n\n"
        "<b>Для сотрудничества:</b> @SecretLinkAds"
    )

    await message.answer(
        text.format(nick=message.from_user.full_name),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ---------- callback от SubGram ----------
@router.callback_query(F.data == "subgram-op")
async def subgram_callback(callback: types.CallbackQuery):
    await callback.answer("⏳ Проверяем подписку...")

    response = await get_subgram_sponsors(
        user_id=callback.from_user.id,
        chat_id=callback.message.chat.id
    )

    if response:
        status = response.get("status")

        if status == "warning":
            return

        if status == "error":
            logging.warning(
                f"SubGram error: {response.get('message')}"
            )

    await callback.message.answer(
        "✅ Подписка подтверждена!\nДоступ открыт 🔓"
    )


# ---------- Запуск ----------
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
