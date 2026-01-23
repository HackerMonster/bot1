import asyncio
import logging
import aiohttp
import sqlite3
import re
import random
import string
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, Router, types, F
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
BOT_USERNAME = "LinksSecret_Bot"

# ID группы и топика
GROUP_CHAT_ID = -1003339432604  # ID группы https://t.me/c/3339432604/2
GROUP_TOPIC_ID = 2  # ID топика в группе

# ===============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

router = Router()

# Хранилище пользователей
USERS = set()

# ================== БАЗА ДАННЫХ ДЛЯ ССЫЛОК ==================

def init_database():
    """Инициализация базы данных для скриптов"""
    try:
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unique_code TEXT UNIQUE NOT NULL,
            script_content TEXT NOT NULL,
            created_by INTEGER,
            created_in_group BOOLEAN DEFAULT FALSE,
            group_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")

init_database()

# ================== FSM СОСТОЯНИЯ ==================

class UploadScriptState(StatesGroup):
    waiting_script = State()

class BroadcastState(StatesGroup):
    waiting_content = State()
    waiting_buttons = State()
    waiting_confirmation = State()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==================

def generate_unique_code():
    """Генерация уникального кода для ссылки от 7 до 25 символов"""
    length = random.randint(7, 25)
    characters = string.ascii_letters + string.digits + "-"
    code = ''.join(random.choice(characters) for _ in range(length))
    return code

def save_script_to_db(script_content: str, created_by: int, created_in_group=False, group_message_id=None):
    """Сохранение скрипта в базу данных и возврат уникального кода"""
    try:
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        while True:
            unique_code = generate_unique_code()
            cursor.execute('SELECT 1 FROM scripts WHERE unique_code = ?', (unique_code,))
            if not cursor.fetchone():
                break
        
        cursor.execute('''
        INSERT INTO scripts (unique_code, script_content, created_by, created_in_group, group_message_id)
        VALUES (?, ?, ?, ?, ?)
        ''', (unique_code, script_content, created_by, created_in_group, group_message_id))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Скрипт сохранен в БД: код={unique_code}, пользователь={created_by}, в_группе={created_in_group}")
        return unique_code
    except Exception as e:
        logger.error(f"Ошибка сохранения скрипта в БД: {e}")
        return None

def get_script_content(unique_code):
    """Получение скрипта по уникальному коду"""
    try:
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT script_content FROM scripts WHERE unique_code = ?', (unique_code,))
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка получения скрипта: {e}")
        return None

def get_statistics():
    """Получение статистики"""
    try:
        conn = sqlite3.connect('scripts.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM scripts")
        total_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by = ?", (ADMIN_ID,))
        admin_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_in_group = TRUE")
        group_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT created_by) FROM scripts WHERE created_in_group = TRUE")
        group_users = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_scripts': total_scripts,
            'admin_scripts': admin_scripts,
            'group_scripts': group_scripts,
            'group_users': group_users,
            'total_users': len(USERS)
        }
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {'total_scripts': 0, 'admin_scripts': 0, 'group_scripts': 0, 'group_users': 0, 'total_users': len(USERS)}

# ================== SubGram ФУНКЦИИ ==================

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
                    logger.error(f"SubGram API error: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"SubGram API error: {e}")
        return None

def create_subscription_keyboard(sponsors_data):
    """Создание клавиатуры с каналами для подписки"""
    sponsors = sponsors_data.get("sponsors", [])
    keyboard = []
    
    for sponsor in sponsors:
        button = InlineKeyboardButton(
            text=sponsor.get("name", "Канал"),
            url=sponsor.get("url", "#")
        )
        keyboard.append([button])
    
    keyboard.append([InlineKeyboardButton(
        text="✅ Проверить подписку",
        callback_data="check_subscription"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================== ФУНКЦИИ ФОРМАТИРОВАНИЯ ==================

def format_script_for_display(script_content: str) -> str:
    """Форматирование скрипта для отображения"""
    if not script_content:
        return ""
    
    if not script_content.startswith('$'):
        script_content = f"${script_content}"
    
    script_content = script_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    lines = script_content.split('\n')
    formatted_lines = []
    
    for line in lines:
        if line.startswith('$'):
            code_content = line[1:].strip()
            if code_content:
                formatted_lines.append(f"<code>{code_content}</code>")
            else:
                formatted_lines.append(line)
        else:
            if '$' in line:
                line = re.sub(r'\$([^$\n]+)\$', r'<code>\1</code>', line)
            formatted_lines.append(line)
    
    return '\n'.join(formatted_lines)

# ================== ПРИВЕТСТВИЕ ==================

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

# ================== /start с уникальными ссылками ==================

@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        return
    
    USERS.add(message.from_user.id)
    
    # Если есть код в команде
    if len(message.text.split()) > 1:
        unique_code = message.text.split()[1]
        await state.update_data(unique_code=unique_code)
        
        # Проверяем подписку через SubGram
        response = await get_subgram_sponsors(message.from_user.id, message.chat.id)
        
        if response and response.get("status") == "warning":
            keyboard = create_subscription_keyboard(response)
            warning_message = "❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
            
            await message.answer(
                warning_message,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
        
        # Если подписка есть, получаем скрипт
        script_content = get_script_content(unique_code)
        
        if script_content:
            await show_script_content(message, script_content)
            await state.clear()
            return
        else:
            await message.answer("❌ Ссылка не найдена или устарела")
            await state.clear()
            return
    
    # Обычный /start без кода
    response = await get_subgram_sponsors(message.from_user.id, message.chat.id)

    if response and response.get("status") == "warning":
        keyboard = create_subscription_keyboard(response)
        warning_message = "❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
        
        await message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    await send_welcome(message)

async def show_script_content(message: types.Message, script_content: str):
    """Показать скрипт с форматированием"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="⚡️ Больше скриптов ⚡️", 
                url="https://t.me/script_f"
            )]
        ]
    )
    
    formatted_script = format_script_for_display(script_content)
    
    header_text = "<b>✅ | Спасибо за подписки!</b>\n\n"
    footer_text = f"\n\n@{BOT_USERNAME}"
    
    final_text = header_text + formatted_script + footer_text
    
    await message.answer(
        final_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("⏳ Проверяем подписку...")
    
    try:
        await callback.message.delete()
    except:
        pass
    
    response = await get_subgram_sponsors(callback.from_user.id, callback.message.chat.id)
    
    if response and response.get("status") == "warning":
        keyboard = create_subscription_keyboard(response)
        warning_message = "❌ Вы еще не подписались на все каналы!\n\n❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
        
        await callback.message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    data = await state.get_data()
    unique_code = data.get('unique_code')
    
    if unique_code:
        script_content = get_script_content(unique_code)
        
        if script_content:
            await show_script_content(callback.message, script_content)
        else:
            await callback.message.answer("❌ Ссылка не найдена или устарела")
    else:
        await send_welcome(callback)
    
    await state.clear()

# ================== РАБОТА В ГРУППЕ (ИСПРАВЛЕНО) ==================

@router.message(F.chat.id == GROUP_CHAT_ID)
async def handle_group_message(message: types.Message):
    """Обработка сообщений в группе"""
    # ИСПРАВЛЕНО: Проверка topic_id с обработкой исключений
    try:
        # Проверяем, что это нужный топик (ID: 2)
        # Используем getattr для безопасной проверки атрибута
        if hasattr(message, 'message_thread_id') and message.message_thread_id is not None:
            if message.message_thread_id != GROUP_TOPIC_ID:
                return
    except Exception as e:
        logger.error(f"Ошибка проверки topic_id: {e}")
        # Если не получается проверить, продолжаем обработку
    
    # Пропускаем команды
    if message.text and message.text.startswith('/'):
        return
    
    user_id = message.from_user.id
    user_name = message.from_user.full_name or f"user_{user_id}"
    
    try:
        # Получаем текст сообщения
        script_content = ""
        
        if message.content_type == 'text':
            script_content = message.text.strip()
            logger.info(f"Получен текст от {user_name} в группе: {script_content[:50]}...")
            
        elif message.content_type == 'document' and message.document:
            logger.info(f"Получен документ от {user_name} в группе: {message.document.file_name}")
            try:
                # ИСПРАВЛЕНО: Загрузка файла через бота
                file = await message.bot.download(message.document)
                if hasattr(file, 'read'):
                    script_content = file.read().decode('utf-8', errors='ignore')
                else:
                    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                        script_content = f.read()
                logger.info(f"Файл прочитан, размер: {len(script_content)} символов")
            except Exception as e:
                logger.error(f"Ошибка чтения файла: {e}")
                await message.reply("❌ Не удалось прочитать файл. Отправьте скрипт как текст.")
                return
        else:
            # Игнорируем другие типы сообщений
            logger.info(f"Игнорирован тип контента: {message.content_type}")
            return
        
        if not script_content or script_content.strip() == "":
            await message.reply("❌ Скрипт не может быть пустым.")
            return
        
        # Сохраняем скрипт в базу данных
        unique_code = save_script_to_db(
            script_content=script_content,
            created_by=user_id,
            created_in_group=True,
            group_message_id=message.message_id
        )
        
        if not unique_code:
            await message.reply("❌ Ошибка при создании ссылки. Попробуйте еще раз.")
            return
        
        # Создаем ссылку
        link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
        
        # Отправляем ответ в группу
        reply_text = (
            f"✅ <b>Уникальная ссылка создана!</b>\n\n"
            f"👤 <b>Отправитель:</b> {user_name}\n"
            f"🔗 <code>{link}</code>\n\n"
            f"<i>Нажмите на ссылку, чтобы получить доступ к скрипту</i>"
        )
        
        # Кнопка для перехода к скрипту
        keyboard = [[InlineKeyboardButton("🔗 Перейти к скрипту", url=link)]]
        
        await message.reply(
            reply_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            disable_web_page_preview=True
        )
        
        logger.info(f"Создана ссылка из группы пользователем {user_id} ({user_name}): {unique_code}")
        
    except Exception as e:
        logger.error(f"Ошибка обработки группового сообщения: {e}", exc_info=True)
        try:
            await message.reply(f"❌ Ошибка при создании ссылки: {str(e)[:100]}")
        except:
            pass

# ================== АДМИН ПАНЕЛЬ ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.chat.type != "private":
        return
        
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📤 Загрузить скрипт", callback_data="admin_upload_script")]
        ]
    )

    await message.answer("👑 <b>Админ-панель</b>", reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "admin_upload_script")
async def upload_script_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "<b>📌 Автоматическое форматирование:</b>\n"
        "• Скрипт будет отображаться как моноширинный текст\n"
        "• Перед скриптом автоматически добавится $\n\n"
        "<i>Пример скрипта:</i>\n"
        "<code>loadstring(game:HttpGet('https://raw.githubusercontent.com/...'))()</code>\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.",
        parse_mode="HTML"
    )
    
    await state.set_state(UploadScriptState.waiting_script)
    await callback.answer()

@router.message(UploadScriptState.waiting_script)
async def process_script_upload(message: types.Message, state: FSMContext):
    """ИСПРАВЛЕНО: Загрузка скрипта через админ-панель"""
    if message.chat.type != "private":
        await state.clear()
        return
    
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        # Получаем текст скрипта
        script_content = ""
        
        if message.content_type == 'text':
            script_content = message.text.strip()
            logger.info(f"Админ загрузил текст: {script_content[:50]}...")
        elif message.content_type == 'document' and message.document:
            logger.info(f"Админ загрузил документ: {message.document.file_name}")
            try:
                file = await message.bot.download(message.document)
                if hasattr(file, 'read'):
                    script_content = file.read().decode('utf-8', errors='ignore')
                else:
                    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                        script_content = f.read()
            except Exception as e:
                logger.error(f"Ошибка чтения файла админом: {e}")
                await message.answer("❌ Не удалось прочитать файл. Отправьте скрипт как текст.")
                return
        else:
            await message.answer("❌ Отправьте скрипт в виде текста или текстового файла.")
            return
        
        if not script_content or script_content.strip() == "":
            await message.answer("❌ Скрипт не может быть пустым.")
            return
        
        # Сохраняем скрипт в базу данных
        unique_code = save_script_to_db(script_content, message.from_user.id)
        
        if not unique_code:
            await message.answer("❌ Ошибка при сохранении скрипта в базу данных.")
            await state.clear()
            return
        
        # Создаем ссылку
        link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
        
        # Показываем результат
        preview_text = "<b>✅ Скрипт загружен!</b>\n\n"
        preview_text += f"<b>Уникальная ссылка:</b>\n<code>{link}</code>\n\n"
        
        # Показываем предпросмотр скрипта
        script_preview = script_content[:200] + "..." if len(script_content) > 200 else script_content
        formatted_preview = format_script_for_display(script_preview)
        
        preview_text += "<b>📝 Предпросмотр скрипта:</b>\n"
        preview_text += formatted_preview
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Открыть ссылку", url=link)],
                [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
            ]
        )
        
        await message.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(f"Админ создал ссылку: {unique_code}")
        
    except Exception as e:
        logger.error(f"Ошибка загрузки скрипта админом: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при загрузке скрипта: {str(e)[:100]}")
    
    await state.clear()

# ================== ОСТАЛЬНЫЕ АДМИН ФУНКЦИИ ==================

@router.callback_query(F.data.startswith("admin_"))
async def admin_callbacks(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if callback.data == "admin_stats":
        stats = get_statistics()
        
        stats_text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
            f"📜 Всего скриптов: <b>{stats['total_scripts']}</b>\n"
            f"👑 Загружено админом: <b>{stats['admin_scripts']}</b>\n"
            f"👥 Загружено в группе: <b>{stats['group_scripts']}</b>\n"
            f"👤 Уникальных пользователей в группе: <b>{stats['group_users']}</b>\n\n"
            f"🔗 Используется SubGram: <b>✅ Да</b>"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
                [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
            ]
        )
        
        await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")

    elif callback.data == "admin_broadcast":
        await callback.message.edit_text(
            "📢 <b>Начало рассылки</b>\n\n"
            "Отправьте сообщение для рассылки:\n"
            "• Текст\n"
            "• Фото с подписью\n"
            "• Видео с подписью\n"
            "• Документ с подписью\n\n"
            "После отправки контента вы сможете добавить URL-кнопки.",
            parse_mode="HTML"
        )
        await state.set_state(BroadcastState.waiting_content)

    await callback.answer()

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📤 Загрузить скрипт", callback_data="admin_upload_script")]
        ]
    )
    
    await callback.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=keyboard, parse_mode="HTML")

# ================== ФУНКЦИИ РАССЫЛКИ ==================

def parse_buttons(text: str) -> InlineKeyboardMarkup | None:
    """Парсинг URL-кнопок из текста"""
    if not text or text.lower().strip() == "нет":
        return None
    
    keyboard = []
    rows = text.strip().split('\n')
    
    for row in rows[:15]:
        buttons = []
        button_parts = row.split('|')
        
        for part in button_parts[:8]:
            part = part.strip()
            if '-' not in part:
                continue
            
            name_url = part.split('-', 1)
            if len(name_url) != 2:
                continue
            
            name, url = name_url
            name = name.strip()
            url = url.strip()
            
            if url and (url.startswith('http://') or url.startswith('https://')):
                buttons.append(InlineKeyboardButton(text=name, url=url))
        
        if buttons:
            keyboard.append(buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

@router.message(BroadcastState.waiting_content)
async def broadcast_get_content(message: types.Message, state: FSMContext):
    """Получение контента для рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    content_data = {
        'content_type': message.content_type,
        'text': message.text if message.text else "",
        'caption': message.caption if message.caption else "",
        'photo': message.photo[-1].file_id if message.photo else None,
        'video': message.video.file_id if message.video else None,
        'document': message.document.file_id if message.document else None,
    }
    
    await state.update_data(content_data=content_data)
    
    await message.answer(
        "⛓ <b>КНОПКИ: URL</b>\n\n"
        "Отправьте боту список URL-кнопок в следующем формате:\n\n"
        "<code>Кнопка 1 - http://link.com\n"
        "Кнопка 2 - http://link.com</code>\n\n"
        "Используйте разделитель « | », чтобы добавить до 8 кнопок в один ряд (допустимо 15 рядов):\n\n"
        "<code>Кнопка 1 - http://link.com | Кнопка 2 - http://link.com</code>\n\n"
        "Или напишите <b>нет</b> для рассылки без кнопок",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastState.waiting_buttons)

@router.message(BroadcastState.waiting_buttons)
async def broadcast_get_buttons(message: types.Message, state: FSMContext):
    """Получение кнопок для рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    keyboard = parse_buttons(message.text)
    
    await state.update_data(keyboard=keyboard)
    
    data = await state.get_data()
    content_data = data.get('content_data', {})
    
    preview_text = "👁 <b>Предпросмотр рассылки:</b>\n\n"
    
    if content_data['content_type'] == 'text':
        text_preview = content_data['text'][:200] + "..." if content_data['text'] and len(content_data['text']) > 200 else content_data['text']
        preview_text += f"📝 <b>Текст:</b>\n{text_preview}\n\n"
    else:
        preview_text += f"📷 <b>Тип:</b> {content_data['content_type']}\n"
        if content_data['caption']:
            caption_preview = content_data['caption'][:200] + "..." if len(content_data['caption']) > 200 else content_data['caption']
            preview_text += f"📝 <b>Подпись:</b>\n{caption_preview}\n\n"
        else:
            preview_text += "📝 <b>Подпись:</b> (нет подписи)\n\n"
    
    preview_text += f"🔘 <b>Кнопки:</b> {'✅ Есть' if keyboard else '❌ Нет'}\n\n"
    preview_text += "Отправить рассылку? Напишите <b>да</b> для подтверждения или <b>нет</b> для отмены."
    
    await message.answer(preview_text, parse_mode="HTML")
    
    await state.set_state(BroadcastState.waiting_confirmation)

@router.message(BroadcastState.waiting_confirmation)
async def broadcast_confirm(message: types.Message, state: FSMContext, bot: Bot):
    """Подтверждение и отправка рассылки"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if message.text.lower() != 'да':
        await message.answer("❌ Рассылка отменена")
        await state.clear()
        return
    
    data = await state.get_data()
    content_data = data.get('content_data', {})
    keyboard = data.get('keyboard')
    
    sent = 0
    failed = 0
    
    await message.answer("⏳ Начинаю рассылку...")
    
    for user_id in list(USERS):
        try:
            if content_data['content_type'] == 'text':
                await bot.send_message(
                    chat_id=user_id,
                    text=content_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'photo' and content_data['photo']:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=content_data['photo'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'video' and content_data['video']:
                await bot.send_video(
                    chat_id=user_id,
                    video=content_data['video'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif content_data['content_type'] == 'document' and content_data['document']:
                await bot.send_document(
                    chat_id=user_id,
                    document=content_data['document'],
                    caption=content_data.get('caption'),
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            sent += 1
            await asyncio.sleep(0.05)
            
        except Exception as e:
            failed += 1
            logger.error(f"Не удалось отправить пользователю {user_id}: {e}")
    
    result_text = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Отправлено: {sent}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего пользователей: {len(USERS)}\n\n"
        f"📈 <b>Эффективность:</b> {(sent/(sent+failed)*100 if (sent+failed) > 0 else 0):.1f}%"
    )
    
    await message.answer(result_text, parse_mode="HTML")
    await state.clear()

# ================== ОТМЕНА ==================

@router.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("❌ Действие отменено")

# ================== ОБРАБОТКА ДРУГИХ СООБЩЕНИЙ ==================

@router.message()
async def handle_other_messages(message: types.Message):
    """Обработка других сообщений"""
    if message.chat.type != "private":
        return
    
    if not message.text or not message.text.startswith('/'):
        if message.from_user.id != ADMIN_ID:
            await message.answer(
                "🤖 <b>Этот бот работает с уникальными ссылками на скрипты</b>\n\n"
                "Перейдите по ссылке, которую вам отправили, чтобы получить доступ к скрипту.\n\n"
                "Или используйте команду /start",
                parse_mode="HTML"
            )

# ================== RUN ==================

async def main():
    logger.info("Запуск бота...")
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удален")
        
        logger.info("Бот запущен!")
        logger.info(f"👑 Админ ID: {ADMIN_ID}")
        logger.info(f"📌 Группа ID: {GROUP_CHAT_ID} (топик {GROUP_TOPIC_ID})")
        logger.info(f"🔗 Пример ссылки: https://t.me/{BOT_USERNAME}?start=пример123")
        
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
