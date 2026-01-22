import asyncio
import logging
import aiohttp

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAGJynndMV16Z_Rr0YgbnTd6nWahzkw221g"  # ⚠️ ВНИМАНИЕ: Это тестовый токен, замените на реальный!
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"  # ⚠️ Храните в безопасном месте!
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

CHANNEL_URL = "https://t.me/script_f"
ADMIN_ID = 5870949629

# ===============================================

logging.basicConfig(level=logging.INFO)
router = Router()

# Хранилище пользователей
USERS = set()


# ================== FSM для рассылки ==================

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
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"SubGram API error: {response.status}")
                    return None
    except asyncio.TimeoutError:
        logging.error("SubGram API timeout")
        return None
    except Exception as e:
        logging.error(f"SubGram API error: {e}")
        return None


# ================== Приветствие ==================

async def send_welcome(target: types.Message | types.CallbackQuery):
    if isinstance(target, types.Message):
        user_id = target.from_user.id
        chat = target
    else:
        user_id = target.from_user.id
        chat = target.message
    
    USERS.add(user_id)

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

    if isinstance(target, types.Message):
        await target.answer(
            text.format(nick=target.from_user.full_name),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await target.message.edit_text(
            text.format(nick=target.from_user.full_name),
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await target.answer()


# ================== /start ==================

@router.message(CommandStart())
async def start_handler(message: types.Message):
    response = await get_subgram_sponsors(message.from_user.id, message.chat.id)

    if response and response.get("status") == "warning":
        # Здесь должна быть логика обработки неподписанных пользователей
        # Например, показать кнопку для подписки
        return

    await send_welcome(message)


@router.callback_query(F.data == "subgram-op")
async def subgram_callback(callback: types.CallbackQuery):
    response = await get_subgram_sponsors(callback.from_user.id, callback.message.chat.id)

    if response and response.get("status") == "warning":
        # Здесь должна быть логика обработки неподписанных пользователей
        return

    await send_welcome(callback)


# ================== АДМИН ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели")
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
                resp = await get_subgram_sponsors(user_id, callback.message.chat.id)
                if resp and resp.get("status") != "warning":
                    subscribed += 1
            except Exception as e:
                logging.error(f"Error checking subscription for {user_id}: {e}")

        await callback.message.edit_text(
            f"📊 <b>Статистика</b>\n\n"
            f"Всего пользователей: {total_users}\n"
            f"Подписавшиеся: {subscribed}\n"
            f"Не подписаны: {total_users - subscribed}",
            parse_mode="HTML"
        )

    elif callback.data == "admin_broadcast":
        await callback.message.edit_text(
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
    # Сохраняем не сам объект message, а его данные
    content_data = {
        'message_id': message.message_id,
        'chat_id': message.chat.id,
        'content_type': message.content_type,
        'text': message.text,
        'caption': message.caption,
        'photo': message.photo[-1].file_id if message.photo else None,
        'video': message.video.file_id if message.video else None,
        'document': message.document.file_id if message.document else None,
        'animation': message.animation.file_id if message.animation else None,
        'sticker': message.sticker.file_id if message.sticker else None,
    }
    
    await state.update_data(content=content_data)

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
    if text.lower() == "нет":
        return None
        
    keyboard = []
    rows = text.strip().split("\n")

    for row in rows[:15]:
        buttons = []
        parts = row.split("|")

        for part in parts[:8]:
            if "-" not in part:
                continue
            name_url = part.split("-", 1)
            if len(name_url) != 2:
                continue
            name, url = name_url
            buttons.append(InlineKeyboardButton(text=name.strip(), url=url.strip()))

        if buttons:
            keyboard.append(buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


@router.message(BroadcastState.buttons)
async def get_buttons(message: types.Message, state: FSMContext):
    keyboard = parse_buttons(message.text)
    await state.update_data(buttons=keyboard)

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
    content_data = data["content"]
    keyboard = data.get("buttons")

    sent = 0
    failed = 0
    
    for user_id in USERS:
        try:
            # Отправляем контент в зависимости от типа
            if content_data['content_type'] == 'text':
                await bot.send_message(
                    chat_id=user_id,
                    text=content_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'photo':
                await bot.send_photo(
                    chat_id=user_id,
                    photo=content_data['photo'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'video':
                await bot.send_video(
                    chat_id=user_id,
                    video=content_data['video'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'document':
                await bot.send_document(
                    chat_id=user_id,
                    document=content_data['document'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'animation':
                await bot.send_animation(
                    chat_id=user_id,
                    animation=content_data['animation'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'sticker':
                await bot.send_sticker(
                    chat_id=user_id,
                    sticker=content_data['sticker'],
                    reply_markup=keyboard
                )
                
            sent += 1
            await asyncio.sleep(0.05)  # Защита от флуда
            
        except Exception as e:
            failed += 1
            logging.error(f"Failed to send to {user_id}: {e}")

    await message.answer(f"✅ Рассылка завершена\nОтправлено: {sent}\nНе удалось: {failed}")
    await state.clear()


@router.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
        
    await state.clear()
    await message.answer("❌ Действие отменено")


# ================== RUN ==================

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Удаляем вебхук и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
