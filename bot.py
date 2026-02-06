import asyncio
import logging
import aiohttp
import sqlite3
import os
import re
import random
import string
from datetime import datetime
import httpx
import json

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8549573387:AAG6oAmjI-w8niZScnzNz42OX_5tiHnLw_k"
SUBGRAM_API_KEY = "f5d4e6567b52e995ebf408cb75ac22740e25c9a02a0427941386c97e8843e891"
SUBGRAM_URL = "https://api.subgram.org/get-sponsors"

# Tgrass API настройки
TGRASS_API_URL = "https://tgrass.space/offers"
TGRASS_API_KEY = "dd20d4a36f0e43b381194d7b5698dad6"

# Flyer API настройки
FLYER_API_BASE = "api.flyerservice.io"  # Замените на реальный базовый URL
FLYER_API_KEY = "FL-ElINbD-pwBXBA-BZpBwY-DFIgFF"  # Замените на ваш ключ Flyer

CHANNEL_URL = "https://t.me/script_f"
ADMIN_ID = 5870949629
BOT_USERNAME = "LinksSecret_Bot"

# Настройки для группы/топика
GROUP_ID = -1001897612345  # Замените на ID вашей группы (должно быть отрицательным числом)
TOPIC_ID = 2  # ID топика, где будут отправляться скрипты

# Для упрощения вместо БД используется список
ALREADY_CHECKED_MESSAGES = []

# Хранилище для Flyer задач (task_id -> signature)
FLYER_TASKS = {}

# ===============================================

logging.basicConfig(level=logging.INFO)
router = Router()

# Хранилище пользователей
USERS = set()
# Хранилище состояний загрузки скриптов
UPLOADING_USERS = set()
# Хранилище для специальных скриптов (ссылкой)
SPECIAL_UPLOADING_USERS = set()
# Хранилище для данных рассылки
broadcast_data = {}
broadcast_buttons = {}

# FSM состояния для рассылки
class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_buttons = State()

# ================== FLYER ФУНКЦИИ ==================

async def get_flyer_tasks(user_id: int, language_code: str = "ru", limit: int = 10) -> dict | None:
    """Получает задания от Flyer"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.post(
                f"{FLYER_API_BASE}/get_tasks",
                json={
                    "key": FLYER_API_KEY,
                    "user_id": int(user_id),
                    "language_code": language_code or "ru",
                    "limit": limit
                },
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                return data
            else:
                logging.error(f"Flyer API error: {response.status_code}, {response.text}")
                return None
    except asyncio.TimeoutError:
        logging.error("Flyer API timeout")
        return None
    except Exception as e:
        logging.error(f"Flyer API error: {e}")
        return None

async def check_flyer_task(signature: str) -> dict | None:
    """Проверяет статус задания Flyer"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.post(
                f"{FLYER_API_BASE}/check_task",
                json={
                    "key": FLYER_API_KEY,
                    "signature": signature
                },
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"Flyer check task error: {response.status_code}, {response.text}")
                return None
    except asyncio.TimeoutError:
        logging.error("Flyer check task timeout")
        return None
    except Exception as e:
        logging.error(f"Flyer check task error: {e}")
        return None

async def get_flyer_completed_tasks(user_id: int) -> dict | None:
    """Получает выполненные задания Flyer"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.post(
                f"{FLYER_API_BASE}/get_completed_tasks",
                json={
                    "key": FLYER_API_KEY,
                    "user_id": int(user_id)
                },
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"Flyer completed tasks error: {response.status_code}, {response.text}")
                return None
    except asyncio.TimeoutError:
        logging.error("Flyer completed tasks timeout")
        return None
    except Exception as e:
        logging.error(f"Flyer completed tasks error: {e}")
        return None

def create_flyer_keyboard(tasks_data):
    """Создание клавиатуры с заданиями Flyer"""
    tasks = tasks_data.get("result", [])
    keyboard = []
    
    for task in tasks:
        if isinstance(task, dict):
            # Предполагаем структуру задания
            url = task.get("url", task.get("link", "#"))
            name = task.get("name", task.get("title", "Задание"))
            task_id = task.get("id", task.get("signature", ""))
            
            # Сохраняем связь для последующей проверки
            if task_id:
                button_id = f"flyer_{task_id}"
                FLYER_TASKS[button_id] = {
                    "signature": task_id,
                    "url": url,
                    "name": name
                }
                
                button = InlineKeyboardButton(
                    text=name,
                    url=url
                )
                keyboard.append([button])
    
    if keyboard:  # Если есть задания
        keyboard.append([
            InlineKeyboardButton(
                text="✅ Проверить задания Flyer",
                callback_data="check_flyer_all"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================== TGRASS ФУНКЦИИ ==================

async def get_tgrass_offers(user_id: int, username: str = None, lang: str = "ru", is_premium: bool = False) -> dict | None:
    """Получает задания от Tgrass"""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            response = await client.post(
                TGRASS_API_URL,
                json={
                    "tg_user_id": int(user_id),
                    "tg_login": username or "",
                    "lang": lang or "ru",
                    "is_premium": is_premium,
                },
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "Auth": TGRASS_API_KEY,
                },
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logging.error(f"Tgrass API error: {response.status_code}")
                return None
    except asyncio.TimeoutError:
        logging.error("Tgrass API timeout")
        return None
    except Exception as e:
        logging.error(f"Tgrass API error: {e}")
        return None

def create_tgrass_keyboard(offers_data):
    """Создание клавиатуры с каналами для подписки от Tgrass"""
    offers = offers_data.get("offers", [])
    keyboard = []
    
    for offer in offers:
        button = InlineKeyboardButton(
            text="Подписаться" if offer.get("type") == "channel" else "Перейти",
            url=offer.get("link", "#")
        )
        keyboard.append([button])
    
    keyboard.append([
        InlineKeyboardButton(
            text="✅ Проверить выполнение",
            callback_data="check_tgrass"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

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
                    logging.error(f"SubGram API error: {response.status}")
                    return None
    except asyncio.TimeoutError:
        logging.error("SubGram API timeout")
        return None
    except Exception as e:
        logging.error(f"SubGram API error: {e}")
        return None

def create_subgram_keyboard(sponsors_data):
    """Создание клавиатуры с каналами для подписки от SubGram"""
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

# ================== КОМБИНИРОВАННАЯ ПРОВЕРКА ПОДПИСОК ==================

async def check_all_subscriptions(user_id: int, username: str = None, lang: str = "ru", is_premium: bool = False):
    """Проверяет подписки через все три сервиса (SubGram, Tgrass и Flyer)"""
    results = {
        "subgram": None,
        "tgrass": None,
        "flyer": None,
        "all_passed": False
    }
    
    # Проверяем SubGram
    results["subgram"] = await get_subgram_sponsors(user_id, user_id)
    
    # Проверяем Tgrass
    results["tgrass"] = await get_tgrass_offers(user_id, username, lang, is_premium)
    
    # Проверяем Flyer
    results["flyer"] = await get_flyer_tasks(user_id, lang, limit=10)
    
    # Определяем, прошли ли все проверки
    subgram_passed = results["subgram"] is None or results["subgram"].get("status") != "warning"
    tgrass_passed = results["tgrass"] is None or (results["tgrass"].get("status") != "not_ok" and results["tgrass"].get("status") != "warning")
    flyer_passed = True  # По умолчанию считаем, что Flyer пройден, если нет ошибок
    
    # Если Flyer вернул задания, значит еще не все выполнены
    if results["flyer"] and results["flyer"].get("result") and len(results["flyer"].get("result", [])) > 0:
        flyer_passed = False
    
    results["all_passed"] = subgram_passed and tgrass_passed and flyer_passed
    
    return results

def create_combined_keyboard(subgram_data, tgrass_data, flyer_data):
    """Создает комбинированную клавиатуру с заданиями от всех трех сервисов"""
    keyboard = []
    has_tasks = False
    
    # Добавляем каналы из SubGram
    if subgram_data and subgram_data.get("status") == "warning":
        has_tasks = True
        sponsors = subgram_data.get("sponsors", [])
        for sponsor in sponsors:
            button = InlineKeyboardButton(
                text=f"📢 {sponsor.get('name', 'Канал')}",
                url=sponsor.get("url", "#")
            )
            keyboard.append([button])
    
    # Добавляем задания из Tgrass
    if tgrass_data and tgrass_data.get("status") == "not_ok":
        has_tasks = True
        offers = tgrass_data.get("offers", [])
        for offer in offers:
            button_text = "🔗 Подписаться" if offer.get("type") == "channel" else "🔗 Перейти"
            button = InlineKeyboardButton(
                text=button_text,
                url=offer.get("link", "#")
            )
            keyboard.append([button])
    
    # Добавляем задания из Flyer
    if flyer_data and flyer_data.get("result") and len(flyer_data.get("result", [])) > 0:
        has_tasks = True
        tasks = flyer_data.get("result", [])
        for task in tasks:
            if isinstance(task, dict):
                name = task.get("name", task.get("title", "Задание"))
                url = task.get("url", task.get("link", "#"))
                
                button = InlineKeyboardButton(
                    text=f"🚀 {name}",
                    url=url
                )
                keyboard.append([button])
    
    # Добавляем кнопки проверки для всех сервисов
    if has_tasks:
        keyboard.append([
            InlineKeyboardButton(
                text="✅ Проверить все задания",
                callback_data="check_all_subscriptions"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================== ОБРАБОТКА ПРОВЕРОК ==================

@router.callback_query(F.data == "check_all_subscriptions")
async def check_all_subscriptions_callback(callback: types.CallbackQuery):
    """Проверка всех подписок"""
    await callback.answer("⏳ Проверяем все задания...")
    
    try:
        await callback.message.delete()
    except:
        pass
    
    results = await check_all_subscriptions(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        lang=callback.from_user.language_code,
        is_premium=callback.from_user.is_premium
    )
    
    # Если есть ошибки во всех сервисах, показываем их
    if not results["all_passed"]:
        error_messages = []
        has_tasks = False
        
        if results["subgram"] and results["subgram"].get("status") == "warning":
            error_messages.append("SubGram: ❌ Вы не подписались на все каналы")
            has_tasks = True
        
        if results["tgrass"] and results["tgrass"].get("status") == "not_ok":
            error_messages.append("Tgrass: ❌ Вы не выполнили все задания")
            has_tasks = True
        
        if results["flyer"] and results["flyer"].get("result") and len(results["flyer"].get("result", [])) > 0:
            error_messages.append("Flyer: ❌ Вы не выполнили все задания")
            has_tasks = True
        
        if has_tasks:
            error_text = "❌ Проверка не пройдена:\n\n"
            error_text += "\n".join(error_messages)
            
            keyboard = create_combined_keyboard(
                results["subgram"], 
                results["tgrass"], 
                results["flyer"]
            )
            
            await callback.message.answer(
                error_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
    
    # Если все проверки пройдены
    await send_welcome(callback.message)

# ================== FLYER ОБРАБОТЧИКИ ==================

@router.callback_query(F.data == "check_flyer_all")
async def check_flyer_all_callback(callback: types.CallbackQuery):
    """Проверка всех заданий Flyer"""
    await callback.answer("⏳ Проверяем задания Flyer...")
    
    # Проверяем выполненные задания
    completed_tasks = await get_flyer_completed_tasks(callback.from_user.id)
    
    if completed_tasks:
        count_all = completed_tasks.get("result", {}).get("count_all_tasks", 0)
        completed = completed_tasks.get("result", {}).get("completed_tasks", [])
        completed_count = len(completed) if isinstance(completed, list) else 0
        
        if completed_count >= count_all:
            await callback.message.answer("✅ Все задания Flyer выполнены!")
            
            # Проверяем другие сервисы
            results = await check_all_subscriptions(
                user_id=callback.from_user.id,
                username=callback.from_user.username,
                lang=callback.from_user.language_code,
                is_premium=callback.from_user.is_premium
            )
            
            if results["all_passed"]:
                await send_welcome(callback.message)
            else:
                keyboard = create_combined_keyboard(
                    results["subgram"], 
                    results["tgrass"], 
                    results["flyer"]
                )
                await callback.message.answer(
                    "✅ Flyer пройден!\n\nНо есть другие задания:",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
        else:
            # Получаем текущие задания
            flyer_tasks = await get_flyer_tasks(callback.from_user.id, callback.from_user.language_code)
            
            if flyer_tasks and flyer_tasks.get("result"):
                keyboard = create_flyer_keyboard(flyer_tasks)
                await callback.message.answer(
                    f"❌ Выполнено {completed_count}/{count_all} заданий Flyer\n\nПродолжайте выполнять задания:",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
    else:
        await callback.message.answer("❌ Не удалось проверить задания Flyer")

# ================== TGRASS КОМАНДА ==================

@router.message(Command("tasks"))
async def tasks_command_handler(message: types.Message):
    """Обработка команды /tasks - показывает задания Tgrass"""
    tgrass_response = await get_tgrass_offers(
        user_id=message.from_user.id,
        username=message.from_user.username,
        lang=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if tgrass_response and tgrass_response.get("status") == "not_ok":
        keyboard = create_tgrass_keyboard(tgrass_response)
        msg = await message.answer(
            reply_markup=keyboard,
            text="Выполнить задание",
        )
    else:
        await message.answer(
            text="На данный момент нет доступных заданий",
        )

@router.callback_query(lambda c: c.data == "check_tgrass")
async def check_tgrass_handler(callback_query: types.CallbackQuery):
    """Проверка выполнения задания Tgrass"""
    await callback_query.answer()
    
    tgrass_response = await get_tgrass_offers(
        user_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        lang=callback_query.from_user.language_code,
        is_premium=callback_query.from_user.is_premium
    )
    
    # Пользователь выполнил задание, наградить
    if tgrass_response and tgrass_response.get("status") == "ok":
        await callback_query.message.answer(text="✅ Задание успешно выполнено!")
        
        # Награждаем юзера если он не был ранее награжден
        if callback_query.message.message_id not in ALREADY_CHECKED_MESSAGES:
            ALREADY_CHECKED_MESSAGES.append(callback_query.message.message_id)
            # Логика для награждения
            await send_coins_to_user(callback_query)
    else:
        await callback_query.message.answer(text="❌ Задание не выполнено!")

async def send_coins_to_user(callback_query: types.CallbackQuery):
    """Функция для награждения пользователя"""
    # Здесь можно добавить логику награждения (например, начисление валюты в вашей системе)
    await callback_query.message.answer(
        "🎉 Вы получили награду за выполнение задания!",
        parse_mode="HTML"
    )

# ================== SubGram ОБРАБОТКА ==================

@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    """Проверка подписки только для SubGram"""
    await callback.answer("⏳ Проверяем подписку...")
    
    try:
        await callback.message.delete()
    except:
        pass
    
    response = await get_subgram_sponsors(callback.from_user.id, callback.message.chat.id)
    
    if response and response.get("status") == "warning":
        keyboard = create_subgram_keyboard(response)
        warning_message = "❌ Вы еще не подписались на все каналы!\n\n❗ Чтобы получить доступ к боту, подпишитесь на следующие каналы:"
        
        await callback.message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    # После проверки SubGram проверяем другие сервисы
    results = await check_all_subscriptions(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        lang=callback.from_user.language_code,
        is_premium=callback.from_user.is_premium
    )
    
    if not results["all_passed"]:
        keyboard = create_combined_keyboard(
            results["subgram"], 
            results["tgrass"], 
            results["flyer"]
        )
        
        # Формируем сообщение о том, что осталось сделать
        message_text = "✅ SubGram проверка пройдена!\n\n"
        
        if results["tgrass"] and results["tgrass"].get("status") == "not_ok":
            message_text += "Теперь необходимо выполнить задания Tgrass:\n"
        elif results["flyer"] and results["flyer"].get("result") and len(results["flyer"].get("result", [])) > 0:
            message_text += "Теперь необходимо выполнить задания Flyer:\n"
        else:
            message_text += "Выполните оставшиеся задания:\n"
        
        await callback.message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    await send_welcome(callback.message)

# ================== БАЗА ДАННЫХ ДЛЯ ССЫЛОК И СОЗДАТЕЛЕЙ ==================

def init_database():
    """Инициализация базы данных для скриптов и создателей"""
    conn = None
    try:
        if os.path.exists('scripts.db'):
            try:
                conn = sqlite3.connect('scripts.db')
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(scripts)")
                columns = [col[1] for col in cursor.fetchall()]
                
                # Проверяем наличие нужных столбцов
                needed_columns = ['is_special', 'link_url']
                columns_to_add = []
                
                for column in needed_columns:
                    if column not in columns:
                        columns_to_add.append(column)
                
                if columns_to_add:
                    print(f"Добавляем отсутствующие столбцы: {columns_to_add}")
                    for column in columns_to_add:
                        if column == 'is_special':
                            cursor.execute('ALTER TABLE scripts ADD COLUMN is_special BOOLEAN DEFAULT 0')
                        elif column == 'link_url':
                            cursor.execute('ALTER TABLE scripts ADD COLUMN link_url TEXT DEFAULT NULL')
                    conn.commit()
                    print("База данных обновлена с новыми полями")
                else:
                    print("База данных в порядке")
                    
            except Exception as e:
                print(f"Ошибка при проверке базы данных: {e}")
                if conn:
                    conn.close()
                if os.path.exists('scripts.db'):
                    os.remove('scripts.db')
                conn = sqlite3.connect('scripts.db')
                cursor = conn.cursor()
        else:
            conn = sqlite3.connect('scripts.db')
            cursor = conn.cursor()
        
        # Создаем таблицы если их нет
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unique_code TEXT UNIQUE NOT NULL,
            script_content TEXT NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_public BOOLEAN DEFAULT 0,
            is_special BOOLEAN DEFAULT 0,
            link_url TEXT DEFAULT NULL,
            original_message_id INTEGER
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS script_creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
        ''')
        
        # Добавляем админа если его нет
        cursor.execute('SELECT 1 FROM script_creators WHERE user_id = ?', (ADMIN_ID,))
        if not cursor.fetchone():
            cursor.execute('INSERT OR IGNORE INTO script_creators (user_id, username, full_name) VALUES (?, ?, ?)',
                          (ADMIN_ID, 'admin', 'Главный Администратор'))
        
        conn.commit()
        print("База данных инициализирована")
        
    except Exception as e:
        print(f"Критическая ошибка при инициализации базы данных: {e}")
        raise
    finally:
        if conn:
            conn.close()

init_database()

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С СОЗДАТЕЛЯМИ ==================

def is_script_creator(user_id: int) -> bool:
    """Проверяет, является ли пользователь создателем скриптов"""
    if user_id == ADMIN_ID:
        return True
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM script_creators WHERE user_id = ? AND is_active = 1', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    
    return result is not None

def add_script_creator(user_id: int, username: str = None, full_name: str = None) -> bool:
    """Добавляет пользователя в создатели скриптов"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
        INSERT OR REPLACE INTO script_creators (user_id, username, full_name, is_active)
        VALUES (?, ?, ?, 1)
        ''', (user_id, username, full_name))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Error adding script creator: {e}")
        conn.close()
        return False

def remove_script_creator(user_id: int) -> bool:
    """Удаляет пользователя из создателей скриптов"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM script_creators WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Error removing script creator: {e}")
        conn.close()
        return False

def get_all_script_creators():
    """Получает список всех создателей скриптов"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT user_id, username, full_name, created_at, is_active 
    FROM script_creators 
    ORDER BY created_at DESC
    ''')
    
    creators = cursor.fetchall()
    conn.close()
    
    return creators

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==================

def generate_unique_code():
    """Генерация уникального кода для ссылки от 7 до 25 символов"""
    length = random.randint(7, 25)
    characters = string.ascii_letters + string.digits + "-"
    code = ''.join(random.choice(characters) for _ in range(length))
    return code

def save_script_to_db(script_content: str, created_by: int, is_public=True, is_special=False, link_url=None, original_message_id=None):
    """Сохранение скрипта в базу данных и возврат уникального кода"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    while True:
        unique_code = generate_unique_code()
        cursor.execute('SELECT 1 FROM scripts WHERE unique_code = ?', (unique_code,))
        if not cursor.fetchone():
            break
    
    # Для специальных скриптов (ссылкой) используем фиксированный текст
    if is_special:
        # Сохраняем текст как на второй фотографии
        script_content = "<b>Ваш скрипт ⬇️</b>"
    
    cursor.execute('''
    INSERT INTO scripts (unique_code, script_content, created_by, is_public, is_special, link_url, original_message_id, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    ''', (unique_code, script_content, created_by, 1 if is_public else 0, 1 if is_special else 0, link_url, original_message_id))
    
    conn.commit()
    conn.close()
    
    return unique_code

def get_script_content(unique_code):
    """Получение скрипта по уникальному коду"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT script_content, is_special, link_url FROM scripts WHERE unique_code = ?
    ''', (unique_code,))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'content': result[0],
            'is_special': result[1],
            'link_url': result[2]
        }
    return None

def get_statistics():
    """Получение статистики"""
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM scripts")
        total_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by = ?", (ADMIN_ID,))
        admin_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE created_by != ?", (ADMIN_ID,))
        user_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE is_public = 1")
        public_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scripts WHERE is_special = 1")
        special_scripts = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM script_creators")
        total_creators = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM script_creators WHERE is_active = 1")
        active_creators = cursor.fetchone()[0]
        
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        total_scripts = admin_scripts = user_scripts = public_scripts = special_scripts = 0
        total_creators = active_creators = 0
    
    conn.close()
    
    return {
        'total_scripts': total_scripts,
        'admin_scripts': admin_scripts,
        'user_scripts': user_scripts,
        'public_scripts': public_scripts,
        'special_scripts': special_scripts,
        'total_users': len(USERS),
        'total_creators': total_creators,
        'active_creators': active_creators
    }

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

def parse_buttons(buttons_text: str):
    """Парсинг кнопок из текста"""
    buttons = []
    
    # Разделяем ряды кнопок
    rows = buttons_text.strip().split('\n')
    
    for row in rows:
        row_buttons = []
        # Разделяем кнопки в ряду
        button_pairs = [btn.strip() for btn in row.split('|') if btn.strip()]
        
        for button_pair in button_pairs:
            if '-' in button_pair:
                parts = button_pair.split('-', 1)
                if len(parts) == 2:
                    text = parts[0].strip()
                    url = parts[1].strip()
                    if text and url and url.startswith('http'):
                        row_buttons.append(InlineKeyboardButton(text=text, url=url))
        
        if row_buttons:
            buttons.append(row_buttons)
    
    return buttons

# ================== ПРИВЕТСТВИЕ ==================

async def send_welcome(message: types.Message):
    """Отправка приветственного сообщения"""
    user_id = message.from_user.id
    USERS.add(user_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="📤 Загрузить скрипт", callback_data="upload_script")],
            [InlineKeyboardButton(text="🔗 Загрузить ссылку", callback_data="upload_link")]
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
        "📤 <b>Загрузить свой скрипт:</b>\n"
        "Нажми кнопку ниже чтобы загрузить свой скрипт и получить уникальную ссылку!\n\n"
        "🔗 <b>ИЛИ Загрузить ссылку:</b>\n"
        "Нажми 'Загрузить ссылку' чтобы создать уникальную ссылку на любой контент!\n\n"
        "❗️ <b>Важно:</b>\n"
        "Чтобы получить скрипт — просто перейди в нужный канал и нажми кнопку «Получить скрипт»\n\n"
        "<b>Для сотрудничества:</b> @SecretLinkAds"
    )

    await message.answer(
        text.format(nick=message.from_user.full_name),
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# ================== /start с уникальными ссылками ==================

@router.message(CommandStart())
async def start_handler(message: types.Message):
    """Обработка команды /start"""
    if message.chat.type != "private":
        return
    
    USERS.add(message.from_user.id)
    
    # Сначала проверяем подписки через все три сервиса
    if len(message.text.split()) > 1:
        unique_code = message.text.split()[1]
        
        # Проверяем все подписки
        results = await check_all_subscriptions(
            user_id=message.from_user.id,
            username=message.from_user.username,
            lang=message.from_user.language_code,
            is_premium=message.from_user.is_premium
        )
        
        if not results["all_passed"]:
            keyboard = create_combined_keyboard(
                results["subgram"], 
                results["tgrass"], 
                results["flyer"]
            )
            warning_message = "❗ Чтобы получить доступ к боту, выполните следующие задания:"
            
            await message.answer(
                warning_message,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return
        
        script_data = get_script_content(unique_code)
        
        if script_data:
            await show_script_content(message, script_data)
            return
        else:
            await message.answer("❌ Ссылка не найдена или устарела")
            return
    
    # Проверяем все подписки для обычного /start
    results = await check_all_subscriptions(
        user_id=message.from_user.id,
        username=message.from_user.username,
        lang=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if not results["all_passed"]:
        keyboard = create_combined_keyboard(
            results["subgram"], 
            results["tgrass"], 
            results["flyer"]
        )
        warning_message = "❗ Чтобы получить доступ к боту, выполните следующие задания:"
        
        await message.answer(
            warning_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    await send_welcome(message)

async def show_script_content(message: types.Message, script_data: dict):
    """Показать скрипт с форматированием"""
    script_content = script_data['content']
    is_special = script_data['is_special']
    link_url = script_data['link_url']
    
    if is_special:
        # Специальный режим - только ссылка
        # Как на второй фотографии: оставляем "Ваш скрипт" и кнопку
        header_text = ""  # Убираем "✅ | Спасибо за подписки!"
        script_text = "<b>Ваш скрипт ⬇️</b>"  # Оставляем как на второй фотке
        footer_text = ""  # Убираем "@{BOT_USERNAME}"
        
        final_text = script_text  # Только "Ваш скрипт ⬇️"
        
        keyboard_buttons = []
        
        if link_url:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="Получить скрипт 🚀",
                    url=link_url
                )
            ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None
        
        # Отправляем сообщение как на второй фотке
        await message.answer(
            final_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    else:
        # Обычный режим - оставляем как было
        formatted_script = format_script_for_display(script_content)
        
        header_text = "<b>✅ | Спасибо за подписки!</b>\n\n"
        footer_text = f"\n\n<b>@</b>{BOT_USERNAME}"
        
        final_text = header_text + formatted_script + footer_text
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="⚡️ Больше скриптов ⚡️", 
                    url="https://t.me/script_f"
                )],
                [InlineKeyboardButton(
                    text="📤 Загрузить свой скрипт", 
                    callback_data="upload_script"
                )]
            ]
        )
        
        await message.answer(
            final_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

# ================== КОМАНДЫ ДЛЯ СОЗДАТЕЛЕЙ СКРИПТОВ ==================

@router.message(Command("oplink"))
async def add_creator_command(message: types.Message):
    """Добавление создателя скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /oplink [id]\n\n"
            "<b>Пример:</b>\n"
            "<code>/oplink 123456789</code>\n\n"
            "Чтобы получить ID пользователя, перешлите его сообщение боту @userinfobot",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(message.text.split()[1])
        
        if is_script_creator(user_id):
            await message.answer(f"❌ Пользователь с ID {user_id} уже является создателем скриптов")
            return
        
        try:
            user = await message.bot.get_chat(user_id)
            username = user.username
            full_name = user.full_name
        except:
            username = None
            full_name = f"User_{user_id}"
        
        if add_script_creator(user_id, username, full_name):
            await message.answer(
                f"✅ <b>Создатель скриптов добавлен!</b>\n\n"
                f"👤 <b>ID:</b> <code>{user_id}</code>\n"
                f"📛 <b>Имя:</b> {full_name}\n"
                f"🔗 <b>Username:</b> @{username if username else 'нет'}\n\n"
                f"Теперь этот пользователь может загружать скрипты через админ-панель.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка при добавлении создателя")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("stoplink"))
async def remove_creator_command(message: types.Message):
    """Удаление создателя скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    if len(message.text.split()) < 2:
        await message.answer(
            "❌ <b>Использование:</b> /stoplink [id]\n\n"
            "<b>Пример:</b>\n"
            "<code>/stoplink 123456789</code>\n\n"
            "Чтобы посмотреть всех создателей, используйте /creators",
            parse_mode="HTML"
        )
        return
    
    try:
        user_id = int(message.text.split()[1])
        
        if not is_script_creator(user_id) or user_id == ADMIN_ID:
            await message.answer(f"❌ Пользователь с ID {user_id} не является создателем скриптов")
            return
        
        if remove_script_creator(user_id):
            await message.answer(
                f"✅ <b>Создатель скриптов удален!</b>\n\n"
                f"👤 <b>ID:</b> <code>{user_id}</code>\n\n"
                f"Теперь этот пользователь больше не может загружать скрипты.",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка при удалении создателя")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. ID должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@router.message(Command("creators"))
async def list_creators_command(message: types.Message):
    """Показывает список всех создателей скриптов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды")
        return
    
    creators = get_all_script_creators()
    
    if not creators:
        await message.answer("📭 <b>Список создателей пуст</b>", parse_mode="HTML")
        return
    
    text = "<b>👥 Список создателей скриптов:</b>\n\n"
    
    for i, (user_id, username, full_name, created_at, is_active) in enumerate(creators, 1):
        status = "🟢 Активен" if is_active else "🔴 Неактивен"
        text += f"{i}. <b>ID:</b> <code>{user_id}</code>\n"
        text += f"   <b>Имя:</b> {full_name}\n"
        text += f"   <b>Username:</b> @{username if username else 'нет'}\n"
        text += f"   <b>Статус:</b> {status}\n"
        text += f"   <b>Добавлен:</b> {created_at}\n\n"
    
    text += f"<b>Всего:</b> {len(creators)} создателей\n"
    text += "<b>Используйте:</b>\n"
    text += "/oplink [id] - добавить создателя\n"
    text += "/stoplink [id] - удалить создателя"
    
    await message.answer(text, parse_mode="HTML")

# ================== АДМИН ПАНЕЛЬ ==================

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Админ панель для главного админа и создателей скриптов"""
    if message.chat.type != "private":
        return
        
    if not (message.from_user.id == ADMIN_ID or is_script_creator(message.from_user.id)):
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return

    stats = get_statistics()
    
    if message.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="🔗 Загрузка ссылки", callback_data="admin_upload_link")],
                [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")],
                [InlineKeyboardButton(text="👑 Управление создателями", callback_data="admin_manage_creators")]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="🔗 Загрузка ссылки", callback_data="admin_upload_link")]
            ]
        )

    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 🔗 Специальных: {stats['special_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n"
    )
    
    if message.from_user.id == ADMIN_ID:
        admin_text += f"• 👥 Создателей: {stats['active_creators']}\n"
    
    admin_text += f"\n🔗 <b>Группа для скриптов:</b>\n"
    admin_text += f"ID: {GROUP_ID}\n"
    admin_text += f"Топик: {TOPIC_ID}\n\n"
    admin_text += f"🔐 <b>Проверка подписок:</b>\n"
    admin_text += f"• SubGram: ✅ Включено\n"
    admin_text += f"• Tgrass: ✅ Включено\n"
    admin_text += f"• Flyer: ✅ Включено"

    await message.answer(admin_text, reply_markup=keyboard, parse_mode="HTML")

# ================== ОБРАБОТКА КНОПОК АДМИН-ПАНЕЛИ ==================

@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    """Статистика бота"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    stats_text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"📜 Всего скриптов: <b>{stats['total_scripts']}</b>\n"
        f"🔗 Специальных ссылок: <b>{stats['special_scripts']}</b>\n"
        f"👤 Публичных скриптов: <b>{stats['public_scripts']}</b>\n"
        f"👑 Загружено админом: <b>{stats['admin_scripts']}</b>\n"
        f"👥 Загружено пользователями: <b>{stats['user_scripts']}</b>\n"
    )
    
    if callback.from_user.id == ADMIN_ID:
        stats_text += f"👥 Создателей скриптов: <b>{stats['active_creators']}</b>\n\n"
    else:
        stats_text += "\n"
    
    stats_text += (
        f"📤 Загрузка скриптов: <b>✅ Доступна всем</b>\n"
        f"🔗 Загрузка ссылок: <b>✅ Доступна всем</b>\n"
        f"🔗 Используется SubGram: <b>✅ Да</b>\n"
        f"🔗 Используется Tgrass: <b>✅ Да</b>\n"
        f"🔗 Используется Flyer: <b>✅ Да</b>\n"
        f"👥 Группа: <b>{GROUP_ID}</b>\n"
        f"📌 Топик: <b>{TOPIC_ID}</b>"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "upload_script")
async def upload_script_callback(callback: types.CallbackQuery):
    """Загрузка скрипта - доступна всем пользователям"""
    UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.answer(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.\n\n"
        "<i>Просто отправьте скрипт следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "upload_link")
async def upload_link_callback(callback: types.CallbackQuery):
    """Загрузка ссылки - доступна всем пользователям"""
    SPECIAL_UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.answer(
        "🔗 <b>Загрузка ссылки</b>\n\n"
        "Отправьте любую ссылку (например: https://t.me/script_f).\n\n"
        "После отправки бот создаст уникальную ссылку.\n"
        "Когда пользователь перейдет по этой ссылке, он увидит:\n"
        "• <b>Ваш скрипт ⬇️</b> (жирным шрифтом)\n"
        "• Кнопку 'Получить скрипт' с вашей ссылкой\n\n"
        "<i>Просто отправьте ссылку следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_upload_script")
async def admin_upload_script_callback(callback: types.CallbackQuery):
    """Загрузка скрипта из админ-панели"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.edit_text(
        "📤 <b>Загрузка скрипта</b>\n\n"
        "Отправьте скрипт для Roblox.\n\n"
        "После отправки бот создаст уникальную ссылку на скрипт.\n\n"
        "<i>Просто отправьте скрипт следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_upload_link")
async def admin_upload_link_callback(callback: types.CallbackQuery):
    """Загрузка ссылки из админ-панели"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    SPECIAL_UPLOADING_USERS.add(callback.from_user.id)
    
    await callback.message.edit_text(
        "🔗 <b>Загрузка ссылки</b>\n\n"
        "Отправьте любую ссылку (например: https://t.me/script_f).\n\n"
        "После отправки бот создаст уникальную ссылку.\n"
        "Когда пользователь перейдет по этой ссылке, он увидит:\n"
        "• <b>Ваш скрипт ⬇️</b> (жирным шрифтом)\n"
        "• Кнопку 'Получить скрипт' с вашей ссылкой\n\n"
        "<i>Просто отправьте ссылку следующим сообщением</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "admin_public_scripts")
async def admin_public_scripts(callback: types.CallbackQuery):
    """Публичные скрипты"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    conn = sqlite3.connect('scripts.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM scripts WHERE is_public = 1')
    total_public = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM scripts WHERE is_special = 1')
    total_special = cursor.fetchone()[0]
    
    cursor.execute('''
    SELECT unique_code, script_content, created_by, created_at, is_special, link_url 
    FROM scripts WHERE is_public = 1 
    ORDER BY created_at DESC LIMIT 10
    ''')
    
    recent_scripts = cursor.fetchall()
    conn.close()
    
    stats_text = f"📜 <b>Публичные скрипты: {total_public}</b>\n"
    stats_text += f"🔗 <b>Специальные ссылки: {total_special}</b>\n\n"
    
    if recent_scripts:
        stats_text += "<b>Последние 10 скриптов:</b>\n"
        for i, (code, content, user_id, created_at, is_special, link_url) in enumerate(recent_scripts, 1):
            preview = content[:30] + "..." if len(content) > 30 else content
            stats_text += f"{i}. <code>{code}</code>\n"
            stats_text += f"   👤 ID: {user_id}\n"
            stats_text += f"   📝 {preview}\n"
            if is_special and link_url:
                stats_text += f"   🔗 Ссылка: {link_url[:30]}...\n"
            stats_text += f"   🕐 {created_at}\n\n"
    else:
        stats_text += "📭 Нет публичных скриптов\n"
    
    stats_text += f"\n🔗 <b>Ссылка на группу:</b>\nhttps://t.me/c/{abs(GROUP_ID) - 1000000000000}/{TOPIC_ID}"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_manage_creators")
async def admin_manage_creators(callback: types.CallbackQuery):
    """Управление создателями скриптов (только для главного админа)"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    creators = get_all_script_creators()
    
    text = "<b>👑 Управление создателями скриптов</b>\n\n"
    
    if creators:
        text += "<b>Текущие создатели:</b>\n"
        for i, (user_id, username, full_name, created_at, is_active) in enumerate(creators, 1):
            status = "🟢" if is_active else "🔴"
            text += f"{i}. {status} <b>ID:</b> <code>{user_id}</code>\n"
            text += f"   👤 {full_name}\n"
            if username:
                text += f"   📱 @{username}\n"
            text += f"   📅 {created_at}\n\n"
    else:
        text += "📭 Создателей нет\n\n"
    
    text += "<b>Команды:</b>\n"
    text += "<code>/oplink [id]</code> - добавить создателя\n"
    text += "<code>/stoplink [id]</code> - удалить создателя\n"
    text += "<code>/creators</code> - список всех создателей"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="back_to_admin")]
        ]
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ================== РАССЫЛКА С КНОПКАМИ ==================

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback: types.CallbackQuery, state: FSMContext):
    """Рассылка (только для главного админа)"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа к рассылке", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📢 <b>Начало рассылки</b>\n\n"
        "Отправьте сообщение для рассылки:\n"
        "• Текст\n"
        "• Фото с подписью\n"
        "• Видео с подписью\n"
        "• Документ с подписью\n\n"
        "Просто отправьте сообщение следующим сообщением.\n\n"
        "После этого вы сможете добавить кнопки.",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)
    await callback.answer()

@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: types.Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    # Сохраняем данные сообщения
    await state.update_data(
        content_type=message.content_type,
        text=message.text or message.caption or "",
        photo=message.photo[-1].file_id if message.photo else None,
        video=message.video.file_id if message.video else None,
        document=message.document.file_id if message.document else None
    )
    
    await message.answer(
        "✅ <b>Сообщение для рассылки принято!</b>\n\n"
        "📝 <b>Что дальше:</b>\n"
        "1. Если нужны кнопки - отправьте их в формате:\n\n"
        "<code>Текст кнопки 1 - https://ссылка1.com</code>\n"
        "<code>Текст кнопки 2 - https://ссылка2.com | Текст кнопки 3 - https://ссылка3.com</code>\n\n"
        "<b>Формат:</b>\n"
        "• Каждая строка - новый ряд кнопок\n"
        "• Разделитель между кнопками в одном ряду - |\n"
        "• Разделитель между текстом и ссылкой - -\n\n"
        "<b>Пример:</b>\n"
        "<code>Наш канал - https://t.me/script_f</code>\n"
        "<code>Поддержка - https://t.me/secretlink | Донат - https://donate.com</code>\n\n"
        "2. Чтобы начать рассылку без кнопок, отправьте: <code>/start_broadcast</code>\n"
        "3. Чтобы отменить рассылку, отправьте: <code>/cancel</code>",
        parse_mode="HTML"
    )
    
    await state.set_state(BroadcastStates.waiting_for_buttons)

@router.message(BroadcastStates.waiting_for_buttons, Command("start_broadcast"))
async def start_broadcast_without_buttons(message: types.Message, state: FSMContext):
    """Начало рассылки без кнопок"""
    if message.from_user.id != ADMIN_ID:
        return
    
    user_data = await state.get_data()
    
    # Очищаем состояние
    await state.clear()
    
    # Начинаем рассылку
    await send_broadcast(message, user_data, None)

@router.message(BroadcastStates.waiting_for_buttons, Command("cancel"))
async def cancel_broadcast(message: types.Message, state: FSMContext):
    """Отмена рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await state.clear()
    await message.answer("❌ Рассылка отменена")

@router.message(BroadcastStates.waiting_for_buttons)
async def handle_broadcast_buttons(message: types.Message, state: FSMContext):
    """Обработка кнопок для рассылки"""
    if message.from_user.id != ADMIN_ID:
        return
    
    user_data = await state.get_data()
    
    if message.text:
        # Парсим кнопки
        buttons = parse_buttons(message.text)
        
        if buttons:
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        else:
            keyboard = None
        
        # Очищаем состояние
        await state.clear()
        
        # Начинаем рассылку
        await send_broadcast(message, user_data, keyboard)
    else:
        await message.answer("❌ Отправьте текст с кнопками или команду /start_broadcast / /cancel")

async def send_broadcast(message: types.Message, broadcast_data: dict, keyboard: InlineKeyboardMarkup = None):
    """Выполнение рассылки"""
    sent = 0
    failed = 0
    
    await message.answer(f"⏳ Начинаю рассылку для {len(USERS)} пользователей...")
    
    for user_id in list(USERS):
        try:
            if broadcast_data['content_type'] == 'text':
                await message.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif broadcast_data['content_type'] == 'photo':
                await message.bot.send_photo(
                    chat_id=user_id,
                    photo=broadcast_data['photo'],
                    caption=broadcast_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif broadcast_data['content_type'] == 'video':
                await message.bot.send_video(
                    chat_id=user_id,
                    video=broadcast_data['video'],
                    caption=broadcast_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            elif broadcast_data['content_type'] == 'document':
                await message.bot.send_document(
                    chat_id=user_id,
                    document=broadcast_data['document'],
                    caption=broadcast_data['text'],
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            sent += 1
            await asyncio.sleep(0.1)
            
        except Exception as e:
            failed += 1
            logging.error(f"Не удалось отправить пользователю {user_id}: {e}")
    
    result_text = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Отправлено: {sent}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего пользователей: {len(USERS)}\n\n"
        f"📈 <b>Эффективность:</b> {(sent/(sent+failed)*100 if (sent+failed) > 0 else 0):.1f}%"
    )
    
    await message.answer(result_text, parse_mode="HTML")

@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_callback(callback: types.CallbackQuery):
    """Назад в админ-панель"""
    if not (callback.from_user.id == ADMIN_ID or is_script_creator(callback.from_user.id)):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    if callback.from_user.id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
                [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="🔗 Загрузка ссылки", callback_data="admin_upload_link")],
                [InlineKeyboardButton(text="👥 Публичные скрипты", callback_data="admin_public_scripts")],
                [InlineKeyboardButton(text="👑 Управление создателями", callback_data="admin_manage_creators")]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Загрузка скрипта", callback_data="admin_upload_script")],
                [InlineKeyboardButton(text="🔗 Загрузка ссылки", callback_data="admin_upload_link")]
            ]
        )
    
    admin_text = (
        "👑 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 📜 Всего скриптов: {stats['total_scripts']}\n"
        f"• 🔗 Специальных: {stats['special_scripts']}\n"
        f"• 👤 Публичных: {stats['public_scripts']}\n"
        f"• 👑 Админских: {stats['admin_scripts']}\n"
    )
    
    if callback.from_user.id == ADMIN_ID:
        admin_text += f"• 👥 Создателей: {stats['active_creators']}\n"
    
    admin_text += f"\n🔗 <b>Группа для скриптов:</b>\n"
    admin_text += f"ID: {GROUP_ID}\n"
    admin_text += f"Топик: {TOPIC_ID}\n\n"
    admin_text += f"🔐 <b>Проверка подписок:</b>\n"
    admin_text += f"• SubGram: ✅ Включено\n"
    admin_text += f"• Tgrass: ✅ Включено\n"
    admin_text += f"• Flyer: ✅ Включено"
    
    await callback.message.edit_text(admin_text, reply_markup=keyboard, parse_mode="HTML")

# ================== ОБРАБОТКА ЗАГРУЗКИ СКРИПТА ==================

@router.message(F.content_type.in_({'text', 'document'}))
async def handle_script_upload(message: types.Message):
    """Обработка загрузки скрипта - доступна всем пользователям"""
    if message.chat.type != "private":
        return
    
    # Проверяем загрузку скрипта
    if message.from_user.id in UPLOADING_USERS:
        UPLOADING_USERS.discard(message.from_user.id)
        
        # Получаем текст скрипта
        script_content = ""
        
        if message.content_type == 'text':
            script_content = message.text.strip()
        elif message.content_type == 'document' and message.document:
            try:
                file = await message.bot.download(message.document)
                content = file.read()
                if isinstance(content, bytes):
                    script_content = content.decode('utf-8', errors='ignore')
                else:
                    script_content = str(content)
            except Exception as e:
                await message.answer(f"❌ Не удалось прочитать файл: {str(e)}. Отправьте скрипт как текст.")
                return
        else:
            await message.answer("❌ Отправьте скрипт в виде текста или текстового файла.")
            return
        
        if not script_content or len(script_content.strip()) < 10:
            await message.answer("❌ Скрипт не может быть пустым или слишком коротким (минимум 10 символов).")
            return
        
        # Сохраняем скрипт в базу данных (публичным для всех, обычный скрипт)
        unique_code = save_script_to_db(script_content, message.from_user.id, is_public=True, is_special=False)
        
        # Создаем ссылку
        link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
        
        # Показываем результат
        preview_text = "<b>✅ Скрипт успешно загружен!</b>\n\n"
        preview_text += f"<b>🎯 Ваша уникальная ссылка:</b>\n<code>{link}</code>\n\n"
        
        preview_text += f"<b>📊 Информация о ссылке:</b>\n"
        preview_text += f"• 👥 <b>Тип:</b> Обычный скрипт\n"
        preview_text += f"• 📝 <b>Размер скрипта:</b> {len(script_content)} символов\n"
        preview_text += f"• 🕐 <b>Создана:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        
        script_preview = script_content[:200] + "..." if len(script_content) > 200 else script_content
        formatted_preview = format_script_for_display(script_preview)
        
        preview_text += "<b>📝 Предпросмотр скрипта:</b>\n"
        preview_text += formatted_preview + "\n\n"
        
        preview_text += "<b>💡 Как пользоваться:</b>\n"
        preview_text += "1. Скопируйте ссылку выше\n"
        preview_text += "2. Отправьте друзьям или в чаты\n"
        preview_text += "3. При переходе по ссылке откроется скрипт\n\n"
        
        preview_text += "🔗 <b>Наш канал:</b> @script_f"
        
        # Клавиатура с кнопками
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Загрузить еще один", callback_data="upload_script")],
                [InlineKeyboardButton(text="🔗 Загрузить ссылку", callback_data="upload_link")],
                [InlineKeyboardButton(text="📢 Поделиться ссылкой", url=f"https://t.me/share/url?url={link}&text=🎮+Топ-скрипт+для+Roblox+🔥%0A"
            "Хочешь+больше+возможностей+в+игре%3F%0A"
            "Лучшие+скрипты+уже+ждут+тебя!%0A"
            "👉+Подписывайся:+@script_f")],
                [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)]
            ]
        )
        
        await message.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")
        
    # Проверяем загрузку ссылки
    elif message.from_user.id in SPECIAL_UPLOADING_USERS:
        SPECIAL_UPLOADING_USERS.discard(message.from_user.id)
        
        if message.content_type == 'text':
            link_url = message.text.strip()
            
            # Проверяем, что это валидная ссылка
            if not (link_url.startswith('http://') or link_url.startswith('https://')):
                await message.answer("❌ Это не валидная ссылка. Отправьте ссылку начинающуюся с http:// или https://")
                return
            
            # Создаем специальный скрипт с заголовком (текст будет заменен в save_script_to_db)
            script_content = ""  # Пустой текст, так как будет заменен на "<b>Ваш скрипт ⬇️</b>"
            
            # Сохраняем в базу данных как специальный скрипт
            unique_code = save_script_to_db(
                script_content, 
                message.from_user.id, 
                is_public=True, 
                is_special=True, 
                link_url=link_url
            )
            
            # Создаем ссылку на бота
            bot_link = f"https://t.me/{BOT_USERNAME}?start={unique_code}"
            
            # Показываем результат
            preview_text = "<b>✅ Ссылка успешно загружена!</b>\n\n"
            preview_text += f"<b>🎯 Ваша уникальная ссылка:</b>\n<code>{bot_link}</code>\n\n"
            
            preview_text += f"<b>📊 Информация о ссылке:</b>\n"
            preview_text += f"• 🔗 <b>Тип:</b> Специальная ссылка\n"
            preview_text += f"• 📎 <b>Ваша ссылка:</b> {link_url}\n"
            preview_text += f"• 🕐 <b>Создана:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            
            preview_text += "<b>👀 Как это будет выглядеть:</b>\n"
            preview_text += "1. Пользователь переходит по вашей ссылке\n"
            preview_text += "2. Видит: <b>Ваш скрипт ⬇️</b>\n"
            preview_text += "3. Видит кнопку: <b>Получить скрипт</b>\n"
            preview_text += "4. Нажимает и переходит по вашей ссылке\n\n"
            
            preview_text += "<b>💡 Как пользоваться:</b>\n"
            preview_text += "1. Скопируйте ссылку выше\n"
            preview_text += "2. Отправьте друзьям или в чаты\n"
            preview_text += "3. При переходе откроется страница с вашей ссылкой\n\n"
            
            preview_text += "🔗 <b>Наш канал:</b> @script_f"
            
            # Клавиатура с кнопками
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Загрузить еще одну", callback_data="upload_link")],
                    [InlineKeyboardButton(text="📤 Загрузить скрипт", callback_data="upload_script")],
                    [InlineKeyboardButton(text="📢 Поделиться ссылкой", url=f"https://t.me/share/url?url={bot_link}&text=🎮+Топ-скрипт+для+Roblox+🔥%0A"
                "Хочешь+больше+возможностей+в+игре%3F%0A"
                "Получи+лучший+скрипт+прямо+сейчас!%0A"
                "👉+Нажми+на+ссылку+и+получи+скрипт")],
                    [InlineKeyboardButton(text="🔥 Наш канал", url=CHANNEL_URL)]
                ]
            )
            
            await message.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.answer("❌ Отправьте ссылку в виде текста.")
            return
    
    else:
        # Если пользователь не в процессе загрузки, просто игнорируем сообщение
        return

# ================== ОБРАБОТКА ДРУГИХ СООБЩЕНИЙ ==================

@router.message()
async def handle_other_messages(message: types.Message):
    """Обработка других сообщений"""
    if message.chat.type != "private":
        return
    
    if not message.text or not message.text.startswith('/'):
        if message.from_user.id != ADMIN_ID and not is_script_creator(message.from_user.id):
            await message.answer(
                "🤖 <b>Неизвестная команда, напишите /start</b>\n\n",
                parse_mode="HTML"
            )

# ================== RUN ==================

async def main():
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    try:
        me = await bot.get_me()
        logging.info(f"Бот запущен: @{me.username}")
        logging.info(f"ID группы для мониторинга: {GROUP_ID}")
        logging.info(f"ID топика: {TOPIC_ID}")
        logging.info(f"Проверка подписок: SubGram + Tgrass + Flyer")
        logging.info(f"ВАЖНО: Установите реальный FLYER_API_KEY и FLYER_API_BASE в настройках!")
    except Exception as e:
        logging.error(f"Ошибка при получении информации о боте: {e}")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
