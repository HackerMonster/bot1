# utils/flyer_api.py
import aiohttp
import logging

# Замените на ваш ключ от @FlyerServiceBot
FLYER_BOT_KEY = "FL-fCmzVf-QyBeLi-xYlScV-gkcahf"

FLYER_URL = "https://api.flyerservice.io/check-subscription"

async def check_flyer_subscription(user_id: int, chat_id: int, language_code: str = "en") -> dict | None:
    payload = {
        "key": FLYER_BOT_KEY,
        "user_id": user_id,
        "language_code": language_code,
        "message": {
            "rows": 2,
            "text": "ℹ️ Подпишитесь на спонсоров",
            "button_bot": "🤖 Бот",
            "button_channel": "📢 Канал",
            "button_boost": "🔥 Boost",
            "button_url": "🌐 Сайт",
            "button_fp": "👤 Профиль"
        }
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(FLYER_URL, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logging.error(f"Flyer error {resp.status}: {await resp.text()}")
                    return {"error": "HTTP error"}
        except Exception as e:
            logging.exception("Flyer API exception")
            return None
