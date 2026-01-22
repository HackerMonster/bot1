import asyncio
import logging
import aiohttp

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAGJynndMV16Z_Rr0YgbnTd6nWahzkw221g"
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

CHANNEL_URL = "https://t.me/script_f"
ADMIN_ID = 5870949629

# ===============================================

logging.basicConfig(level=logging.INFO)
router = Dispatcher()
storage = MemoryStorage()

# Хранилище пользователей
USERS = set()


# ================== FSM ==================

class BroadcastState(StatesGroup):
    content = State()
    buttons = State()
    confirm = State()


# ================== SubGram ==================

async def get_subgram_sponsors(user_id: int, chat_id: int) -> dict | None:
    headers = {"Auth": SUBGRAM_API_KEY}
    payload = {"user_id": user_id, "chat_id": chat_id}

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


# ================== Приветствие ==================

async def send_welcome(message: types.Message):
    USERS.add(message.from_user.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)]
        ]
    )

    text = (
        "<b>👋 Приветствуем {nick}</b>\n\n"
        "<b>Добро пожаловать в Secret Link — место, где ты можешь быстро и безопасно "
        "получить свой скрипт для Roblox.</b>\n\n"
        "🔹 <b>Что тебя ждёт:</b>\n"
        "• ⚡️ <b>Только лучшие скрипты — без вирусов, рекламы и переходников</b>\n"
        "• 🛡 <b>Проверены вручную — гарантированная безопасность</b>\n"
        "• 🔁 <b>Постоянные обновления — всё актуально и стабильно работает</b>\n\n"
        "❗️ <b>Важно:</b>\n"
        "Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»\n\n"
        "<b>Для сотрудничества:</b> @SecretLinkAds"
    )

    await message.answer(
        text.format(nick=message.from_user.full_name),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ================== /start ==================

@router.message(CommandStart())
async def start_handler(message: types.Message):
    response = await get_subgram_sponsors(message.from_user.id, message.chat.id)

    if response and response.get("status") == "warning":
        return

    await send_welcome(message)


# ================== callback SubGram ==================

@router.callback_query(F.data == "subgram-op")
async def subgram_callback(callback: types.CallbackQuery):
    response = await get_subgram_sponsors(callback.from_user.id, callback.message.chat.id)

    if response and response.get("status") == "warning":
        return

    await send_welcome(callback.message)


# ================== АДМИН ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
        ]
    )

    await message.answer("👑 <b>Админ-панель</b>", reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if callback.data == "admin_stats":
        total_users = len(USERS)
        subscribed = 0
        for user_id in USERS:
            try:
                resp = asyncio.run(get_subgram_sponsors(user_id, callback.message.chat.id))
                if resp and resp.get("status") != "warning":
                    subscribed += 1
            except Exception:
                pass

        await callback.message.answer(
            f"📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: {total_users}\n"
            f"Подписавшиеся: {subscribed}\n"
            f"Не подписаны: {total_users - subscribed}",
            parse_mode="HTML"
        )

    elif callback.data == "admin_broadcast":
        await callback.message.answer(
            "📢 <b>Рассылка</b>\n\n"
            "Отправь любой контент:\n"
            "текст / фото / видео / документ / GIF / стикер\n\n"
            "Или /cancel для отмены",
            parse_mode="HTML"
        )
        await state.set_state(BroadcastState.content)

    await callback.answer()


# ================== РАССЫЛКА ==================

@router.message(BroadcastState.content)
async def get_broadcast_content(message: types.Message, state: FSMContext):
    await state.update_data(content=message)

    await message.answer(
        "⛓ <b>КНОПКИ: URL</b>\n\n"
        "Отправьте кнопки в формате:\n"
        "Кнопка 1 - http://link.com\n"
        "Кнопка 2 - http://link.com\n\n"
        "Для нескольких кнопок в ряд используйте | (до 8 кнопок в ряду, 15 рядов)\n"
        "Или напишите <b>нет</b>",
        parse_mode="HTML"
    )

    await state.set_state(BroadcastState.buttons)


def parse_buttons(text: str) -> InlineKeyboardMarkup | None:
    keyboard = []
    rows = text.split("\n")

    for row in rows[:15]:
        buttons = []
        parts = row.split("|")

        for part in parts[:8]:
            if "-" not in part:
                continue
            name, url = part.split("-", 1)
            buttons.append(InlineKeyboardButton(text=name.strip(), url=url.strip()))

        if buttons:
            keyboard.append(buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


@router.message(BroadcastState.buttons)
async def get_buttons(message: types.Message, state: FSMContext):
    if message.text.lower() != "нет":
        keyboard = parse_buttons(message.text)
        await state.update_data(keyboard=keyboard)
    else:
        await state.update_data(keyboard=None)

    await message.answer(
        "✅ Готово. Отправить рассылку?\nНапиши <b>да</b> или <b>нет</b>",
        parse_mode="HTML"
    )
    await state.set_state(BroadcastState.confirm)


@router.message(BroadcastState.confirm)
async def confirm_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    if message.text.lower() != "да":
        await message.answer("❌ Рассылка отменена")
        await state.clear()
        return

    data = await state.get_data()
    content: types.Message = data["content"]
    keyboard = data.get("keyboard")

    sent = 0
    for user_id in USERS:
        try:
            await content.copy_to(chat_id=user_id, reply_markup=keyboard)
            sent += 1
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена\nОтправлено: {sent}")
    await state.clear()


@router.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено")


# ================== RUN ==================

async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(bot=bot, storage=storage)
    dp.include_router(router)
    await dp.start_polling()


if __name__ == "__main__":
    asyncio.run(main())
