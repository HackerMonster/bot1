# --- Файл: utils/subgram_api.py ---
import aiohttp
import logging

API_KEY = "ВАШ_API_КЛЮЧ_БОТА"
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

# --- Файл: handlers/start.py (пример обработчика) ---
from aiogram import F, types, Router
from utils.subgram_api import get_subgram_sponsors
from aiogram.filters import CommandStart

router = Router()

@router.message(CommandStart())
async def handle_start_command(message: types.Message):
    # Вызываем функцию для проверки подписки
    response = await get_subgram_sponsors(
        user_id=message.from_user.id,
        chat_id=message.chat.id
    )

    if response:
        status = response.get("status")
       
        if status and status == 'warning':
            # Сервис сам отправит сообщение с просьбой подписаться
            return
        if status and status == "error":
            logging.warning(f"Ошибка SubGram API: {response.get('message')}. Предоставляем доступ.")

    await message.answer("✅ Доступ предоставлен!")
    # ... ваш код для выдачи контента ...


@router.callback_query(F.data == 'subgram-op')
async def handle_subgram_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    await callback.answer("⏳ Проверяем подписки...")

    # Отправляем повторный запрос в SubGram API с новыми данными
    response = await get_subgram_sponsors(user_id, chat_id)

    if response:
        status = response.get("status")
       
        if status and status == 'warning':
            return
        if status and status == "error":
            logging.warning(f"Ошибка SubGram API: {response.get('message')}. Предоставляем доступ.")

    await callback.message.answer("✅ Доступ предоставлен!")
    # ... ваш код для выдачи контента ...
