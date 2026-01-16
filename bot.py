import logging
import re
import random
import string
import json
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import BadRequest
import sqlite3

# === НАСТРОЙКИ ===

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

ADMIN_USER_IDS = {8523456846, 5870949629}
MAX_CAMPAIGNS = 15
MAX_MEMBER_LIMIT = 50000
BOT_USERNAME = "EpiLink_Bot"

# === FLYER API ===
FLYER_API_KEY = "FL-fCmzVf-QyBeLi-xYlScV-gkcahf"  # ⚠️ ЗАМЕНИТЕ НА СВОЙ КЛЮЧ
FLYER_ENABLED = bool(FLYER_API_KEY)

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ===

DB_PATH = Path("bot.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            chat_id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_messages (
            code TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def load_from_db():
    global user_ids, active_campaigns, saved_messages
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id FROM users")
    user_ids = {row[0] for row in cursor.fetchall()}

    cursor.execute("SELECT chat_id, data FROM campaigns")
    active_campaigns = {}
    for chat_id, data_str in cursor.fetchall():
        try:
            data = json.loads(data_str)
            if data.get('expires_at'):
                data['expires_at'] = datetime.fromisoformat(data['expires_at'])
            if data.get('start_time'):
                data['start_time'] = datetime.fromisoformat(data['start_time'])
            active_campaigns[int(chat_id)] = data
        except Exception as e:
            logging.error(f"Ошибка загрузки кампании {chat_id}: {e}")

    cursor.execute("SELECT code, data FROM saved_messages")
    saved_messages = {}
    for code, data_str in cursor.fetchall():
        try:
            saved_messages[code] = json.loads(data_str)
        except Exception as e:
            logging.error(f"Ошибка загрузки сообщения {code}: {e}")

    conn.close()

def save_user_to_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def save_campaign_to_db(chat_id: int, data: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    safe_data = data.copy()
    if safe_data.get('expires_at'):
        safe_data['expires_at'] = safe_data['expires_at'].isoformat()
    if safe_data.get('start_time'):
        safe_data['start_time'] = safe_data['start_time'].isoformat()
    cursor.execute("INSERT OR REPLACE INTO campaigns (chat_id, data) VALUES (?, ?)",
                   (str(chat_id), json.dumps(safe_data, ensure_ascii=False)))
    conn.commit()
    conn.close()

def delete_campaign_from_db(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM campaigns WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    conn.close()

def delete_all_campaigns_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM campaigns")
    conn.commit()
    conn.close()

def save_message_to_db(code: str, data: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO saved_messages (code, data) VALUES (?, ?)",
                   (code, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()

# === ХРАНИЛИЩА (будут загружены из БД) ===

user_ids = set()
active_campaigns = {}
saved_messages = {}

# === ФОРМАТИРОВАНИЕ ТЕКСТА ===

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
            result.append(f"<code>{code_content}</code>")
        else:
            safe_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            result.append(safe_line)
    return '\n'.join(result)

# === FLYER API ФУНКЦИЯ ===

async def check_flyer_subscription(user_id: int, language_code: str = "ru") -> dict:
    if not FLYER_ENABLED:
        return {"skip": True}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.flyerservice.io/check-subscription",
                json={
                    "key": FLYER_API_KEY,
                    "user_id": user_id,
                    "language_code": language_code
                }
            ) as resp:
                return await resp.json()
    except Exception as e:
        logging.error(f"Ошибка Flyer API: {e}")
        return {"error": str(e)}

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

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

async def get_unsubscribed_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    unsubscribed = []
    for chat_id in list(active_campaigns.keys()):
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                unsubscribed.append(chat_id)
        except BadRequest as e:
            logging.warning(f"Ошибка проверки {chat_id}: {e}")
            if "User not found" in str(e) or "chat not found" in str(e):
                unsubscribed.append(chat_id)
        except Exception as e:
            logging.warning(f"Ошибка проверки {chat_id}: {e}")
            unsubscribed.append(chat_id)
    return unsubscribed

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
    for chat_id, data in active_campaigns.items():
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
            delete_campaign_from_db(cid)

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

# === СТАТУС ПРОВЕРОК ===

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
                block += f"\n⚠️ КАМПАНИЯ ЗАВЕРШЕНА ({reason})"
            status_lines.append(block)
        status = "\n\n" + "\n\n".join(status_lines) + "\n"

    flyer_info = "\nℹ️ Flyer API: " + ("включён" if FLYER_ENABLED else "отключён")
    return status + flyer_info

# === ОБРАБОТЧИКИ ===

async def start_with_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    user_ids.add(user_id)
    save_user_to_db(user_id)
    await cleanup_expired_campaigns(context)

    if FLYER_ENABLED:
        response = await check_flyer_subscription(user_id=user_id, language_code=update.effective_user.language_code or "ru")
        if response.get("skip"):
            pass  # Пропускаем обязательную проверку
        elif response.get("error"):
            logging.warning(f"Flyer API ошибка: {response.get('error')}. Продолжаем локальную проверку.")

    unsubscribed = await get_unsubscribed_channels(user_id, context)
    if unsubscribed:
        buttons = []
        for i in range(0, len(unsubscribed), 2):
            row = []
            if i < len(unsubscribed):
                chat_id = unsubscribed[i]
                link = active_campaigns[chat_id]['link']
                row.append(InlineKeyboardButton("🔺 Подписаться", url=link))
            if i + 1 < len(unsubscribed):
                chat_id = unsubscribed[i + 1]
                link = active_campaigns[chat_id]['link']
                row.append(InlineKeyboardButton("🔺 Подписаться", url=link))
            buttons.append(row)
        buttons.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")])
        reply_markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            "❕ | Прежде чем пользоваться ботом, подпишись на указанные каналы ниже!\n\n"
            "⚠️ Подпишитесь на все каналы\n\n"
            "❕ Нажмите по кнопкам ниже, затем проверьте подписку.",
            reply_markup=reply_markup
        )
        return

    if context.args:
        code = context.args[0]
        if code in saved_messages:
            data = saved_messages[code]
            try:
                if data['type'] == 'text':
                    await update.message.reply_text(data['content'], parse_mode="HTML")
                elif data['type'] == 'photo':
                    await update.message.reply_photo(photo=data['content'], caption=data.get('caption', ''), parse_mode="HTML")
                elif data['type'] == 'video':
                    await update.message.reply_video(video=data['content'], caption=data.get('caption', ''), parse_mode="HTML")
                elif data['type'] == 'document':
                    await update.message.reply_document(document=data['content'], caption=data.get('caption', ''), parse_mode="HTML")
                return
            except Exception as e:
                logging.error(f"Ошибка отправки сохранённого сообщения: {e}")
    await start(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await cleanup_expired_campaigns(context)
    await show_subscription_prompt_inplace(update, context)

async def show_subscription_prompt_inplace(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    user_ids.add(user_id)
    save_user_to_db(user_id)
    unsubscribed = await get_unsubscribed_channels(user_id, context)

    if not active_campaigns or not unsubscribed:
        welcome = (
            "👋 Привет, друг!\n\n"
            "Добро пожаловать в бот от Roblox Scripts — твоего надёжного источника скриптов для Roblox!\n\n"
            "Что тебя ждёт:\n"
            "• ⚡️ Топовые скрипты — без вирусов, рекламы и переходников\n"
            "• 🔒 Ручная проверка — только безопасный и стабильный софт\n"
            "• ♻️ Ежедневные обновления — всё всегда актуально\n\n"
            "❗️ Важно: \n"
            "Все скрипты публикуются только в наших Telegram-каналах. Подписывайся, чтобы не пропустить свежие читы и обновления!\n\n"
            "• По поводу сотрудничества: @nikitos_adsll\n\n"
            "✅ Играй с умом:\n"
            "Наслаждайся возможностями, но не нарушай правила Roblox и не забывай о безопасности!"
        )
        keyboard = [[InlineKeyboardButton("🔥 Наш канал", url="https://t.me/script_f")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.message.edit_text(welcome, reply_markup=reply_markup)
        else:
            await update.effective_message.reply_text(welcome, reply_markup=reply_markup)
        return

    buttons = []
    for i in range(0, len(unsubscribed), 2):
        row = []
        if i < len(unsubscribed):
            chat_id = unsubscribed[i]
            link = active_campaigns[chat_id]['link']
            row.append(InlineKeyboardButton("🔺 Подписаться", url=link))
        if i + 1 < len(unsubscribed):
            chat_id = unsubscribed[i + 1]
            link = active_campaigns[chat_id]['link']
            row.append(InlineKeyboardButton("🔺 Подписаться", url=link))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")])
    reply_markup = InlineKeyboardMarkup(buttons)
    text = message_text or (
        "❕ | Прежде чем пользоваться ботом, подпишись на указанные каналы ниже!\n\n"
        "⚠️ Подпишитесь на все каналы\n\n"
        "❕ Нажмите по кнопкам ниже, затем проверьте подписку."
    )
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup)

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

        if FLYER_ENABLED:
            response = await check_flyer_subscription(user_id=user_id, language_code=query.from_user.language_code or "ru")
            if response.get("skip"):
                pass
            elif response.get("error"):
                logging.warning(f"Flyer API ошибка: {response.get('error')}. Продолжаем локальную проверку.")

        unsubscribed = await get_unsubscribed_channels(user_id, context)
        if unsubscribed:
            channel_list = ""
            for chat_id in unsubscribed[:5]:
                try:
                    chat = await context.bot.get_chat(chat_id)
                    title = chat.title or chat.username or f"Канал {chat_id}"
                    channel_list += f"• {title}\n"
                except:
                    channel_list += f"• Канал {chat_id}\n"
            if len(unsubscribed) > 5:
                channel_list += f"• ... и ещё {len(unsubscribed) - 5} каналов\n"
            await show_subscription_prompt_inplace(
                update, context,
                message_text=f"❌ Вы не подписаны на все каналы!\n\n"
                           f"Не подписаны на:\n{channel_list}\n"
                           f"Пожалуйста, подпишитесь на все каналы и нажмите «Проверить подписку»."
            )
        else:
            welcome = (
                "✅ Отлично! Вы подписаны на все каналы!\n\n"
                "👋 Привет, друг!\n\n"
                "Добро пожаловать в бот от Roblox Scripts — твоего надёжного источника скриптов для Roblox!\n\n"
                "Что тебя ждёт:\n"
                "• ⚡️ Топовые скрипты — без вирусов, рекламы и переходников\n"
                "• 🔒 Ручная проверка — только безопасный и стабильный софт\n"
                "• ♻️ Ежедневные обновления — всё всегда актуально\n\n"
                "❗️ Важно: \n"
                "Все скрипты публикуются только в наших Telegram-каналах. Подписывайся, чтобы не пропустить свежие читы и обновления!\n\n"
                "• По поводу сотрудничества: @nikitos_ads\n\n"
                "✅ Играй с умом:\n"
                "Наслаждайся возможностями, но не нарушай правила Roblox и не забывай о безопасности!"
            )
            keyboard = [[InlineKeyboardButton("🔥 Наш канал", url="https://t.me/script_f")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(welcome, reply_markup=reply_markup)

# === АДМИНКА ===

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
        [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔗 Создать ссылку", callback_data="admin_create_link")],
    ]
    await update.message.reply_text("🛠️ Панель управления администратора:", reply_markup=InlineKeyboardMarkup(keyboard))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    count = len(user_ids)
    await update.message.reply_text(f"📊 Всего пользователей в боте: {count:,}")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "admin_setup":
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
    elif data == "admin_broadcast":
        context.user_data["broadcast_mode"] = True
        keyboard = [[InlineKeyboardButton("✖️ Отменить", callback_data="cancel_broadcast")]]
        await query.edit_message_text(
            "📨 Отправьте сообщение для рассылки (текст, фото, видео и т.д.):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data == "admin_create_link":
        context.user_data["create_link_mode"] = True
        keyboard = [[InlineKeyboardButton("✖️ Отменить", callback_data="cancel_link")]]
        await query.edit_message_text(
            "📤 Отправьте сообщение (текст, фото, видео и т.д.), из которого нужно создать ссылку:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data == "admin_back":
        keyboard = [
            [InlineKeyboardButton("✅ Добавить проверку", callback_data="admin_setup")],
            [InlineKeyboardButton("🗑 Удалить проверку", callback_data="admin_unsetup")],
            [InlineKeyboardButton("📋 Статус проверок", callback_data="admin_status")],
            [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🔗 Создать ссылку", callback_data="admin_create_link")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🛠️ Панель управления администратора:", reply_markup=reply_markup)

async def handle_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "del_all":
        count = len(active_campaigns)
        active_campaigns.clear()
        delete_all_campaigns_from_db()
        await query.edit_message_text(f"✅ Удалено {count} проверок.")
    elif data.startswith("del_"):
        try:
            chat_id = int(data.split("_", 1)[1])
            if chat_id in active_campaigns:
                del active_campaigns[chat_id]
                delete_campaign_from_db(chat_id)
                await query.edit_message_text(f"✅ Проверка для {chat_id} удалена.")
            else:
                await query.edit_message_text("⚠️ Проверка уже удалена.")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")

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
        campaign_data = {
            'link': link,
            'expires_at': expires_at,
            'member_limit': member_limit,
            'start_time': datetime.now()
        }
        active_campaigns[chat_id] = campaign_data
        save_campaign_to_db(chat_id, campaign_data)
        if not expires_at and not member_limit:
            status = "навсегда"
        elif expires_at:
            mins = int(delta.total_seconds() // 60)
            status = f"до {expires_at.strftime('%Y-%m-%d %H:%M')} ({mins} мин)"
        else:
            status = f"до {member_limit} участников"
        await update.message.reply_text(f"✅ Проверка добавлена!\nID: {chat_id}\nСсылка: {link}\nДействует: {status}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}\n\nИспользуйте: /setup <chat_id> <ссылка> [время/лимит]\nПримеры:\n/setup -100123456 https://t.me/channel 30m\n/setup -100123456 https://t.me/channel 1")

# === РАССЫЛКА ===

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

# === СОЗДАНИЕ ССЫЛОК ===

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
    while unique_code.startswith(('-', '')) or unique_code.endswith(('-', '')):
        unique_code = ''.join(random.choices(safe_chars, k=length))
    if update.message.text:
        raw_text = update.message.text
        data = {
            'type': 'text',
            'content': format_text_with_code_blocks(raw_text)
        }
        saved_messages[unique_code] = data
        save_message_to_db(unique_code, data)
    elif update.message.photo:
        data = {
            'type': 'photo',
            'content': update.message.photo[-1].file_id,
            'caption': update.message.caption or ""
        }
        saved_messages[unique_code] = data
        save_message_to_db(unique_code, data)
    elif update.message.video:
        data = {
            'type': 'video',
            'content': update.message.video.file_id,
            'caption': update.message.caption or ""
        }
        saved_messages[unique_code] = data
        save_message_to_db(unique_code, data)
    elif update.message.document:
        data = {
            'type': 'document',
            'content': update.message.document.file_id,
            'caption': update.message.caption or ""
        }
        saved_messages[unique_code] = data
        save_message_to_db(unique_code, data)
    else:
        await update.message.reply_text("❌ Поддерживаются только текст, фото, видео и документы.")
        return
    link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
    await update.message.reply_text(
        f"✅ Уникальная ссылка создана!\n\n"
        f"🔗 <code>{link}</code>",
        parse_mode="HTML"
    )

# === ЗАПУСК ===

def main():
    TOKEN = "8584027906:AAEZvDcBZw-ugYDOKT6yOurh6vSS5fljpTY"
    init_db()
    load_from_db()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, lambda u, c: user_ids.add(u.effective_user.id) or save_user_to_db(u.effective_user.id)), group=-1)
    application.add_handler(CommandHandler("start", start_with_code))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("setup", setup_command))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^check_sub$|^cancel_"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(handle_deletion, pattern=r"^(del_all|del_-?\d+)$"))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL, create_link_handler), group=0)
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL, broadcast_handler), group=1)
    print("✅ Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
