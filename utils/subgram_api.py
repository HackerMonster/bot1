# --- Файл: utils/subgram_api.py ---
import aiohttp
import logging

API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
URL = "https://api.subgram.org/get-sponsors"

async def get_subgram_sponsors(user_id: int, chat_id: int, **kwargs) -> dict | None:
    """Универсальная функция для запроса спонсоров."""
    headers = { "Auth": API_KEY }
    payload = { "user_id": user_id, "chat_id": chat_id }
    payload.update(kwargs)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(URL, headers=headers, json=payload, timeout=10) as response:
                return await response.json()
        except Exception as e:
            logging.error(f"Ошибка запроса к SubGram API: {e}")
            return None

# --- Файл: handlers/subgram.py ---
import logging
from aiogram import F, types, Router
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from utils.subgram_api import get_subgram_sponsors

router = Router()

async def process_subgram_check(user: types.User, chat_id: int, api_kwargs: dict = None):
    """Основная функция для обработки всех статусов от SubGram."""
    if api_kwargs is None:
        api_kwargs = {}

    # Передаем больше данных о пользователе, если не передавали токен.
    user_data = {
        "first_name": user.first_name,
        "username": user.username,
        "language_code": user.language_code,
        "is_premium": bool(user.is_premium)
    }
    user_data.update(api_kwargs)
    
    response = await get_subgram_sponsors(user_id=user.id, chat_id=chat_id, **user_data)

    if response:
        status = response.get("status")
        if status and status == "warning":
            builder = InlineKeyboardBuilder()
            text = "Пожалуйста, выполните задания ниже:"
            sponsors = response.get("additional", {}).get("sponsors", [])
            for sponsor in sponsors:
                # Показываем только тех, на кого надо подписаться
                if sponsor.get("available_now") and sponsor.get("status") == "unsubscribed":
                    builder.button(text=sponsor.get("button_text", "Подписаться"), url=sponsor.get("link"))
            builder.button(text="✅ Я выполнил", callback_data="subgram-op")
            builder.adjust(1)
            
            # Для универсальности примера вернем данные для отправки:
            return False, text, builder.as_markup()
                
        else: # error, ok или неизвестный статус -> пускаем
            return True, None, None
    else: # ошибка запроса -> пускаем
        return True, None, None


@router.message(CommandStart())
async def handle_start_links(message: types.Message):
    is_allowed, text, reply_markup = await process_subgram_check(message.from_user, message.chat.id)

    if not is_allowed:
        await message.answer(text, reply_markup=reply_markup)
        return

    # Даем доступ
    await message.answer("✅ Доступ предоставлен!")
    # ... ваш основной код ...
    

@router.callback_query(F.data == 'subgram-op')
async def handle_callback_links(callback: types.CallbackQuery):
    # Удаляем старое сообщение
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        logging.info("Не удалось удалить сообщение (возможно, уже удалено)")

    await callback.answer("⏳ Проверяем подписки...")

    is_allowed, text, reply_markup = await process_subgram_check(callback.from_user, callback.message.chat.id)

    if not is_allowed:
        await callback.message.answer(text, reply_markup=reply_markup)
        return

    # Даем доступ
    await callback.message.answer("✅ Доступ предоставлен!")
    # ... ваш основной код ...
