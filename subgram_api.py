import aiohttp
import logging

# ⚠️ ЗАМЕНИТЕ НА СВОЙ РЕАЛЬНЫЙ КЛЮЧ
API_KEY = "6a60c30063d0f76f57a07c0d77a452c5284f727f09d04a84c606a14d5a843496"
URL = "https://api.subgram.org/get-sponsors"

async def get_subgram_sponsors(user_id: int, chat_id: int, **kwargs) -> dict | None:
    """Универсальная функция для запроса спонсоров через SubGram API."""
    headers = {"Auth": API_KEY}
    payload = {"user_id": user_id, "chat_id": chat_id}
    payload.update(kwargs)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(URL, headers=headers, json=payload, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"SubGram API вернул статус {response.status}: {await response.text()}")
                    return None
        except Exception as e:
            logging.error(f"Ошибка запроса к SubGram API: {e}")
            return None
