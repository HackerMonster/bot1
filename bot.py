import logging
import re
import random
import string
import os
import io
import base64
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import BadRequest

# === НАСТРОЙКИ ===

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

ADMIN_USER_IDS = {8523456846, 5870949629}
MAX_CAMPAIGNS = 15
MAX_MEMBER_LIMIT = 50000
BOT_USERNAME = "LinksSecret_Bot"

# === КОНКРЕТНАЯ ГРУППА ДЛЯ РАБОТЫ ===
ALLOWED_GROUP_ID = -1003339432604  # ID группы https://t.me/c/3339432604/2

# === SUBGRAM API КОНФИГУРАЦИЯ ===
SUBGRAM_API_KEY = os.getenv("SUBGRAM_API_KEY", "ВАШ_API_КЛЮЧ_БОТА")
SUBGRAM_API_URL = "https://api.subgram.org/get-sponsors"

# Хранилища
active_campaigns = {}
user_ids = set()
saved_messages = {}
user_password_attempts = {}  # user_id -> {'code': str, 'attempts': int}

# === API-конфигурация (env) ===

FLYER_API_URL = os.getenv("FLYER_API_URL", "https://api.flyerservice.io")
FLYER_API_KEY = os.getenv("FLYER_API_KEY", None)

# === SUBGRAM API ИНТЕГРАЦИЯ ===

async def get_subgram_sponsors(user_id: int, chat_id: int, **kwargs) -> dict | None:
    """Универсальная функция для запроса спонсоров из SubGram API."""
    headers = {"Auth": SUBGRAM_API_KEY}
    payload = {
        "user_id": user_id,
        "chat_id": chat_id,
        "first_name": kwargs.get("first_name", ""),
        "username": kwargs.get("username", ""),
        "language_code": kwargs.get("language_code", "ru"),
        "is_premium": kwargs.get("is_premium", False)
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                SUBGRAM_API_URL, 
                headers=headers, 
                json=payload, 
                timeout=10
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"SubGram API error: {response.status}")
                    return None
        except Exception as e:
            logging.error(f"Ошибка запроса к SubGram API: {e}")
            return None

async def process_subgram_check(user, chat_id: int, api_kwargs: dict = None) -> Tuple[bool, Optional[str], Optional[InlineKeyboardMarkup]]:
    """Основная функция для обработки всех статусов от SubGram."""
    if api_kwargs is None:
        api_kwargs = {}

    user_data = {
        "first_name": user.first_name or "",
        "username": user.username or "",
        "language_code": user.language_code or "ru",
        "is_premium": bool(user.is_premium if hasattr(user, 'is_premium') else False)
    }
    user_data.update(api_kwargs)
    
    response = await get_subgram_sponsors(user.id, chat_id, **user_data)

    if response:
        status = response.get("status")
        if status and status == "warning":
            # Нужно подписаться на спонсоров
            builder = []
            text = "❕ | Прежде чем пользоваться ботом, подпишись на указанные каналы ниже!\n\n⚠️ Подпишитесь на все каналы\n\n❕ Нажмите по кнопкам ниже, затем проверьте подписку."
            
            sponsors = response.get("additional", {}).get("sponsors", [])
            for sponsor in sponsors:
                # Показываем только тех, на кого надо подписаться
                if sponsor.get("available_now") and sponsor.get("status") == "unsubscribed":
                    button_text = sponsor.get("button_text", "🔺 Подписаться")
                    link = sponsor.get("link", "")
                    if link:
                        builder.append([InlineKeyboardButton(button_text, url=link)])
            
            # Добавляем кнопку проверки подписки
            if builder:
                builder.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")])
                return False, text, InlineKeyboardMarkup(builder)
            else:
                # Если нет спонсоров для подписки, разрешаем доступ
                return True, None, None
        else:  # error, ok или неизвестный статус -> пускаем
            return True, None, None
    else:  # ошибка запроса -> пускаем
        return True, None, None

# === ОБНОВЛЕННАЯ: Обработка сообщений из КОНКРЕТНОЙ группы ===

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений только из конкретной группы"""
    if not update.message:
        return
    
    # Проверяем, что сообщение из РАЗРЕШЕННОЙ группы
    if update.effective_chat.id != ALLOWED_GROUP_ID:
        return  # Игнорируем все другие группы/каналы
    
    # Пропускаем команды бота
    if update.message.text and update.message.text.startswith('/'):
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    try:
        # Получаем текст сообщения
        message_text = ""
        content_type = "text"
        
        if update.message.text:
            message_text = update.message.text
            content_type = "text"
        elif update.message.caption:
            message_text = update.message.caption
            if update.message.photo:
                content_type = "photo"
            elif update.message.video:
                content_type = "video"
            elif update.message.document:
                content_type = "document"
        else:
            # Если нет текста и нет подписи
            return
        
        # Форматируем текст с кодом
        formatted_text = format_text_with_code_blocks(message_text)
        
        # Генерируем уникальную ссылку для этого сообщения
        length = random.randint(6, 25)
        safe_chars = string.ascii_letters + string.digits + "-"
        unique_code = ''.join(random.choices(safe_chars, k=length))
        
        # Сохраняем сообщение в зависимости от типа
        if content_type == "text":
            saved_messages[unique_code] = {
                'type': 'text',
                'content': formatted_text,
                'password': None,
                'created_in_group': True,
                'group_message_id': update.message.message_id,
                'group_chat_id': chat_id,
                'user_id': user_id,
                'timestamp': datetime.now()
            }
        elif content_type == "photo":
            saved_messages[unique_code] = {
                'type': 'photo',
                'content': update.message.photo[-1].file_id,
                'caption': formatted_text,
                'password': None,
                'created_in_group': True,
                'group_message_id': update.message.message_id,
                'group_chat_id': chat_id,
                'user_id': user_id,
                'timestamp': datetime.now()
            }
        elif content_type == "video":
            saved_messages[unique_code] = {
                'type': 'video',
                'content': update.message.video.file_id,
                'caption': formatted_text,
                'password': None,
                'created_in_group': True,
                'group_message_id': update.message.message_id,
                'group_chat_id': chat_id,
                'user_id': user_id,
                'timestamp': datetime.now()
            }
        elif content_type == "document":
            saved_messages[unique_code] = {
                'type': 'document',
                'content': update.message.document.file_id,
                'caption': formatted_text,
                'password': None,
                'created_in_group': True,
                'group_message_id': update.message.message_id,
                'group_chat_id': chat_id,
                'user_id': user_id,
                'timestamp': datetime.now()
            }
        
        # Создаем ссылку
        link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
        
        # Отправляем ответ в группу
        reply_text = (
            f"✅ <b>Уникальная ссылка создана!</b>\n\n"
            f"👤 Отправитель: {update.effective_user.first_name or 'Пользователь'}\n"
            f"🔗 <code>{link}</code>\n\n"
            f"<i>Нажмите на ссылку, чтобы получить доступ к контенту</i>"
        )
        
        # УБРАЛИ кнопку "Скопировать код" - оставляем только кнопку перехода
        keyboard = [[InlineKeyboardButton("🔗 Перейти к контенту", url=link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем ответ
        await update.message.reply_text(
            reply_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        
        # Логируем создание ссылки
        logging.info(f"Создана ссылка из группы {chat_id} пользователем {user_id}: {unique_code}")
        
    except Exception as e:
        logging.error(f"Ошибка обработки группового сообщения: {e}")
        try:
            await update.message.reply_text("❌ Ошибка при создании ссылки. Попробуйте еще раз.")
        except:
            pass

# === НОВОЕ: Дополнительный способ загрузки через админку ===

async def admin_upload_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню загрузки контента через админку"""
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещён.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 Создать ссылку из текста", callback_data="admin_upload_text")],
        [InlineKeyboardButton("🖼 Создать ссылку из фото", callback_data="admin_upload_photo")],
        [InlineKeyboardButton("🎥 Создать ссылку из видео", callback_data="admin_upload_video")],
        [InlineKeyboardButton("📎 Создать ссылку из файла", callback_data="admin_upload_document")],
        [InlineKeyboardButton("🔗 Прямая загрузка (как в группе)", callback_data="admin_upload_direct")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
    ]
    
    await update.message.reply_text(
        "📤 **Панель загрузки контента**\n\n"
        "Выберите тип контента для создания ссылки:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def admin_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок загрузки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_upload_text":
        context.user_data["upload_mode"] = "text"
        await query.edit_message_text(
            "📝 Отправьте текст для создания ссылки:\n\n"
            "Формат с паролем: <code>#[пароль] ваш текст</code>\n"
            "Можно использовать $ для кодовых блоков\n\n"
            "<i>Пример с кодом:</i>\n"
            "<code>$loadstring(game:HttpGet'https://raw.githubusercontent.com/...')()</code>",
            parse_mode="HTML"
        )
    elif query.data == "admin_upload_photo":
        context.user_data["upload_mode"] = "photo"
        await query.edit_message_text(
            "🖼 Отправьте фото для создания ссылки:\n\n"
            "В подписи можно указать пароль: <code>#[пароль] ваш текст</code>\n"
            "Можно использовать $ для кодовых блоков",
            parse_mode="HTML"
        )
    elif query.data == "admin_upload_video":
        context.user_data["upload_mode"] = "video"
        await query.edit_message_text(
            "🎥 Отправьте видео для создания ссылки:\n\n"
            "В подписи можно указать пароль: <code>#[пароль] ваш текст</code>\n"
            "Можно использовать $ для кодовых блоков",
            parse_mode="HTML"
        )
    elif query.data == "admin_upload_document":
        context.user_data["upload_mode"] = "document"
        await query.edit_message_text(
            "📎 Отправьте файл для создания ссылки:\n\n"
            "В подписи можно указать пароль: <code>#[пароль] ваш текст</code>\n"
            "Можно использовать $ для кодовых блоков",
            parse_mode="HTML"
        )
    elif query.data == "admin_upload_direct":
        context.user_data["upload_mode"] = "direct"
        await query.edit_message_text(
            "🔗 **Прямая загрузка**\n\n"
            "Просто отправьте любое сообщение (текст, фото, видео, файл) и бот автоматически создаст ссылку.\n\n"
            "Для выхода из режима используйте /cancel",
            parse_mode="HTML"
        )

async def handle_admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загружаемого контента через админку"""
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    upload_mode = context.user_data.get("upload_mode")
    if not upload_mode:
        return
    
    # Создаем уникальную ссылку
    length = random.randint(6, 25)
    safe_chars = string.ascii_letters + string.digits + "-"
    unique_code = ''.join(random.choices(safe_chars, k=length))
    
    password = None
    content = ""
    
    def extract_password_and_text(s: str):
        s = s.strip()
        if s.startswith("#"):
            parts = s.split(None, 1)
            if len(parts) == 1:
                return parts[0][1:], ""
            else:
                return parts[0][1:], parts[1]
        return None, s
    
    if upload_mode == "text" and update.message.text:
        text = update.message.text
        password, content = extract_password_and_text(text)
        saved_messages[unique_code] = {
            'type': 'text',
            'content': format_text_with_code_blocks(content),
            'password': password
        }
    
    elif upload_mode == "photo" and update.message.photo:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'photo',
            'content': update.message.photo[-1].file_id,
            'caption': caption,
            'password': password
        }
    
    elif upload_mode == "video" and update.message.video:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'video',
            'content': update.message.video.file_id,
            'caption': caption,
            'password': password
        }
    
    elif upload_mode == "document" and update.message.document:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'document',
            'content': update.message.document.file_id,
            'caption': caption,
            'password': password
        }
    
    elif upload_mode == "direct":
        # Автоматически определяем тип контента
        if update.message.text:
            text = update.message.text
            password, content = extract_password_and_text(text)
            saved_messages[unique_code] = {
                'type': 'text',
                'content': format_text_with_code_blocks(content),
                'password': password
            }
        elif update.message.photo:
            caption = update.message.caption or ""
            password, caption = extract_password_and_text(caption)
            saved_messages[unique_code] = {
                'type': 'photo',
                'content': update.message.photo[-1].file_id,
                'caption': caption,
                'password': password
            }
        elif update.message.video:
            caption = update.message.caption or ""
            password, caption = extract_password_and_text(caption)
            saved_messages[unique_code] = {
                'type': 'video',
                'content': update.message.video.file_id,
                'caption': caption,
                'password': password
            }
        elif update.message.document:
            caption = update.message.caption or ""
            password, caption = extract_password_and_text(caption)
            saved_messages[unique_code] = {
                'type': 'document',
                'content': update.message.document.file_id,
                'caption': caption,
                'password': password
            }
        else:
            await update.message.reply_text("❌ Неподдерживаемый тип контента.")
            return
    
    else:
        await update.message.reply_text("❌ Неверный тип контента для выбранного режима.")
        return
    
    # Создаем ссылку
    link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
    
    # Очищаем режим загрузки
    context.user_data.pop("upload_mode", None)
    
    await update.message.reply_text(
        f"✅ Контент загружен! Уникальная ссылка создана:\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"Пароль: {'🔒 ' + password if password else '❌ Нет'}\n"
        f"Тип: {saved_messages[unique_code]['type']}",
        parse_mode="HTML"
    )

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена режима загрузки"""
    if update.effective_chat.type != "private":
        return
    
    context.user_data.pop("upload_mode", None)
    context.user_data.pop("create_link_mode", None)
    context.user_data.pop("broadcast_mode", None)
    
    await update.message.reply_text("✅ Все активные режимы отменены.")

# === ВОССТАНОВЛЕННАЯ ФУНКЦИЯ setup_command ===

async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    if len(context.args) < 2:
        await update.message.reply_text("❌ Используйте: /setup <chat_id> <ссылка> [время/лимит]\nПример: /setup -100123456 https://t.me/channel 30m")
        return
    if len(active_campaigns) >= MAX_CAMPAIGNS:
        await update.message.reply_text(f"❌ Достигнут лимит: максимум {MAX_CAMPAIGNS} активных проверок.")
        return
    try:
        chat_id = int(context.args[0])
        link = context.args[1].strip()
        if not link.startswith("https://t.me/"):
            raise ValueError("Ссылка должна начинаться с https://t.me/")
        param = context.args[2].strip() if len(context.args) > 2 else "w"
        delta, member_limit = parse_duration(param)
        expires_at = None
        if delta:
            expires_at = datetime.now() + delta
        active_campaigns[chat_id] = {
            'link': link,
            'expires_at': expires_at,
            'member_limit': member_limit,
            'start_time': datetime.now()
        }
        if not expires_at and not member_limit:
            status = "навсегда"
        elif expires_at:
            mins = int(delta.total_seconds() // 60)
            status = f"до {expires_at.strftime('%Y-%m-%d %H:%M')} ({mins} мин)"
        else:
            status = f"до {member_limit} участников"
        await update.message.reply_text(f"✅ Проверка добавлена!\nID: {chat_id}\nСсылка: {link}\nДействует: {status}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}\n\nИспользуйте: /setup <chat_id> <ссылка> [время/лимит]")

# === ВОССТАНОВЛЕННАЯ ФУНКЦИЯ parse_duration ===

def parse_duration(param: str):
    param = param.strip().lower()
    if param == "w":
        return None, None
    if param.isdigit():
        limit = int(param)
        if limit > MAX_MEMBER_LIMIT:
            raise ValueError(f"Лимит не может быть больше {MAX_MEMBER_LIMIT}")
        return None, limit
    match = re.match(r'^(\d+)([smhd])$', param)
    if not match:
        raise ValueError("Неверный формат времени. Используйте: 30s, 5m, 1h, 2d или число для лимита участников")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 's':
        delta = timedelta(seconds=amount)
    elif unit == 'm':
        delta = timedelta(minutes=amount)
    elif unit == 'h':
        delta = timedelta(hours=amount)
    elif unit == 'd':
        delta = timedelta(days=amount)
    else:
        raise ValueError("Недопустимая единица времени")
    return delta, None

# === УЛУЧШЕННОЕ ФОРМАТИРОВАНИЕ ТЕКСТА С КОДОМ ===

def format_text_with_code_blocks(text: str) -> str:
    if not text:
        return text
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith('$'):
            code_content = stripped[1:]
            code_content = code_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            # Убираем лишние пробелы для лучшего копирования
            code_content = code_content.strip()
            result.append(f"<code>{code_content}</code>")
        else:
            safe_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            result.append(safe_line)
    return '\n'.join(result)

# === ОБНОВЛЕННЫЕ ФУНКЦИИ ПРОВЕРКИ ПОДПИСОК С SUBGRAM ===

async def check_user_subscriptions(user_id: int, chat_id: int, user_data: dict = None) -> Tuple[bool, Optional[str], Optional[InlineKeyboardMarkup]]:
    """Проверка подписок пользователя через SubGram API"""
    if user_data is None:
        user_data = {}
    
    try:
        # Сначала пробуем SubGram API
        is_allowed, text, reply_markup = await process_subgram_check(
            type('User', (), {
                'id': user_id,
                'first_name': user_data.get('first_name', ''),
                'username': user_data.get('username', ''),
                'language_code': user_data.get('language_code', 'ru'),
                'is_premium': user_data.get('is_premium', False)
            })(),
            chat_id,
            user_data
        )
        
        return is_allowed, text, reply_markup
        
    except Exception as e:
        logging.error(f"Ошибка проверки подписок: {e}")
        # В случае ошибки разрешаем доступ
        return True, None, None

# === ОБНОВЛЕННОЕ ПРИВЕТСТВИЕ С ЖИРНЫМ ТЕКСТОМ И SUBGRAM ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await cleanup_expired_campaigns(context)
    
    # Получаем имя пользователя
    user = update.effective_user
    first_name = user.first_name or "друг"
    last_name = user.last_name or ""
    if last_name:
        name = f"{first_name} {last_name}"
    else:
        name = first_name
    
    # Проверяем подписки через SubGram
    user_id = update.effective_user.id
    user_ids.add(user_id)
    
    user_data = {
        'first_name': user.first_name or '',
        'username': user.username or '',
        'language_code': user.language_code or 'ru',
        'is_premium': user.is_premium if hasattr(user, 'is_premium') else False
    }
    
    is_allowed, text, reply_markup = await check_user_subscriptions(
        user_id, 
        update.effective_chat.id,
        user_data
    )
    
    if not is_allowed:
        # Показываем сообщение с просьбой подписаться
        await update.effective_message.reply_text(text, reply_markup=reply_markup)
        return
    
    # Пользователь подписан на все каналы
    welcome = f"""<b>👋 Привет, друг/подруга {name}!</b>

<b>Добро пожаловать в Secret Link</b> — место, где ты можешь быстро и безопасно получить свой скрипт для Roblox.

<b>🔹 Что тебя ждёт:</b>
• <b>⚡️ Только лучшие скрипты</b> — без вирусов, рекламы и переходников  
• <b>🛡 Проверены вручную</b> — гарантированная безопасность
• <b>🔁 Постоянные обновления</b> — всё актуально и стабильно работает

<b>❗️ Важно:</b>  
Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»

Для сотрудничества: @SecretLinkAds"""

    keyboard = [
        [InlineKeyboardButton("🔥 Наш канал ", url="https://t.me/script_f")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.edit_text(welcome, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(welcome, reply_markup=reply_markup, parse_mode="HTML")

async def start_with_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    user_ids.add(user_id)
    await cleanup_expired_campaigns(context)

    # Проверяем подписки через SubGram
    user = update.effective_user
    user_data = {
        'first_name': user.first_name or '',
        'username': user.username or '',
        'language_code': user.language_code or 'ru',
        'is_premium': user.is_premium if hasattr(user, 'is_premium') else False
    }
    
    is_allowed, text, reply_markup = await check_user_subscriptions(
        user_id, 
        update.effective_chat.id,
        user_data
    )
    
    if not is_allowed:
        # Показываем сообщение с просьбой подписаться
        await update.message.reply_text(text, reply_markup=reply_markup)
        return

    # Обработка кода из ссылки
    if context.args:
        code = context.args[0]
        if code not in saved_messages:
            await update.message.reply_text("❌ Неверная или устаревшая ссылка.")
            return

        data = saved_messages[code]
        password = data.get('password')

        if password:
            if user_id in user_password_attempts and user_password_attempts[user_id]['code'] == code:
                entered = update.message.text.strip()
                attempts = user_password_attempts[user_id].get('attempts', 0)
                if entered == password:
                    del user_password_attempts[user_id]
                    await send_saved_message(update, context, data)
                    return
                else:
                    attempts += 1
                    if attempts >= 3:
                        del user_password_attempts[user_id]
                        await update.message.reply_text("🔒 Превышено количество попыток. Доступ закрыт.")
                        return
                    user_password_attempts[user_id] = {'code': code, 'attempts': attempts}
                    await update.message.reply_text(
                        f"❌ Неверный пароль. Попытка {attempts}/3.\nВведите пароль для доступа к контенту:"
                    )
                    return
            else:
                user_password_attempts[user_id] = {'code': code, 'attempts': 0}
                await update.message.reply_text("🔐 Этот контент защищён паролем.\nВведите пароль:")
                return
        else:
            await send_saved_message(update, context, data)
            return

    await start(update, context)

# === ОБНОВЛЕННАЯ АДМИН-ПАНЕЛЬ ===

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Доступ запрещён.")
        return
    keyboard = [
        [InlineKeyboardButton("✅ Добавить проверку", callback_data="admin_setup")],
        [InlineKeyboardButton("🗑 Удалить проверку", callback_data="admin_unsetup")],
        [InlineKeyboardButton("📋 Статус проверок", callback_data="admin_status")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📤 Загрузить контент", callback_data="admin_upload_menu")],
        [InlineKeyboardButton("🔗 Создать ссылку", callback_data="admin_create_link")],
        [InlineKeyboardButton("🔄 Импорт SubGram", callback_data="admin_import_subgram")],
        [InlineKeyboardButton("🎨 Создать Flyer", callback_data="admin_flyer_create")],
    ]
    await update.message.reply_text("🛠️ Панель управления администратора:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "admin_upload_menu":
        await admin_upload_menu(update, context)
        return
    elif data == "admin_setup":
        await query.edit_message_text(
            "🔧 Отправьте команду в формате:\n<code>/setup &lt;chat_id&gt; &lt;ссылка&gt; [время/лимит]</code>\n\n"
            "Примеры:\n"
            "<code>/setup -1001994526641 https://t.me/script_f 30m</code> - на 30 минут\n"
            "<code>/setup -1001994526641 https://t.me/script_f 1</code> - на 1 участника\n"
            "<code>/setup -1001994526641 https://t.me/script_f 1h</code> - на 1 час\n"
            "<code>/setup -1001994526641 https://t.me/script_f w</code> - навсегда\n\n"
            "Единицы времени: s (секунды), m (минуты), h (часы), d (дни)",
            parse_mode="HTML"
        )
    elif data == "admin_unsetup":
        if not active_campaigns:
            await query.edit_message_text("❌ Нет активных проверок.")
            return
        buttons = [
            [InlineKeyboardButton(f"Удалить {cid}", callback_data=f"del_{cid}")]
            for cid in active_campaigns
        ]
        buttons.append([InlineKeyboardButton("🗑 Удалить всё", callback_data="del_all")])
        buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_back")])
        await query.edit_message_text("Выберите проверку для удаления:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data == "admin_status":
        text = await generate_human_readable_status(context)
        buttons = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif data == "admin_stats":
        total_users = len(user_ids)
        total_campaigns = len(active_campaigns)
        total_links = len(saved_messages)
        protected_links = sum(1 for msg in saved_messages.values() if msg.get('password'))
        group_links = sum(1 for msg in saved_messages.values() if msg.get('created_in_group'))

        stats_text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"👥 Всего пользователей: <b>{total_users:,}</b>\n"
            f"✅ Активных кампаний: <b>{total_campaigns}</b>\n"
            f"🔗 Сохранённых ссылок: <b>{total_links}</b>\n"
            f"📱 Создано в группах: <b>{group_links}</b>\n"
            f"🔒 Защищённых паролем: <b>{protected_links}</b>\n"
            f"🔗 Используется SubGram: <b>✅ Да</b>"
        )
        buttons = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]]
        await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif data == "admin_broadcast":
        context.user_data["broadcast_mode"] = True
        keyboard = [[InlineKeyboardButton("✖️ Отменить", callback_data="cancel_broadcast")]]
        await query.edit_message_text(
            "📨 Отправьте сообщение для рассылки (текст, фото, видео и т.д.):\n\n"
            "Можно добавить кнопки в конце:\n\n"
            "<code>BUTTONS:\nКнопка | https://example.com</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    elif data == "admin_create_link":
        context.user_data["create_link_mode"] = True
        keyboard = [[InlineKeyboardButton("✖️ Отменить", callback_data="cancel_link")]]
        await query.edit_message_text(
            "📤 Отправьте сообщение (текст, фото, видео и т.д.), из которого нужно создать ссылку:\n\n"
            "Формат с паролем: <code>#[пароль] текст</code>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    elif data == "admin_import_subgram":
        await query.edit_message_text("SubGram уже интегрирован в проверку подписок. Настройте спонсоров в панели SubGram.")
    elif data == "admin_flyer_create":
        await query.edit_message_text("Используйте команду /flyer_create <template_id> [chat_id] чтобы создать флаер через Flyer API.")
    elif data == "admin_back":
        await admin_menu(update, context)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

async def get_unsubscribed_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    # Теперь используем SubGram для проверки подписок
    return []

async def notify_campaign_ended(context: ContextTypes.DEFAULT_TYPE, chat_id: int, reason: str):
    if chat_id not in active_campaigns:
        return
    data = active_campaigns[chat_id]
    link = data['link']
    try:
        chat = await context.bot.get_chat(chat_id)
        title = chat.title or chat.username or str(chat_id)
    except:
        title = "Неизвестный канал"
    if reason == "limit":
        reason_text = f"достигнут лимит в {data['member_limit']:,} участников"
    else:
        reason_text = "истекло время действия"
    try:
        current_members = getattr(chat, 'members_count', "N/A")
    except:
        current_members = "N/A"
    start_time = data.get('start_time', datetime.now() - timedelta(hours=1))
    end_time = datetime.now()
    duration = end_time - start_time
    days = duration.days
    hours, remainder = divmod(duration.seconds, 3600)
    minutes = remainder // 60
    dur_str = ""
    if days: dur_str += f"{days} дн "
    if hours: dur_str += f"{hours} ч "
    if minutes: dur_str += f"{minutes} мин"
    if not dur_str: dur_str = "менее минуты"
    message = (
        "🎉 <b>Обязательная подписка завершена!</b>\n\n"
        f"❗ Кампания на канале <b>{title}</b> больше не активна.\n\n"
        f"🔗 <a href=\"{link}\">Перейти в канал</a>\n\n"
        "📊 <b>Статистика:</b>\n"
        f"• Начало: {start_time.strftime('%d %B %Y, %H:%M')}\n"
        f"• Окончание: {end_time.strftime('%d %B %Y, %H:%M')}\n"
        f"• Длительность: {dur_str.strip()}\n"
        f"• Участников привлечено: {current_members}\n\n"
        f"🎯 <b>Причина завершения:</b> {reason_text}\n\n"
        "💬 Спасибо всем, кто подписался!\n"
        "Не отписывайтесь — в канале выходят самые свежие и безопасные скрипты для Roblox!\n\n"
        "🚀 Следите за обновлениями — скоро новые акции!"
    )
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

async def cleanup_expired_campaigns(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    to_remove = []
    for chat_id, data in list(active_campaigns.items()):
        if data.get('expires_at') and now >= data['expires_at']:
            await notify_campaign_ended(context, chat_id, "time")
            to_remove.append(chat_id)
            continue
        if data.get('member_limit'):
            try:
                chat = await context.bot.get_chat(chat_id)
                if hasattr(chat, 'members_count') and chat.members_count >= data['member_limit']:
                    await notify_campaign_ended(context, chat_id, "limit")
                    to_remove.append(chat_id)
            except Exception as e:
                logging.warning(f"Не удалось проверить участников для {chat_id}: {e}")
    for cid in to_remove:
        if cid in active_campaigns:
            del active_campaigns[cid]

def parse_message_with_buttons(text: str):
    if "\nBUTTONS:\n" not in text:
        return text, []
    parts = text.split("\nBUTTONS:\n", 1)
    message_text = parts[0]
    button_lines = parts[1].strip().split("\n")
    buttons = []
    for line in button_lines[:10]:
        if " | " in line:
            name, url = line.split(" | ", 1)
            name = name.strip()
            url = url.strip()
            if name and url.startswith(("http://", "https://", "tg://")):
                buttons.append([InlineKeyboardButton(name, url=url)])
    return message_text, buttons

# === НОВАЯ ФУНКЦИЯ СТАТУСА ===

async def generate_human_readable_status(context: ContextTypes.DEFAULT_TYPE) -> str:
    if not active_campaigns:
        status = "❌ Нет активных локальных проверок подписки."
    else:
        status_lines = []
        now = datetime.now()
        for chat_id, data in active_campaigns.items():
            try:
                chat = await context.bot.get_chat(chat_id)
                title = chat.title or chat.username or f"Канал {chat_id}"
            except Exception as e:
                logging.warning(f"Не удалось получить данные канала {chat_id}: {e}")
                title = f"Канал {chat_id}"
            link = data['link']

            ended = False
            reason = ""
            if data.get('expires_at') and now >= data['expires_at']:
                ended = True
                reason = "время действия истекло"
            elif data.get('member_limit'):
                try:
                    current_count = getattr(chat, 'members_count', 0)
                    if current_count >= data['member_limit']:
                        ended = True
                        reason = f"достигнут лимит в {data['member_limit']:,} участников"
                except:
                    pass

            limit_str = f"{data['member_limit']:,}" if data.get('member_limit') else "∞"
            if data.get('expires_at') and not ended:
                time_left = data['expires_at'] - now
                total_seconds = int(time_left.total_seconds())
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60
                secs = total_seconds % 60
                parts = []
                if days: parts.append(f"{days}д")
                if hours: parts.append(f"{hours}ч")
                if minutes: parts.append(f"{minutes}м")
                if total_seconds < 300: parts.append(f"{secs}с")
                time_str = "".join(parts) if parts else "0с"
            elif data.get('expires_at') and ended:
                time_str = "0"
            else:
                time_str = "∞"

            end_time_str = data['expires_at'].strftime('%d %B %Y, %H:%M') if data.get('expires_at') else "никогда"
            members_str = f"{getattr(chat, 'members_count', '~неизвестно'):,}" if hasattr(chat, 'members_count') else "~неизвестно"

            block = (
                f"📌 {title} / {link}\n"
                f"👥 {limit_str} / ⏳ {time_str}\n"
                f"🕒 {end_time_str}\n"
                f"👤 {members_str}"
            )
            if ended:
                block += f"\n⚠️ КАМПАНИЯ ЗАВЕРШЕНA ({reason})"
            status_lines.append(block)
        status = "\n" + "\n\n".join(status_lines) + "\n"
    return status

# === ОБНОВЛЕННАЯ ОТПРАВКА СООБЩЕНИЯ С ЖИРНЫМ ТЕКСТОМ ===

async def send_saved_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    try:
        # ОБНОВЛЕННЫЙ заголовок с жирным текстом
        standard_header = "<b>✅ | Спасибо за подписки!</b>\n\n"
        bot_mention = "\n\n@LinksSecret_Bot"
        
        # Создаем клавиатуру с кнопкой
        keyboard = [
            [InlineKeyboardButton("⚡️ Больше скриптов ⚡️", url="https://t.me/script_f")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if data['type'] == 'text':
            full_content = standard_header + data['content'] + bot_mention
            await update.message.reply_text(
                full_content, 
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        elif data['type'] == 'photo':
            caption = data.get('caption', '')
            full_caption = standard_header + caption + bot_mention
            await update.message.reply_photo(
                photo=data['content'], 
                caption=full_caption, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif data['type'] == 'video':
            caption = data.get('caption', '')
            full_caption = standard_header + caption + bot_mention
            await update.message.reply_video(
                video=data['content'], 
                caption=full_caption, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif data['type'] == 'document':
            caption = data.get('caption', '')
            full_caption = standard_header + caption + bot_mention
            await update.message.reply_document(
                document=data['content'], 
                caption=full_caption, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    except Exception as e:
        logging.error(f"Ошибка отправки сохранённого сообщения: {e}")
        await update.message.reply_text("❌ Ошибка при отправке контента.")

async def show_subscription_prompt_inplace(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    user_ids.add(user_id)
    
    # Проверяем подписки через SubGram
    user = update.effective_user
    user_data = {
        'first_name': user.first_name or '',
        'username': user.username or '',
        'language_code': user.language_code or 'ru',
        'is_premium': user.is_premium if hasattr(user, 'is_premium') else False
    }
    
    is_allowed, text, reply_markup = await check_user_subscriptions(
        user_id, 
        update.effective_chat.id,
        user_data
    )
    
    if not is_allowed:
        # Показываем сообщение с просьбой подписаться
        if update.callback_query:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=reply_markup)
        return

    # Пользователь подписан на все каналы
    # Обновленное приветствие с жирным текстом
    user = update.effective_user
    first_name = user.first_name or "друг"
    last_name = user.last_name or ""
    if last_name:
        name = f"{first_name} {last_name}"
    else:
        name = first_name
        
    welcome = f"""<b>👋 Привет, друг/подруга {name}!</b>

<b>Добро пожаловать в Secret Link</b> — место, где ты можешь быстро и безопасно получить свой скрипт для Roblox.

<b>🔹 Что тебя ждёт:</b>
• <b>⚡️ Только лучшие скрипты</b> — без вирусов, рекламы и переходников  
• <b>🛡 Проверены вручную</b> — гарантированная безопасность
• <b>🔁 Постоянные обновления</b> — всё актуально и стабильно работает

<b>❗️ Важно:</b>  
Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»

Для сотрудничества: @SecretLinkAds"""

    keyboard = [
        [InlineKeyboardButton("🔥 Наш канал ", url="https://t.me/script_f")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.message.edit_text(welcome, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(welcome, reply_markup=reply_markup, parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_broadcast":
        context.user_data.pop("broadcast_mode", None)
        await query.edit_message_text("❌ Режим рассылки отменён.")
        return

    if query.data == "cancel_link":
        context.user_data.pop("create_link_mode", None)
        await query.edit_message_text("❌ Создание ссылки отменено.")
        return

    if query.data == "check_sub":
        user_id = query.from_user.id
        user = query.from_user
        user_data = {
            'first_name': user.first_name or '',
            'username': user.username or '',
            'language_code': user.language_code or 'ru',
            'is_premium': user.is_premium if hasattr(user, 'is_premium') else False
        }
        
        # Проверяем подписки через SubGram
        is_allowed, text, reply_markup = await check_user_subscriptions(
            user_id, 
            query.message.chat.id,
            user_data
        )
        
        if not is_allowed:
            # Показываем сообщение с просьбой подписаться
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            # Обновленное приветствие с жирным текстом
            first_name = user.first_name or "друг"
            last_name = user.last_name or ""
            if last_name:
                name = f"{first_name} {last_name}"
            else:
                name = first_name
                
            welcome = f"""<b>👋 Привет, друг/подруга {name}!</b>

<b>Добро пожаловать в Secret Link</b> — место, где ты можешь быстро и безопасно получить свой скрипт для Roblox.

<b>🔹 Что тебя ждёт:</b>
• <b>⚡️ Только лучшие скрипты</b> — без вирусов, рекламы и переходников  
• <b>🛡 Проверены вручную</b> — гарантированная безопасность
• <b>🔁 Постоянные обновления</b> — всё актуально и стабильно работает

<b>❗️ Важно:</b>  
Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт 🚀»

Для сотрудничества: @SecretLinkAds"""

            keyboard = [
                [InlineKeyboardButton("🔥 Наш канал ", url="https://t.me/script_f")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(welcome, reply_markup=reply_markup, parse_mode="HTML")

# === Flyer интеграция ===

async def create_flyer_via_api(template_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if FLYER_API_KEY:
        headers["Authorization"] = f"Bearer {FLYER_API_KEY}"

    candidates = [
        f"{FLYER_API_URL}/v1/flyers",
        f"{FLYER_API_URL}/flyers",
        f"{FLYER_API_URL}/create",
        f"{FLYER_API_URL}/api/flyers",
        f"{FLYER_API_URL}/api/v1/flyers",
    ]

    body = {
        "template": template_id,
        "data": payload
    }

    async with aiohttp.ClientSession() as session:
        last_exc = None
        for endpoint in candidates:
            try:
                async with session.post(endpoint, json=body, headers=headers, timeout=30) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 201):
                        last_exc = RuntimeError(f"{resp.status} {text}")
                        continue
                    try:
                        data = await resp.json()
                    except Exception:
                        return {"raw": text}
                    return data
            except Exception as e:
                last_exc = e
                continue
        raise RuntimeError(f"Не удалось связаться с Flyer API: {last_exc}")

async def flyer_create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ У вас нет доступа.")
        return

    if not context.args:
        await update.message.reply_text("❌ Использование: /flyer_create <template_id> [chat_id]")
        return

    template_id = context.args[0]
    target_chat = None
    if len(context.args) > 1:
        try:
            target_chat = int(context.args[1])
        except:
            target_chat = None

    payload = {
        "title": "Рекламный флаер",
        "subtitle": "Генерирован ботом"
    }

    try:
        resp = await create_flyer_via_api(template_id, payload)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка Flyer API: {e}")
        return

    image_url = None
    base64_data = None
    if isinstance(resp, dict):
        for key in ("url", "image_url", "result_url", "file_url"):
            if key in resp and isinstance(resp[key], str):
                image_url = resp[key]
                break
        if not image_url:
            data = resp.get("data") or resp.get("result")
            if isinstance(data, dict):
                for key in ("url", "image_url", "result_url", "file_url"):
                    if key in data and isinstance(data[key], str):
                        image_url = data[key]
                        break
            for key in ("file", "image_base64", "image"):
                if key in resp and isinstance(resp[key], str):
                    base64_data = resp[key]
                    break
            if not base64_data and isinstance(data, dict):
                for key in ("file", "image_base64", "image"):
                    if key in data and isinstance(data[key], str):
                        base64_data = data[key]
                        break

    if image_url:
        dest = target_chat or update.effective_user.id
        try:
            await context.bot.send_photo(chat_id=dest, photo=image_url, caption="Флаер от Flyer API")
            await update.message.reply_text("✅ Флаер создан и отправлен.")
            return
        except Exception as e:
            await update.message.reply_text(f"⚠️ Не удалось отправить по URL (попробуем другие варианты): {e}")

    if base64_data:
        try:
            if "," in base64_data and base64_data.startswith("data:"):
                base64_data = base64_data.split(",", 1)[1]
            binary = base64.b64decode(base64_data)
            bio = io.BytesIO(binary)
            bio.name = "flyer.png"
            dest = target_chat or update.effective_user.id
            await context.bot.send_photo(chat_id=dest, photo=InputFile(bio), caption="Флаер от Flyer API")
            await update.message.reply_text("✅ Флаер создан и отправлен (base64).")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при декодировании base64: {e}")
            return

    await update.message.reply_text(f"⚠️ Flyer вернул нестандартный ответ:\n<pre>{resp}</pre>", parse_mode="HTML")

async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user:
            user_ids.add(update.effective_user.id)
    except:
        pass

# === СОЗДАНИЕ ССЫЛОК И РАССЫЛКА ===

async def create_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    if not context.user_data.get("create_link_mode"):
        return
    context.user_data["create_link_mode"] = False

    length = random.randint(6, 25)
    safe_chars = string.ascii_letters + string.digits + "-"
    unique_code = ''.join(random.choices(safe_chars, k=length))
    while unique_code.startswith(('-', '_')) or unique_code.endswith(('-', '_')):
        unique_code = ''.join(random.choices(safe_chars, k=length))

    password = None
    raw_content = ""

    # Поддерживаем формат #password text (один # и слово пароля)
    def extract_password_and_text(s: str):
        s = s.strip()
        if s.startswith("#"):
            parts = s.split(None, 1)
            if len(parts) == 1:
                return parts[0][1:], ""
            else:
                return parts[0][1:], parts[1]
        return None, s

    if update.message.text:
        text = update.message.text
        password, raw_content = extract_password_and_text(text)
        saved_messages[unique_code] = {
            'type': 'text',
            'content': format_text_with_code_blocks(raw_content),
            'password': password
        }

    elif update.message.photo:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'photo',
            'content': update.message.photo[-1].file_id,
            'caption': caption,
            'password': password
        }

    elif update.message.video:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'video',
            'content': update.message.video.file_id,
            'caption': caption,
            'password': password
        }

    elif update.message.document:
        caption = update.message.caption or ""
        password, caption = extract_password_and_text(caption)
        saved_messages[unique_code] = {
            'type': 'document',
            'content': update.message.document.file_id,
            'caption': caption,
            'password': password
        }

    else:
        await update.message.reply_text("❌ Поддерживаются только текст, фото, видео и документы.")
        return

    link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
    await update.message.reply_text(
        f"✅ Уникальная ссылка создана!\n\n"
        f"🔗 <code>{link}</code>",
        parse_mode="HTML"
    )

async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    if not context.user_data.get("broadcast_mode"):
        return
    context.user_data["broadcast_mode"] = False
    success = 0
    failed = 0
    recipients = [uid for uid in user_ids if uid not in ADMIN_USER_IDS]
    if not recipients:
        await update.message.reply_text("❌ Нет получателей для рассылки.")
        return

    if update.message.text:
        raw_text = update.message.text.strip()
        if not raw_text:
            await update.message.reply_text("❌ Сообщение пустое.")
            return
        formatted_text = format_text_with_code_blocks(raw_text)
        message_text, buttons = parse_message_with_buttons(formatted_text)
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        for user_id in recipients:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
                success += 1
            except Exception as e:
                failed += 1
                if "Forbidden" in str(e):
                    user_ids.discard(user_id)

    elif update.message.photo or update.message.video or update.message.document:
        caption = update.message.caption or ""
        formatted_caption = format_text_with_code_blocks(caption)
        message_text, buttons = parse_message_with_buttons(formatted_caption)
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        for user_id in recipients:
            try:
                if update.message.photo:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=update.message.photo[-1].file_id,
                        caption=message_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                elif update.message.video:
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=update.message.video.file_id,
                        caption=message_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                elif update.message.document:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=update.message.document.file_id,
                        caption=message_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                success += 1
            except Exception as e:
                failed += 1
                if "Forbidden" in str(e):
                    user_ids.discard(user_id)
    else:
        await update.message.reply_text("❌ Поддерживаются только текст, фото, видео и документы.")
        return

    await update.message.reply_text(
        f"✅ Рассылка завершена!\n"
        f"Доставлено: {success}\n"
        f"Ошибок: {failed}"
    )

async def handle_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "del_all":
        count = len(active_campaigns)
        active_campaigns.clear()
        await query.edit_message_text(f"✅ Удалено {count} проверок.")
    elif data.startswith("del_"):
        try:
            chat_id = int(data.split("_", 1)[1])
            if chat_id in active_campaigns:
                del active_campaigns[chat_id]
                await query.edit_message_text(f"✅ Проверка для {chat_id} удалена.")
            else:
                await query.edit_message_text("⚠️ Проверка уже удалена.")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")

# === ЗАПУСК ===

def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN", "8549573387:AAGJynndMV16Z_Rr0YgbnTd6nWahzkw221g")
    SUBGRAM_API_KEY_ENV = os.getenv("SUBGRAM_API_KEY", "")
    
    # Обновляем API ключ SubGram из переменной окружения
    global SUBGRAM_API_KEY
    if SUBGRAM_API_KEY_ENV:
        SUBGRAM_API_KEY = SUBGRAM_API_KEY_ENV
    
    application = Application.builder().token(TOKEN).build()

    # Трекинг всех пользователей
    application.add_handler(MessageHandler(filters.ALL, track_user), group=-1)

    # Основные команды
    application.add_handler(CommandHandler("start", start_with_code))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("setup", setup_command))
    application.add_handler(CommandHandler("flyer_create", flyer_create_command))
    application.add_handler(CommandHandler("cancel", cancel_upload))

    # Callback handlers - УБРАЛИ "show_all_channels" и "copy_" шаблоны
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^check_sub$|^cancel_"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(handle_deletion, pattern=r"^(del_all|del_-?\d+)$"))
    application.add_handler(CallbackQueryHandler(admin_upload_handler, pattern="^admin_upload_"))

    # Обработка групповых сообщений ТОЛЬКО в разрешенной группе
    application.add_handler(MessageHandler(filters.Chat(ALLOWED_GROUP_ID), handle_group_message), group=0)
    
    # Загрузка через админку
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_admin_upload), group=1)
    
    # Создание ссылок и рассылка
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL, create_link_handler), group=2)
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL, broadcast_handler), group=3)

    print(f"✅ Бот запущен...")
    print(f"📌 Работает только в группе ID: {ALLOWED_GROUP_ID}")
    print(f"🔗 SubGram API: {'✅ Настроен' if SUBGRAM_API_KEY and SUBGRAM_API_KEY != 'ВАШ_API_КЛЮЧ_БОТА' else '❌ Нужен API ключ'}")
    
    application.run_polling()

if __name__ == "__main__":
    main()
