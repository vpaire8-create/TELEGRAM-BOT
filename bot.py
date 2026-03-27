#!/usr/bin/env python3
"""
SYAPA KING - FACEBOOK AUTOMATION BOT
Supports: Inbox Messaging, Group Messaging, Group Lock
Deployment: Render.com
"""

import sqlite3
import hashlib
import json
import os
import time
import threading
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Tuple

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
ADMIN_USER_IDS = [int(x) for x in os.environ.get('ADMIN_USER_IDS', '').split(',') if x]  # Add in Render env vars
OWNER_NAME = "SYAPA KING"
OWNER_FACEBOOK = os.environ.get('OWNER_FACEBOOK', 'https://www.facebook.com/share/168AJz6Ehm/')

# Use persistent storage path for Render
DATA_DIR = Path(os.environ.get('DATA_DIR', '/opt/render/project/src/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'facebook_bot.db'
ENCRYPTION_KEY_FILE = DATA_DIR / '.encryption_key'

# Conversation states
(CHOOSING_AUTOMATION, WAITING_COOKIES, WAITING_TARGET_ID, WAITING_NAME_PREFIX,
 WAITING_DELAY, WAITING_MESSAGES, WAITING_GROUP_NAME, WAITING_NICKNAMES) = range(8)

# Automation types
AUTO_INBOX = "inbox"
AUTO_GROUP_MSG = "group_msg"
AUTO_GROUP_LOCK = "group_lock"

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
    except Exception:
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved INTEGER DEFAULT 0,
            approval_key TEXT
        )
    ''')
    
    # Messaging configs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messaging_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            name_prefix TEXT,
            delay INTEGER DEFAULT 30,
            messages TEXT,
            cookies_encrypted TEXT,
            running INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Group lock configs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_lock_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT,
            nicknames TEXT,
            enabled INTEGER DEFAULT 0,
            cookies_encrypted TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")

def get_or_create_user(telegram_id: int, username: str = None) -> Tuple[int, str, int]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, approval_key, approved FROM users WHERE telegram_id = ?', (telegram_id,))
    user = cursor.fetchone()
    
    if user:
        conn.close()
        return user[0], user[1], user[2]
    else:
        approval_key = hashlib.sha256(f"{telegram_id}:{time.time()}".encode()).hexdigest()[:12].upper()
        cursor.execute('''
            INSERT INTO users (telegram_id, username, approval_key)
            VALUES (?, ?, ?)
        ''', (telegram_id, username, approval_key))
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

def save_messaging_config(user_id: int, target_type: str, target_id: str, 
                           name_prefix: str, delay: int, messages: str, cookies: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    encrypted_cookies = encrypt_data(cookies)
    
    cursor.execute('''
        INSERT OR REPLACE INTO messaging_configs 
        (user_id, target_type, target_id, name_prefix, delay, messages, cookies_encrypted, running)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    ''', (user_id, target_type, target_id, name_prefix, delay, messages, encrypted_cookies))
    
    conn.commit()
    conn.close()

def get_messaging_config(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT target_type, target_id, name_prefix, delay, messages, cookies_encrypted, running
        FROM messaging_configs WHERE user_id = ?
    ''', (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'target_type': row[0],
            'target_id': row[1],
            'name_prefix': row[2] or '',
            'delay': row[3] or 30,
            'messages': row[4] or '',
            'cookies': decrypt_data(row[5]) if row[5] else '',
            'running': row[6] or 0
        }
    return None

def update_messaging_running(user_id: int, running: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE messaging_configs SET running = ? WHERE user_id = ?', (running, user_id))
    conn.commit()
    conn.close()

def save_group_lock_config(user_id: int, group_id: str, group_name: str, nicknames: Dict, cookies: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    encrypted_cookies = encrypt_data(cookies)
    nicknames_json = json.dumps(nicknames)
    
    cursor.execute('''
        INSERT OR REPLACE INTO group_lock_configs 
        (user_id, group_id, group_name, nicknames, cookies_encrypted, enabled)
        VALUES (?, ?, ?, ?, ?, 1)
    ''', (user_id, group_id, group_name, nicknames_json, encrypted_cookies))
    
    conn.commit()
    conn.close()

def get_group_lock_config(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT group_id, group_name, nicknames, cookies_encrypted, enabled
        FROM group_lock_configs WHERE user_id = ?
    ''', (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        try:
            nicknames = json.loads(row[2]) if row[2] else {}
        except:
            nicknames = {}
        
        return {
            'group_id': row[0],
            'group_name': row[1] or '',
            'nicknames': nicknames,
            'cookies': decrypt_data(row[3]) if row[3] else '',
            'enabled': bool(row[4])
        }
    return None

def update_group_lock_enabled(user_id: int, enabled: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE group_lock_configs SET enabled = ? WHERE user_id = ?', (enabled, user_id))
    conn.commit()
    conn.close()

# ==================== SELENIUM BROWSER SETUP FOR RENDER ====================
def setup_browser():
    """Setup Chrome browser for headless environment (Render)"""
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-setuid-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--remote-debugging-port=9222')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    # For Render - chromium paths
    chromium_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/chrome'
    ]
    
    for path in chromium_paths:
        if Path(path).exists():
            chrome_options.binary_location = path
            logger.info(f"Using Chrome binary: {path}")
            break
    
    # Chromedriver path for Render
    chromedriver_paths = [
        '/usr/bin/chromedriver',
        '/usr/local/bin/chromedriver'
    ]
    
    driver_path = None
    for path in chromedriver_paths:
        if Path(path).exists():
            driver_path = path
            logger.info(f"Using ChromeDriver: {path}")
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
        logger.error(f"Browser setup failed: {e}")
        raise

def add_facebook_cookies(driver, cookies_str: str):
    if not cookies_str:
        return
    
    driver.get('https://www.facebook.com/')
    time.sleep(3)
    
    cookie_pairs = cookies_str.split(';')
    for pair in cookie_pairs:
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            try:
                driver.add_cookie({
                    'name': name.strip(),
                    'value': value.strip(),
                    'domain': '.facebook.com',
                    'path': '/'
                })
            except Exception:
                pass

# ==================== MESSAGE SENDING ====================
def find_message_input(driver, log_callback=None):
    time.sleep(5)
    
    selectors = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'div[aria-label*="message" i][contenteditable="true"]',
        'div[aria-label*="Message" i][contenteditable="true"]',
        '[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea'
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    return element
        except:
            continue
    
    return None

def send_messages_to_target(config: Dict, user_id: int, log_callback=None):
    driver = None
    messages_sent = 0
    
    try:
        target_type = config['target_type']
        target_id = config['target_id']
        
        if log_callback:
            log_callback(f"Starting {target_type.upper()} messaging to: {target_id}")
        
        driver = setup_browser()
        add_facebook_cookies(driver, config['cookies'])
        
        if target_type == AUTO_INBOX:
            driver.get(f'https://www.facebook.com/messages/t/{target_id}')
        else:
            driver.get(f'https://www.facebook.com/groups/{target_id}')
        
        time.sleep(8)
        
        message_input = find_message_input(driver, log_callback)
        if not message_input:
            if log_callback:
                log_callback("Message input not found!")
            update_messaging_running(user_id, 0)
            return 0
        
        messages_list = [msg.strip() for msg in config['messages'].split('\n') if msg.strip()]
        if not messages_list:
            messages_list = ['Hello!']
        
        delay = config['delay']
        name_prefix = config.get('name_prefix', '')
        message_index = 0
        
        while get_messaging_config(user_id) and get_messaging_config(user_id).get('running', 0):
            message = messages_list[message_index % len(messages_list)]
            full_message = f"{name_prefix} {message}".strip() if name_prefix else message
            
            try:
                driver.execute_script("""
                    arguments[0].focus();
                    arguments[0].click();
                    arguments[0].innerHTML = arguments[1];
                    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                """, message_input, full_message)
                
                time.sleep(1)
                
                send_buttons = driver.find_elements(By.CSS_SELECTOR, '[aria-label*="Send" i], [data-testid="send-button"]')
                sent = False
                for btn in send_buttons:
                    if btn.is_displayed():
                        btn.click()
                        sent = True
                        break
                
                if not sent:
                    driver.execute_script("""
                        const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13 });
                        arguments[0].dispatchEvent(event);
                    """, message_input)
                
                messages_sent += 1
                if log_callback:
                    log_callback(f"Sent #{messages_sent}: {full_message[:40]}...")
                
                message_index += 1
                time.sleep(delay)
                
            except Exception as e:
                if log_callback:
                    log_callback(f"Error: {str(e)[:80]}")
                time.sleep(5)
        
        if log_callback:
            log_callback(f"Stopped. Total: {messages_sent}")
        
        update_messaging_running(user_id, 0)
        return messages_sent
        
    except Exception as e:
        logger.error(f"Messaging error: {e}")
        if log_callback:
            log_callback(f"Fatal error: {str(e)}")
        update_messaging_running(user_id, 0)
        return 0
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def start_messaging(user_id: int, config: Dict):
    def run():
        send_messages_to_target(config, user_id)
    
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()

# ==================== GROUP LOCK ====================
def run_group_lock(user_id: int, config: Dict, log_callback=None):
    driver = None
    
    try:
        if log_callback:
            log_callback("Starting group lock monitoring...")
        
        driver = setup_browser()
        add_facebook_cookies(driver, config['cookies'])
        
        group_id = config['group_id']
        nicknames = config.get('nicknames', {})
        
        while get_group_lock_config(user_id) and get_group_lock_config(user_id).get('enabled', 0):
            if log_callback:
                log_callback(f"Scanning group {group_id}...")
            
            driver.get(f'https://www.facebook.com/groups/{group_id}')
            time.sleep(5)
            
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
            
            messages = []
            try:
                message_elements = driver.find_elements(By.CSS_SELECTOR, '[data-ad-preview="message"], div[dir="auto"][style*="text-align"]')
                for element in message_elements:
                    try:
                        text = element.text.strip()
                        if text:
                            messages.append(text)
                    except:
                        continue
            except:
                pass
            
            if messages:
                if log_callback:
                    log_callback(f"Found {len(messages)} messages")
                
                for nickname, lock_status in list(nicknames.items()):
                    if lock_status:
                        mentioned = any(nickname.lower() in msg.lower() for msg in messages)
                        if mentioned and log_callback:
                            log_callback(f"LOCKED: {nickname} was mentioned!")
            
            time.sleep(60)
            
    except Exception as e:
        logger.error(f"Group lock error: {e}")
        if log_callback:
            log_callback(f"Error: {str(e)}")
    finally:
        if driver:
            driver.quit()

def start_group_lock(user_id: int, config: Dict):
    def run():
        run_group_lock(user_id, config)
    
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()

# ==================== TELEGRAM BOT ====================
class AutomationBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', self.start_command),
                CallbackQueryHandler(self.button_callback, pattern='^(inbox|group_msg|group_lock)$'),
            ],
            states={
                CHOOSING_AUTOMATION: [CallbackQueryHandler(self.button_callback)],
                WAITING_COOKIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_cookies)],
                WAITING_TARGET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_target_id)],
                WAITING_NAME_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_name_prefix)],
                WAITING_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_delay)],
                WAITING_MESSAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_messages)],
                WAITING_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_group_name)],
                WAITING_NICKNAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_nicknames)],
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
                [InlineKeyboardButton("📥 Inbox Messaging", callback_data="inbox")],
                [InlineKeyboardButton("👥 Group Messaging", callback_data="group_msg")],
                [InlineKeyboardButton("🔒 Group Lock", callback_data="group_lock")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✨ Welcome {user.first_name}!\n\nChoose automation type:",
                reply_markup=reply_markup
            )
            context.user_data['user_id'] = user_id
            return CHOOSING_AUTOMATION
        else:
            message = f"""
👑 **SYAPA KING FACEBOOK BOT** 👑

Hello {user.first_name}!

🔑 **Approval Key:** `{approval_key}`

📌 Contact owner for approval: {OWNER_FACEBOOK}

*Features:*
• 📥 Inbox Messaging
• 👥 Group Messaging  
• 🔒 Group Lock Monitor
"""
            await update.message.reply_text(message, parse_mode='Markdown')
            return ConversationHandler.END
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data['target_type'] = query.data
        
        await query.edit_message_text(
            "Send your Facebook cookies.\n\n"
            "*How to get cookies:*\n"
            "1. Login to Facebook\n"
            "2. F12 → Application → Cookies\n"
            "3. Copy as: `name1=value1; name2=value2`",
            parse_mode='Markdown'
        )
        return WAITING_COOKIES
    
    async def receive_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['cookies'] = update.message.text.strip()
        
        target_type = context.user_data['target_type']
        
        if target_type == AUTO_GROUP_LOCK:
            await update.message.reply_text("Send the **Group ID**\nFrom URL: facebook.com/groups/`GROUP_ID`")
        else:
            type_name = "Chat ID" if target_type == AUTO_INBOX else "Group ID"
            await update.message.reply_text(f"Send the **{type_name}**")
        
        return WAITING_TARGET_ID
    
    async def receive_target_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['target_id'] = update.message.text.strip()
        
        target_type = context.user_data['target_type']
        
        if target_type == AUTO_GROUP_LOCK:
            await update.message.reply_text("Send the **Group Name** (for reference)")
            return WAITING_GROUP_NAME
        else:
            await update.message.reply_text("Send **Name Prefix** (optional, send 'none' to skip)")
            return WAITING_NAME_PREFIX
    
    async def receive_name_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name_prefix = update.message.text.strip()
        if name_prefix.lower() == 'none':
            name_prefix = ''
        context.user_data['name_prefix'] = name_prefix
        
        await update.message.reply_text("Send **Delay** between messages (seconds)\nExample: 30")
        return WAITING_DELAY
    
    async def receive_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            context.user_data['delay'] = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Send a valid number!")
            return WAITING_DELAY
        
        await update.message.reply_text(
            "Send your **Messages** (one per line)\n\n"
            "Example:\nHello!\nHow are you?"
        )
        return WAITING_MESSAGES
    
    async def receive_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        messages = update.message.text.strip()
        
        user_id = context.user_data['user_id']
        target_type = context.user_data['target_type']
        target_id = context.user_data['target_id']
        name_prefix = context.user_data.get('name_prefix', '')
        delay = context.user_data['delay']
        cookies = context.user_data['cookies']
        
        save_messaging_config(user_id, target_type, target_id, name_prefix, delay, messages, cookies)
        
        type_name = "Inbox" if target_type == AUTO_INBOX else "Group"
        
        await update.message.reply_text(
            f"✅ {type_name} Messaging Configured!\n\n"
            f"Target: `{target_id}`\n"
            f"Delay: {delay}s\n"
            f"Messages: {len(messages.split(chr(10)))}\n\n"
            f"Starting automation...",
            parse_mode='Markdown'
        )
        
        config = get_messaging_config(user_id)
        if config:
            update_messaging_running(user_id, 1)
            start_messaging(user_id, config)
        
        return ConversationHandler.END
    
    async def receive_group_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['group_name'] = update.message.text.strip()
        
        await update.message.reply_text(
            "Send **Nicknames to Lock** (one per line)\n"
            "Example:\nJohn Doe\nJane Smith"
        )
        return WAITING_NICKNAMES
    
    async def receive_nicknames(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        nicknames_text = update.message.text.strip()
        nicknames = {name.strip(): True for name in nicknames_text.split('\n') if name.strip()}
        
        user_id = context.user_data['user_id']
        group_id = context.user_data['target_id']
        group_name = context.user_data['group_name']
        cookies = context.user_data['cookies']
        
        save_group_lock_config(user_id, group_id, group_name, nicknames, cookies)
        
        await update.message.reply_text(
            f"✅ Group Lock Configured!\n\n"
            f"Group: {group_name}\n"
            f"Locked Users: {len(nicknames)}\n\n"
            f"Starting monitoring...",
            parse_mode='Markdown'
        )
        
        config = get_group_lock_config(user_id)
        if config:
            start_group_lock(user_id, config)
        
        return ConversationHandler.END
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id, _, approved = get_or_create_user(user.id, user.username)
        
        messaging_config = get_messaging_config(user_id)
        group_config = get_group_lock_config(user_id)
        
        status_text = f"📊 **Status**\n\n"
        status_text += f"User: {user.first_name}\n"
        status_text += f"Approved: {'✅ Yes' if approved else '❌ No'}\n\n"
        
        if messaging_config:
            type_name = "Inbox" if messaging_config['target_type'] == AUTO_INBOX else "Group"
            status_text += f"**{type_name} Messaging:**\n"
            status_text += f"• Target: {messaging_config['target_id'][:20]}...\n"
            status_text += f"• Status: {'🟢 Running' if messaging_config['running'] else '🔴 Stopped'}\n\n"
        
        if group_config:
            status_text += f"**Group Lock:**\n"
            status_text += f"• Group: {group_config['group_name'][:20]}\n"
            status_text += f"• Status: {'🟢 Monitoring' if group_config['enabled'] else '🔴 Stopped'}\n"
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id, _, _ = get_or_create_user(user.id, user.username)
        
        update_messaging_running(user_id, 0)
        update_group_lock_enabled(user_id, 0)
        
        await update.message.reply_text("🛑 All automations stopped!")
    
    async def approve_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ Not authorized.")
            return
        
        try:
            telegram_id = int(context.args[0])
            if approve_user(telegram_id):
                await update.message.reply_text(f"✅ User {telegram_id} approved!")
            else:
                await update.message.reply_text(f"⚠️ User {telegram_id} not found.")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /approve <telegram_id>")
    
    async def list_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_USER_IDS:
            await update.message.reply_text("❌ Not authorized.")
            return
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT telegram_id, username, approved FROM users')
        users = cursor.fetchall()
        conn.close()
        
        if users:
            text = "👥 **Users**\n\n"
            for uid, username, approved in users:
                status = "✅" if approved else "⏳"
                text += f"{status} `{uid}` - {username or 'No username'}\n"
            await update.message.reply_text(text, parse_mode='Markdown')
        else:
            await update.message.reply_text("No users.")
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Cancelled. Use /start to begin again.")
        return ConversationHandler.END
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = f"""
🤖 **Commands:**
/start - Start bot
/status - Check status
/stop - Stop all
/help - This help

**Features:**
• 📥 Inbox Messaging
• 👥 Group Messaging
• 🔒 Group Lock

**Admin:**
/approve <id> - Approve user
/listusers - List users

Owner: {OWNER_FACEBOOK}
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}")
    
    def run(self):
        init_db()
        logger.info("Starting Facebook Automation Bot on Render...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    bot = AutomationBot()
    bot.run()