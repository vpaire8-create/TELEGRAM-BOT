#!/usr/bin/env python3
"""
SYAPA KING - COMPLETE FACEBOOK AUTOMATION BOT
Features:
1. Inbox Automation (Cookie-based, Selenium)
2. Group Automation (Cookie-based, Selenium)
Telegram Bot Control
"""

import sqlite3
import hashlib
import json
import os
import time
import threading
import asyncio
import logging
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from cryptography.fernet import Fernet
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7791213862:AAFvGyuCCVZqpnQQwjZBbu89drzuiJPAcJM')
ADMIN_USER_IDS = [int(x) for x in os.environ.get('ADMIN_USER_IDS', '7791213862').split(',')]
OWNER_NAME = os.environ.get('OWNER_NAME', 'SYAPA KING')
OWNER_FACEBOOK = os.environ.get('OWNER_FACEBOOK', 'https://www.facebook.com/share/168AJz6Ehm/')

# Data directory
DATA_DIR = Path(os.environ.get('DATA_DIR', '/tmp/data'))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA_DIR = Path('/app/data')
    DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'facebook_bot.db'
ENCRYPTION_KEY_FILE = DATA_DIR / '.encryption_key'

# Conversation states
(CHOOSING_AUTOMATION, WAITING_COOKIES, WAITING_INBOX_CHAT_ID, WAITING_NAME_PREFIX,
 WAITING_DELAY, WAITING_MESSAGES, WAITING_GROUP_ID, WAITING_GROUP_MESSAGES,
 WAITING_GROUP_DELAY, WAITING_GROUP_NAME) = range(10)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Active automation threads
active_inbox_threads = {}
active_group_threads = {}

# ==================== ENCRYPTION ====================
def get_encryption_key():
    if ENCRYPTION_KEY_FILE.exists():
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        return key

ENCRYPTION_KEY = get_encryption_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_data(data: str) -> str:
    if not data:
        return ""
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    if not encrypted_data:
        return ""
    try:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    except:
        return ""

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            approved INTEGER DEFAULT 0,
            approval_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Inbox configs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inbox_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id TEXT,
            name_prefix TEXT,
            delay INTEGER DEFAULT 30,
            cookies_encrypted TEXT,
            messages TEXT,
            running INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Group configs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id TEXT,
            group_name TEXT,
            delay INTEGER DEFAULT 30,
            cookies_encrypted TEXT,
            messages TEXT,
            running INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")

def get_or_create_user(telegram_id: int, username: str = None) -> tuple:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, approval_key, approved FROM users WHERE telegram_id = ?', (telegram_id,))
    user = cursor.fetchone()
    
    if user:
        conn.close()
        return user[0], user[1], user[2]
    else:
        approval_key = hashlib.sha256(f"{telegram_id}:{time.time()}".encode()).hexdigest()[:12].upper()
        cursor.execute('INSERT INTO users (telegram_id, username, approval_key) VALUES (?, ?, ?)',
                      (telegram_id, username, approval_key))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id, approval_key, 0

def approve_user(telegram_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET approved = 1 WHERE telegram_id = ?', (telegram_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

# Inbox functions
def save_inbox_config(user_id: int, chat_id: str, name_prefix: str, delay: int, messages: str, cookies: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    encrypted_cookies = encrypt_data(cookies)
    
    cursor.execute('''
        INSERT OR REPLACE INTO inbox_configs 
        (user_id, chat_id, name_prefix, delay, messages, cookies_encrypted)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, chat_id, name_prefix, delay, messages, encrypted_cookies))
    
    conn.commit()
    conn.close()

def get_inbox_config(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT chat_id, name_prefix, delay, messages, cookies_encrypted, running, message_count
        FROM inbox_configs WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'chat_id': row[0],
            'name_prefix': row[1] or '',
            'delay': row[2] or 30,
            'messages': row[3] or '',
            'cookies': decrypt_data(row[4]) if row[4] else '',
            'running': row[5] or 0,
            'message_count': row[6] or 0
        }
    return None

def update_inbox_running(user_id: int, running: int, message_count: int = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if message_count is not None:
        cursor.execute('UPDATE inbox_configs SET running = ?, message_count = ? WHERE user_id = ?',
                      (running, message_count, user_id))
    else:
        cursor.execute('UPDATE inbox_configs SET running = ? WHERE user_id = ?', (running, user_id))
    conn.commit()
    conn.close()

# Group functions
def save_group_config(user_id: int, group_id: str, group_name: str, delay: int, messages: str, cookies: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    encrypted_cookies = encrypt_data(cookies)
    
    cursor.execute('''
        INSERT OR REPLACE INTO group_configs 
        (user_id, group_id, group_name, delay, messages, cookies_encrypted)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, group_id, group_name, delay, messages, encrypted_cookies))
    
    conn.commit()
    conn.close()

def get_group_config(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT group_id, group_name, delay, messages, cookies_encrypted, running, message_count
        FROM group_configs WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'group_id': row[0],
            'group_name': row[1] or '',
            'delay': row[2] or 30,
            'messages': row[3] or '',
            'cookies': decrypt_data(row[4]) if row[4] else '',
            'running': row[5] or 0,
            'message_count': row[6] or 0
        }
    return None

def update_group_running(user_id: int, running: int, message_count: int = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if message_count is not None:
        cursor.execute('UPDATE group_configs SET running = ?, message_count = ? WHERE user_id = ?',
                      (running, message_count, user_id))
    else:
        cursor.execute('UPDATE group_configs SET running = ? WHERE user_id = ?', (running, user_id))
    conn.commit()
    conn.close()

# ==================== SELENIUM HELPERS ====================
def setup_browser(log_callback=None):
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-setuid-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    # Find Chromium
    chromium_paths = ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome', '/usr/bin/chrome']
    for path in chromium_paths:
        if Path(path).exists():
            chrome_options.binary_location = path
            if log_callback:
                log_callback(f"✅ Browser found: {path}")
            break
    
    # Find ChromeDriver
    driver_paths = ['/usr/bin/chromedriver', '/usr/local/bin/chromedriver']
    driver_path = None
    for path in driver_paths:
        if Path(path).exists():
            driver_path = path
            if log_callback:
                log_callback(f"✅ Driver found: {path}")
            break
    
    try:
        if driver_path:
            service = Service(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            driver = webdriver.Chrome(options=chrome_options)
        
        driver.set_window_size(1920, 1080)
        return driver
    except Exception as e:
        if log_callback:
            log_callback(f"❌ Browser error: {e}")
        raise

def add_facebook_cookies(driver, cookies_str, log_callback=None):
    if not cookies_str:
        return False
    
    driver.get('https://www.facebook.com/')
    time.sleep(5)
    
    cookie_pairs = cookies_str.split(';')
    for pair in cookie_pairs:
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            try:
                driver.add_cookie({'name': name.strip(), 'value': value.strip(), 'domain': '.facebook.com', 'path': '/'})
            except:
                pass
    
    driver.refresh()
    time.sleep(5)
    
    if 'login' in driver.current_url.lower():
        if log_callback:
            log_callback("❌ Login failed! Cookies may be expired.")
        return False
    
    if log_callback:
        log_callback("✅ Login successful!")
    return True

def find_message_input(driver, log_callback=None):
    time.sleep(5)
    
    selectors = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
        '[role="textbox"][contenteditable="true"]',
        'textarea'
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    if log_callback:
                        log_callback(f"✅ Found input: {selector[:40]}")
                    return element
        except:
            continue
    
    return None

def find_comment_input(driver, log_callback=None):
    time.sleep(5)
    
    selectors = [
        'div[contenteditable="true"][aria-label*="comment" i]',
        'div[contenteditable="true"][aria-label*="write" i]',
        'div[contenteditable="true"]',
        'textarea'
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    if log_callback:
                        log_callback(f"✅ Found comment input: {selector[:40]}")
                    return element
        except:
            continue
    
    return None

def send_message_to_input(driver, input_element, message, log_callback=None):
    try:
        driver.execute_script("""
            arguments[0].focus();
            arguments[0].click();
            if (arguments[0].tagName === 'DIV') {
                arguments[0].innerHTML = arguments[1];
                arguments[0].textContent = arguments[1];
            } else {
                arguments[0].value = arguments[1];
            }
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """, input_element, message)
        
        time.sleep(1)
        
        # Try to click send button
        send_buttons = driver.find_elements(By.CSS_SELECTOR, 
            '[aria-label*="Send" i], [data-testid="send-button"], [aria-label*="Post" i], button[type="submit"]')
        
        for btn in send_buttons:
            if btn.is_displayed():
                btn.click()
                if log_callback:
                    log_callback(f"✅ Sent: {message[:50]}...")
                return True
        
        # Try Enter key
        driver.execute_script("""
            var event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true });
            arguments[0].dispatchEvent(event);
        """, input_element)
        
        if log_callback:
            log_callback(f"✅ Sent via Enter: {message[:50]}...")
        return True
        
    except Exception as e:
        if log_callback:
            log_callback(f"❌ Send error: {str(e)[:80]}")
        return False

# ==================== INBOX AUTOMATION ====================
def run_inbox_automation(user_id: int, config: Dict, chat_id: int, log_callback=None):
    driver = None
    messages_sent = 0
    
    try:
        if log_callback:
            log_callback("🚀 Starting INBOX automation...")
            log_callback(f"📱 Chat ID: {config['chat_id']}")
            log_callback(f"⏱️ Delay: {config['delay']}s")
        
        driver = setup_browser(log_callback)
        
        # Login with cookies
        if not add_facebook_cookies(driver, config['cookies'], log_callback):
            update_inbox_running(user_id, 0)
            return 0
        
        # Navigate to conversation
        chat_id_target = config['chat_id'].strip()
        urls = [
            f'https://www.facebook.com/messages/t/{chat_id_target}',
            f'https://www.facebook.com/messages/e2ee/t/{chat_id_target}'
        ]
        
        for url in urls:
            driver.get(url)
            time.sleep(8)
            if 'messages' in driver.current_url:
                if log_callback:
                    log_callback(f"✅ Opened conversation")
                break
        
        # Find message input
        message_input = find_message_input(driver, log_callback)
        if not message_input:
            if log_callback:
                log_callback("❌ Message input not found!")
            update_inbox_running(user_id, 0)
            return 0
        
        # Prepare messages
        messages_list = [msg.strip() for msg in config['messages'].split('\n') if msg.strip()]
        if not messages_list:
            messages_list = ['Hello!']
        
        delay = config['delay']
        name_prefix = config.get('name_prefix', '')
        message_index = 0
        
        # Main loop
        while True:
            current_config = get_inbox_config(user_id)
            if not current_config or not current_config.get('running', 0):
                if log_callback:
                    log_callback("🛑 Automation stopped")
                break
            
            message = messages_list[message_index % len(messages_list)]
            full_message = f"{name_prefix} {message}".strip() if name_prefix else message
            
            if send_message_to_input(driver, message_input, full_message, log_callback):
                messages_sent += 1
                update_inbox_running(user_id, 1, messages_sent)
                message_index += 1
            
            time.sleep(delay)
        
        if log_callback:
            log_callback(f"📊 Total messages sent: {messages_sent}")
        
        update_inbox_running(user_id, 0, messages_sent)
        return messages_sent
        
    except Exception as e:
        logger.error(f"Inbox error: {e}")
        if log_callback:
            log_callback(f"💥 Error: {str(e)}")
        update_inbox_running(user_id, 0)
        return 0
    finally:
        if driver:
            driver.quit()

def start_inbox_automation(user_id: int, config: Dict, chat_id: int):
    def run_with_logging():
        def log(msg):
            asyncio.run_coroutine_threadsafe(
                send_telegram_log(chat_id, msg),
                loop
            )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        run_inbox_automation(user_id, config, chat_id, log)
    
    thread = threading.Thread(target=run_with_logging)
    thread.daemon = True
    thread.start()
    active_inbox_threads[user_id] = thread

# ==================== GROUP AUTOMATION ====================
def run_group_automation(user_id: int, config: Dict, chat_id: int, log_callback=None):
    driver = None
    messages_sent = 0
    
    try:
        if log_callback:
            log_callback("🚀 Starting GROUP automation...")
            log_callback(f"👥 Group ID: {config['group_id']}")
            log_callback(f"📛 Group Name: {config['group_name']}")
            log_callback(f"⏱️ Delay: {config['delay']}s")
        
        driver = setup_browser(log_callback)
        
        # Login with cookies
        if not add_facebook_cookies(driver, config['cookies'], log_callback):
            update_group_running(user_id, 0)
            return 0
        
        # Navigate to group
        group_id = config['group_id'].strip()
        driver.get(f'https://www.facebook.com/groups/{group_id}')
        time.sleep(8)
        
        # Find comment input
        comment_input = find_comment_input(driver, log_callback)
        if not comment_input:
            if log_callback:
                log_callback("❌ Comment input not found! Trying alternative...")
                # Try to find post box
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                comment_input = find_comment_input(driver, log_callback)
            
            if not comment_input:
                log_callback("❌ Could not find comment input!")
                update_group_running(user_id, 0)
                return 0
        
        # Prepare messages
        messages_list = [msg.strip() for msg in config['messages'].split('\n') if msg.strip()]
        if not messages_list:
            messages_list = ['Hello!']
        
        delay = config['delay']
        message_index = 0
        
        # Main loop
        while True:
            current_config = get_group_config(user_id)
            if not current_config or not current_config.get('running', 0):
                if log_callback:
                    log_callback("🛑 Group automation stopped")
                break
            
            message = messages_list[message_index % len(messages_list)]
            
            if send_message_to_input(driver, comment_input, message, log_callback):
                messages_sent += 1
                update_group_running(user_id, 1, messages_sent)
                message_index += 1
            
            time.sleep(delay)
        
        if log_callback:
            log_callback(f"📊 Total group messages sent: {messages_sent}")
        
        update_group_running(user_id, 0, messages_sent)
        return messages_sent
        
    except Exception as e:
        logger.error(f"Group error: {e}")
        if log_callback:
            log_callback(f"💥 Error: {str(e)}")
        update_group_running(user_id, 0)
        return 0
    finally:
        if driver:
            driver.quit()

def start_group_automation(user_id: int, config: Dict, chat_id: int):
    def run_with_logging():
        def log(msg):
            asyncio.run_coroutine_threadsafe(
                send_telegram_log(chat_id, msg),
                loop
            )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        run_group_automation(user_id, config, chat_id, log)
    
    thread = threading.Thread(target=run_with_logging)
    thread.daemon = True
    thread.start()
    active_group_threads[user_id] = thread

async def send_telegram_log(chat_id: int, message: str):
    """Send log message to Telegram"""
    try:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        await app.bot.send_message(chat_id=chat_id, text=message)
        await app.shutdown()
    except:
        pass

# ==================== TELEGRAM BOT HANDLERS ====================
class AutomationBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start_command)],
            states={
                CHOOSING_AUTOMATION: [CallbackQueryHandler(self.automation_choice)],
                WAITING_COOKIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_cookies)],
                WAITING_INBOX_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_inbox_chat_id)],
                WAITING_NAME_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_name_prefix)],
                WAITING_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_delay)],
                WAITING_MESSAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_messages)],
                WAITING_GROUP_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_id)],
                WAITING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_name)],
                WAITING_GROUP_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_delay)],
                WAITING_GROUP_MESSAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_messages)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_command)],
        )
        
        self.application.add_handler(conv_handler)
        self.application.add_handler(CommandHandler('status', self.status_command))
        self.application.add_handler(CommandHandler('stop', self.stop_command))
        self.application.add_handler(CommandHandler('approve', self.approve_command))
        self.application.add_handler(CommandHandler('listusers', self.list_users_command))
        self.application.add_handler(CommandHandler('help', self.help_command))
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id, approval_key, approved = get_or_create_user(user.id, user.username)
        
        if approved:
            keyboard = [
                [InlineKeyboardButton("📥 Inbox Automation", callback_data="inbox")],
                [InlineKeyboardButton("👥 Group Automation", callback_data="group")]
            ]
            await update.message.reply_text(
                f"✨ Welcome {user.first_name}! Choose automation type:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['user_id'] = user_id
            return CHOOSING_AUTOMATION
        else:
            message = f"""
👑 **SYAPA KING FACEBOOK BOT** 👑

Hello {user.first_name}!

🔑 **Approval Key:** `{approval_key}`

📌 **To get approved:**
1. Contact: {OWNER_FACEBOOK}
2. Send your approval key

✅ After approval, use /start again.

**Features:**
• 📥 Inbox Automation - Send messages to any conversation
• 👥 Group Automation - Send messages to groups
• 🔐 Cookie-based authentication

👑 **Owner:** {OWNER_NAME}
"""
            await update.message.reply_text(message, parse_mode='Markdown')
            return ConversationHandler.END
    
    async def automation_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "inbox":
            context.user_data['automation_type'] = 'inbox'
            await query.edit_message_text(
                "📥 **Inbox Automation Setup**\n\n"
                "Send your Facebook cookies.\n\n"
                "*How to get cookies:*\n"
                "1. Login to Facebook\n"
                "2. Press F12 → Application → Cookies\n"
                "3. Copy all cookies as:\n"
                "`c_user=123456; xs=789:...; datr=...`"
            )
            return WAITING_COOKIES
        else:
            context.user_data['automation_type'] = 'group'
            await query.edit_message_text(
                "👥 **Group Automation Setup**\n\n"
                "Send your Facebook cookies.\n\n"
                "*How to get cookies:*\n"
                "1. Login to Facebook\n"
                "2. Press F12 → Application → Cookies\n"
                "3. Copy all cookies as:\n"
                "`c_user=123456; xs=789:...; datr=...`"
            )
            return WAITING_COOKIES
    
    async def receive_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['cookies'] = update.message.text.strip()
        
        if context.user_data['automation_type'] == 'inbox':
            await update.message.reply_text(
                "✅ Cookies received!\n\n"
                "Send **Chat ID**.\n\n"
                "*How to get Chat ID:*\n"
                "Open conversation → URL: facebook.com/messages/t/`CHAT_ID`"
            )
            return WAITING_INBOX_CHAT_ID
        else:
            await update.message.reply_text(
                "✅ Cookies received!\n\n"
                "Send **Group ID**.\n\n"
                "*How to get Group ID:*\n"
                "Open group → URL: facebook.com/groups/`GROUP_ID`"
            )
            return WAITING_GROUP_ID
    
    async def receive_inbox_chat_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['chat_id'] = update.message.text.strip()
        await update.message.reply_text(
            "✅ Chat ID received!\n\n"
            "Send **Name Prefix** (optional, send 0 to skip)\n"
            "Example: [END TO END]"
        )
        return WAITING_NAME_PREFIX
    
    async def receive_name_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name_prefix = update.message.text.strip()
        context.user_data['name_prefix'] = "" if name_prefix == "0" else name_prefix
        await update.message.reply_text(
            "✅ Name prefix saved!\n\n"
            "Send **Delay** between messages (seconds)\n"
            "Example: 30"
        )
        return WAITING_DELAY
    
    async def receive_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            context.user_data['delay'] = int(update.message.text.strip())
        except:
            context.user_data['delay'] = 30
        await update.message.reply_text(
            "✅ Delay saved!\n\n"
            "Send your **Messages** (one per line)\n\n"
            "Example:\n"
            "Hello!\n"
            "How are you?\n"
            "Nice to meet you"
        )
        return WAITING_MESSAGES
    
    async def receive_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        messages = update.message.text.strip()
        user_id = context.user_data['user_id']
        chat_id = update.effective_chat.id
        
        save_inbox_config(
            user_id,
            context.user_data['chat_id'],
            context.user_data['name_prefix'],
            context.user_data['delay'],
            messages,
            context.user_data['cookies']
        )
        
        await update.message.reply_text(
            f"✅ **Inbox Configured!**\n\n"
            f"Chat ID: `{context.user_data['chat_id']}`\n"
            f"Delay: `{context.user_data['delay']}s`\n"
            f"Messages: `{len([m for m in messages.split('\\n') if m.strip()])}`\n\n"
            f"Use /start to start automation",
            parse_mode='Markdown'
        )
        
        # Ask to start
        keyboard = [[InlineKeyboardButton("▶️ Start Inbox Automation", callback_data="start_inbox")]]
        await update.message.reply_text("Start now?", reply_markup=InlineKeyboardMarkup(keyboard))
        
        return ConversationHandler.END
    
    async def receive_group_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['group_id'] = update.message.text.strip()
        await update.message.reply_text(
            "✅ Group ID received!\n\n"
            "Send **Group Name** (for reference)\n"
            "Example: My Group"
        )
        return WAITING_GROUP_NAME
    
    async def receive_group_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['group_name'] = update.message.text.strip()
        await update.message.reply_text(
            "✅ Group name saved!\n\n"
            "Send **Delay** between messages (seconds)\n"
            "Example: 30"
        )
        return WAITING_GROUP_DELAY
    
    async def receive_group_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            context.user_data['delay'] = int(update.message.text.strip())
        except:
            context.user_data['delay'] = 30
        await update.message.reply_text(
            "✅ Delay saved!\n\n"
            "Send your **Messages** (one per line)\n\n"
            "Example:\n"
            "Hello everyone!\n"
            "Check this out!\n"
            "Great post!"
        )
        return WAITING_GROUP_MESSAGES
    
    async def receive_group_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        messages = update.message.text.strip()
        user_id = context.user_data['user_id']
        chat_id = update.effective_chat.id
        
        save_group_config(
            user_id,
            context.user_data['group_id'],
            context.user_data['group_name'],
            context.user_data['delay'],
            messages,
            context.user_data['cookies']
        )
        
        await update.message.reply_text(
            f"✅ **Group Configured!**\n\n"
            f"Group ID: `{context.user_data['group_id']}`\n"
            f"Group Name: `{context.user_data['group_name']}`\n"
            f"Delay: `{context.user_data['delay']}s`\n"
            f"Messages: `{len([m for m in messages.split('\\n') if m.strip()])}`\n\n"
            f"Use /start to start automation",
            parse_mode='Markdown'
        )
        
        # Ask to start
        keyboard = [[InlineKeyboardButton("▶️ Start Group Automation", callback_data="start_group")]]
        await update.message.reply_text("Start now?", reply_markup=InlineKeyboardMarkup(keyboard))
        
        return ConversationHandler.END
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id, _, approved = get_or_create_user(user.id, user.username)
        
        inbox_config = get_inbox_config(user_id)
        group_config = get_group_config(user_id)
        
        status_text = f"📊 **Your Status**\n\n"
        status_text += f"User: {user.first_name}\n"
        status_text += f"Approved: {'✅ Yes' if approved else '❌ No'}\n\n"
        
        if inbox_config:
            status_text += f"**📥 Inbox:**\n"
            status_text += f"• Chat: `{inbox_config['chat_id'][:20]}...`\n"
            status_text += f"• Status: {'🟢 Running' if inbox_config['running'] else '🔴 Stopped'}\n"
            status_text += f"• Sent: {inbox_config['message_count']}\n\n"
        
        if group_config:
            status_text += f"**👥 Group:**\n"
            status_text += f"• Group: `{group_config['group_name'][:20] or group_config['group_id'][:20]}...`\n"
            status_text += f"• Status: {'🟢 Running' if group_config['running'] else '🔴 Stopped'}\n"
            status_text += f"• Sent: {group_config['message_count']}\n\n"
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id, _, _ = get_or_create_user(user.id, user.username)
        
        inbox_config = get_inbox_config(user_id)
        group_config = get_group_config(user_id)
        
        if inbox_config and inbox_config['running']:
            update_inbox_running(user_id, 0)
            await update.message.reply_text("🛑 Inbox automation stopped!")
        
        if group_config and group_config['running']:
            update_group_running(user_id, 0)
            await update.message.reply_text("🛑 Group automation stopped!")
        
        if not inbox_config and not group_config:
            await update.message.reply_text("No active automation found.")
    
    async def approve_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ Unauthorized")
            return
        
        try:
            telegram_id = int(context.args[0])
            if approve_user(telegram_id):
                await update.message.reply_text(f"✅ User {telegram_id} approved!")
            else:
                await update.message.reply_text(f"⚠️ User {telegram_id} not found")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /approve <telegram_id>")
    
    async def list_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ Unauthorized")
            return
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT telegram_id, username, approved FROM users')
        users = cursor.fetchall()
        conn.close()
        
        if users:
            text = "👥 **Users**\n\n"
            for uid, name, approved in users:
                status = "✅" if approved else "⏳"
                text += f"{status} `{uid}` - {name or 'No name'}\n"
            await update.message.reply_text(text, parse_mode='Markdown')
        else:
            await update.message.reply_text("No users")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = f"""
🤖 **SYAPA KING FACEBOOK BOT**

**Commands:**
/start - Configure automation
/status - Check status
/stop - Stop all automations
/help - This help

**Admin:**
/approve <id> - Approve user
/listusers - List users

**Features:**
• 📥 Inbox Automation - Send messages to conversations
• 👥 Group Automation - Send messages to groups
• 🔐 Cookie-based authentication

For support: {OWNER_FACEBOOK}
"""
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Cancelled. Use /start to begin again.")
        return ConversationHandler.END
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again."
            )
    
    def run(self):
        init_db()
        logger.info("Starting Facebook Automation Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== MAIN ====================
if __name__ == "__main__":
    # For Render - keep alive
    import threading
    import socket
    
    def keep_alive():
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("0.0.0.0", int(os.environ.get("PORT", 8080))))
            server.listen(1)
            print(f"Keep-alive server running on port {os.environ.get('PORT', 8080)}")
            while True:
                conn, addr = server.accept()
                conn.sendall(b"Bot is running!")
                conn.close()
        except Exception as e:
            print(f"Keep-alive server error: {e}")
    
    # Start keep-alive in background
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # Start bot
    bot = AutomationBot()
    bot.run()
