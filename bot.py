#!/usr/bin/env python3
"""
SYAPA KING - FACEBOOK AUTOMATION BOT (TELEGRAM VERSION)
Based on working Streamlit app code
Supports: Inbox Automation & Group Messaging Automation
"""

import sqlite3
import hashlib
import json
import os
import time
import threading
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass

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
# Render environment variables
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '7791213862:AAFvGyuCCVZqpnQQwjZBbu89drzuiJPAcJM')
ADMIN_USER_IDS = [int(x) for x in os.environ.get('ADMIN_USER_IDS', '7791213862').split(',')]
OWNER_NAME = os.environ.get('OWNER_NAME', 'SYAPA KING')
OWNER_FACEBOOK = os.environ.get('OWNER_FACEBOOK', 'https://www.facebook.com/share/168AJz6Ehm/')

# Data directory for Render persistence - FIXED PATH
DATA_DIR = Path(os.environ.get('DATA_DIR', '/tmp/data'))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    # Fallback to /app/data if /tmp/data fails
    DATA_DIR = Path('/app/data')
    DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'facebook_bot.db'
ENCRYPTION_KEY_FILE = DATA_DIR / '.encryption_key'

# Conversation states
(CHOOSING_AUTOMATION, WAITING_COOKIES, WAITING_INBOX_CHAT_ID, WAITING_NAME_PREFIX,
 WAITING_DELAY, WAITING_MESSAGES, WAITING_GROUP_ID, WAITING_GROUP_MESSAGES,
 WAITING_GROUP_DELAY) = range(9)

# Automation types
AUTO_INBOX = "inbox"
AUTO_GROUP = "group"

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ENCRYPTION ====================
def get_encryption_key():
    """Get or create encryption key"""
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
    """Initialize database"""
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
    
    # Inbox config table (same as streamlit app)
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
    
    # Group config table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id TEXT,
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
    """Get existing user or create new one"""
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
    """Approve a user"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET approved = 1 WHERE telegram_id = ?', (telegram_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

def save_inbox_config(user_id: int, chat_id: str, name_prefix: str, delay: int, messages: str, cookies: str):
    """Save inbox config"""
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
    """Get inbox config"""
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
    """Update inbox running status"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if message_count is not None:
        cursor.execute('UPDATE inbox_configs SET running = ?, message_count = ? WHERE user_id = ?',
                      (running, message_count, user_id))
    else:
        cursor.execute('UPDATE inbox_configs SET running = ? WHERE user_id = ?', (running, user_id))
    conn.commit()
    conn.close()

def save_group_config(user_id: int, group_id: str, delay: int, messages: str, cookies: str):
    """Save group config"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    encrypted_cookies = encrypt_data(cookies)
    
    cursor.execute('''
        INSERT OR REPLACE INTO group_configs 
        (user_id, group_id, delay, messages, cookies_encrypted)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, group_id, delay, messages, encrypted_cookies))
    
    conn.commit()
    conn.close()

def get_group_config(user_id: int) -> Optional[Dict]:
    """Get group config"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT group_id, delay, messages, cookies_encrypted, running, message_count
        FROM group_configs WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'group_id': row[0],
            'delay': row[1] or 30,
            'messages': row[2] or '',
            'cookies': decrypt_data(row[3]) if row[3] else '',
            'running': row[4] or 0,
            'message_count': row[5] or 0
        }
    return None

def update_group_running(user_id: int, running: int, message_count: int = None):
    """Update group running status"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if message_count is not None:
        cursor.execute('UPDATE group_configs SET running = ?, message_count = ? WHERE user_id = ?',
                      (running, message_count, user_id))
    else:
        cursor.execute('UPDATE group_configs SET running = ? WHERE user_id = ?', (running, user_id))
    conn.commit()
    conn.close()

# ==================== SELENIUM HELPERS (Same as working Streamlit app) ====================
def setup_browser(log_callback=None):
    """Setup Chrome browser - exact same as working streamlit app"""
    if log_callback:
        log_callback("🌐 Setting up Chrome browser...")
    
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-setuid-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    # Find Chromium/Chrome
    chromium_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/chrome'
    ]
    
    for path in chromium_paths:
        if Path(path).exists():
            chrome_options.binary_location = path
            if log_callback:
                log_callback(f"✅ Found browser at: {path}")
            break
    
    # Find ChromeDriver
    chromedriver_paths = [
        '/usr/bin/chromedriver',
        '/usr/local/bin/chromedriver'
    ]
    
    driver_path = None
    for path in chromedriver_paths:
        if Path(path).exists():
            driver_path = path
            if log_callback:
                log_callback(f"✅ Found chromedriver at: {path}")
            break
    
    try:
        if driver_path:
            service = Service(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            driver = webdriver.Chrome(options=chrome_options)
        
        driver.set_window_size(1920, 1080)
        if log_callback:
            log_callback("✅ Browser setup complete!")
        return driver
    except Exception as e:
        if log_callback:
            log_callback(f"❌ Browser setup failed: {e}")
        raise

def find_message_input(driver, log_callback=None):
    """Find message input - exact same as working streamlit app"""
    if log_callback:
        log_callback("🔍 Finding message input...")
    time.sleep(5)
    
    # Scroll to bottom and top
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
    except:
        pass
    
    message_input_selectors = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'div[aria-label*="message" i][contenteditable="true"]',
        'div[aria-label*="Message" i][contenteditable="true"]',
        'div[contenteditable="true"][spellcheck="true"]',
        '[role="textbox"][contenteditable="true"]',
        'textarea[placeholder*="message" i]',
        'div[aria-placeholder*="message" i]',
        'div[data-placeholder*="message" i]',
        '[contenteditable="true"]',
        'textarea',
        'input[type="text"]'
    ]
    
    for idx, selector in enumerate(message_input_selectors):
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                try:
                    is_editable = driver.execute_script("""
                        return arguments[0].contentEditable === 'true' ||
                               arguments[0].tagName === 'TEXTAREA' ||
                               arguments[0].tagName === 'INPUT';
                    """, element)
                    
                    if is_editable and element.is_displayed():
                        if log_callback:
                            log_callback(f"✅ Found message input with selector: {selector[:50]}")
                        return element
                except:
                    continue
        except:
            continue
    
    return None

# ==================== INBOX AUTOMATION (Working from Streamlit) ====================
def send_inbox_messages(config: Dict, user_id: int, log_callback=None):
    """Send messages to inbox - exact same working code from streamlit"""
    driver = None
    messages_sent = 0
    
    try:
        if log_callback:
            log_callback("🚀 Starting inbox automation...")
            log_callback(f"📱 Chat ID: {config['chat_id']}")
            log_callback(f"⏱️ Delay: {config['delay']}s")
        
        driver = setup_browser(log_callback)
        
        # Navigate to Facebook
        if log_callback:
            log_callback("🌐 Opening Facebook...")
        driver.get('https://www.facebook.com/')
        time.sleep(8)
        
        # Add cookies
        if config['cookies'] and config['cookies'].strip():
            if log_callback:
                log_callback("🍪 Adding cookies...")
            cookie_array = config['cookies'].split(';')
            for cookie in cookie_array:
                cookie_trimmed = cookie.strip()
                if cookie_trimmed and '=' in cookie_trimmed:
                    first_equal_index = cookie_trimmed.find('=')
                    name = cookie_trimmed[:first_equal_index].strip()
                    value = cookie_trimmed[first_equal_index + 1:].strip()
                    try:
                        driver.add_cookie({
                            'name': name,
                            'value': value,
                            'domain': '.facebook.com',
                            'path': '/'
                        })
                    except:
                        pass
        
        # Refresh to apply cookies
        driver.refresh()
        time.sleep(5)
        
        # Check if logged in
        if 'login' in driver.current_url.lower():
            if log_callback:
                log_callback("❌ Not logged in! Cookies may be expired.")
            update_inbox_running(user_id, 0)
            return 0
        
        if log_callback:
            log_callback("✅ Successfully logged in!")
        
        # Navigate to conversation
        chat_id = config['chat_id'].strip()
        if log_callback:
            log_callback(f"💬 Opening conversation: {chat_id}")
        
        # Try both URL formats
        urls = [
            f'https://www.facebook.com/messages/t/{chat_id}',
            f'https://www.facebook.com/messages/e2ee/t/{chat_id}'
        ]
        
        for url in urls:
            try:
                driver.get(url)
                time.sleep(8)
                if 'messages' in driver.current_url:
                    if log_callback:
                        log_callback(f"✅ Opened conversation: {url}")
                    break
            except:
                continue
        
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
        
        if log_callback:
            log_callback(f"📨 Starting message loop with {len(messages_list)} messages")
        
        # Main sending loop
        while True:
            # Check if still running
            current_config = get_inbox_config(user_id)
            if not current_config or not current_config.get('running', 0):
                if log_callback:
                    log_callback("🛑 Automation stopped by user")
                break
            
            message = messages_list[message_index % len(messages_list)]
            full_message = f"{name_prefix} {message}".strip() if name_prefix else message
            
            try:
                # Type message
                driver.execute_script("""
                    const element = arguments[0];
                    const message = arguments[1];
                    
                    element.scrollIntoView({behavior: 'smooth', block: 'center'});
                    element.focus();
                    element.click();
                    
                    if (element.tagName === 'DIV') {
                        element.textContent = message;
                        element.innerHTML = message;
                    } else {
                        element.value = message;
                    }
                    
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                """, message_input, full_message)
                
                time.sleep(1)
                
                # Send message
                sent = driver.execute_script("""
                    const sendButtons = document.querySelectorAll('[aria-label*="Send" i]:not([aria-label*="like" i]), [data-testid="send-button"]');
                    
                    for (let btn of sendButtons) {
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return 'button_clicked';
                        }
                    }
                    
                    // Try Enter key
                    const element = arguments[0];
                    const enterEvent = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true });
                    element.dispatchEvent(enterEvent);
                    return 'enter_key';
                """, message_input)
                
                messages_sent += 1
                if log_callback:
                    log_callback(f"✅ Message #{messages_sent}: {full_message[:50]}...")
                
                # Update message count in DB
                update_inbox_running(user_id, 1, messages_sent)
                
                message_index += 1
                time.sleep(delay)
                
            except Exception as e:
                if log_callback:
                    log_callback(f"❌ Error: {str(e)[:100]}")
                time.sleep(5)
        
        if log_callback:
            log_callback(f"📊 Inbox automation stopped. Total messages: {messages_sent}")
        
        update_inbox_running(user_id, 0, messages_sent)
        return messages_sent
        
    except Exception as e:
        logger.error(f"Inbox automation error: {e}")
        if log_callback:
            log_callback(f"💥 Fatal error: {str(e)}")
        update_inbox_running(user_id, 0)
        return 0
    finally:
        if driver:
            try:
                driver.quit()
                if log_callback:
                    log_callback("🔒 Browser closed")
            except:
                pass

def start_inbox_automation(user_id: int, config: Dict, log_callback=None):
    """Start inbox automation in background thread"""
    def run():
        send_inbox_messages(config, user_id, log_callback)
    
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()

# ==================== GROUP AUTOMATION ====================
def send_group_messages(config: Dict, user_id: int, log_callback=None):
    """Send messages to group"""
    driver = None
    messages_sent = 0
    
    try:
        if log_callback:
            log_callback("🚀 Starting group automation...")
            log_callback(f"👥 Group ID: {config['group_id']}")
            log_callback(f"⏱️ Delay: {config['delay']}s")
        
        driver = setup_browser(log_callback)
        
        # Navigate to Facebook
        driver.get('https://www.facebook.com/')
        time.sleep(8)
        
        # Add cookies
        if config['cookies'] and config['cookies'].strip():
            cookie_array = config['cookies'].split(';')
            for cookie in cookie_array:
                cookie_trimmed = cookie.strip()
                if cookie_trimmed and '=' in cookie_trimmed:
                    first_equal_index = cookie_trimmed.find('=')
                    name = cookie_trimmed[:first_equal_index].strip()
                    value = cookie_trimmed[first_equal_index + 1:].strip()
                    try:
                        driver.add_cookie({'name': name, 'value': value, 'domain': '.facebook.com', 'path': '/'})
                    except:
                        pass
        
        driver.refresh()
        time.sleep(5)
        
        if 'login' in driver.current_url.lower():
            if log_callback:
                log_callback("❌ Not logged in!")
            update_group_running(user_id, 0)
            return 0
        
        # Navigate to group
        group_id = config['group_id'].strip()
        if log_callback:
            log_callback(f"👥 Opening group: {group_id}")
        
        driver.get(f'https://www.facebook.com/groups/{group_id}')
        time.sleep(8)
        
        # Find comment box
        comment_input = None
        selectors = [
            'div[contenteditable="true"][aria-label*="Write a comment" i]',
            'div[contenteditable="true"][aria-label*="comment" i]',
            'div[contenteditable="true"]',
            'textarea'
        ]
        
        for selector in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        comment_input = element
                        if log_callback:
                            log_callback(f"✅ Found comment input")
                        break
                if comment_input:
                    break
            except:
                continue
        
        if not comment_input:
            if log_callback:
                log_callback("❌ Comment input not found!")
            update_group_running(user_id, 0)
            return 0
        
        # Prepare messages
        messages_list = [msg.strip() for msg in config['messages'].split('\n') if msg.strip()]
        if not messages_list:
            messages_list = ['Hello!']
        
        delay = config['delay']
        message_index = 0
        
        # Main sending loop
        while True:
            current_config = get_group_config(user_id)
            if not current_config or not current_config.get('running', 0):
                break
            
            message = messages_list[message_index % len(messages_list)]
            
            try:
                # Type comment
                driver.execute_script("""
                    arguments[0].focus();
                    arguments[0].click();
                    arguments[0].innerHTML = arguments[1];
                    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                """, comment_input, message)
                
                time.sleep(1)
                
                # Find and click post button
                post_buttons = driver.find_elements(By.CSS_SELECTOR, 
                    '[aria-label*="Post" i], [aria-label*="Comment" i], button[type="submit"]')
                
                posted = False
                for btn in post_buttons:
                    if btn.is_displayed():
                        btn.click()
                        posted = True
                        break
                
                if not posted:
                    driver.execute_script("""
                        const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13 });
                        arguments[0].dispatchEvent(event);
                    """, comment_input)
                
                messages_sent += 1
                if log_callback:
                    log_callback(f"✅ Group message #{messages_sent}: {message[:50]}...")
                
                update_group_running(user_id, 1, messages_sent)
                message_index += 1
                time.sleep(delay)
                
            except Exception as e:
                if log_callback:
                    log_callback(f"❌ Error: {str(e)[:100]}")
                time.sleep(5)
        
        if log_callback:
            log_callback(f"📊 Group automation stopped. Total messages: {messages_sent}")
        
        update_group_running(user_id, 0, messages_sent)
        return messages_sent
        
    except Exception as e:
        logger.error(f"Group automation error: {e}")
        if log_callback:
            log_callback(f"💥 Fatal error: {str(e)}")
        update_group_running(user_id, 0)
        return 0
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def start_group_automation(user_id: int, config: Dict, log_callback=None):
    """Start group automation in background thread"""
    def run():
        send_group_messages(config, user_id, log_callback)
    
    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()

# ==================== TELEGRAM BOT HANDLERS ====================
class AutomationBot:
    def __init__(self):
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.setup_handlers()
        self.user_log_callbacks = {}
    
    def setup_handlers(self):
        """Setup all handlers"""
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
        """Start command - show automation choice"""
        user = update.effective_user
        user_id, approval_key, approved = get_or_create_user(user.id, user.username)
        
        if approved:
            keyboard = [
                [InlineKeyboardButton("📥 Inbox Automation", callback_data="inbox")],
                [InlineKeyboardButton("👥 Group Automation", callback_data="group")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✨ Welcome {user.first_name}! ✨\n\n"
                f"Choose automation type:",
                reply_markup=reply_markup
            )
            context.user_data['user_id'] = user_id
            return CHOOSING_AUTOMATION
        else:
            message = f"""
👑 **SYAPA KING FACEBOOK AUTOMATION BOT** 👑

Hello {user.first_name}!

🔑 **Your Approval Key:** `{approval_key}`

📌 **To get approved:**
1. Contact owner: {OWNER_FACEBOOK}
2. Send your approval key
3. Wait for approval

✅ After approval, use /start again.

*Features:*
• 📥 Inbox Automation - Send messages to any conversation
• 👥 Group Automation - Send messages to groups
• 🔐 Cookie-based authentication
• ⏰ 24/7 automation

👑 **Owner:** {OWNER_NAME}
"""
            await update.message.reply_text(message, parse_mode='Markdown')
            return ConversationHandler.END
    
    async def automation_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle automation type choice"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "inbox":
            context.user_data['automation_type'] = 'inbox'
            await query.edit_message_text(
                "📥 **Inbox Automation Setup**\n\n"
                "Send your Facebook cookies.\n\n"
                "*How to get cookies:*\n"
                "1. Login to Facebook\n"
                "2. Open Developer Tools (F12)\n"
                "3. Go to Application → Cookies → https://www.facebook.com\n"
                "4. Copy all cookies as:\n"
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
                "2. Open Developer Tools (F12)\n"
                "3. Go to Application → Cookies → https://www.facebook.com\n"
                "4. Copy all cookies as:\n"
                "`c_user=123456; xs=789:...; datr=...`"
            )
            return WAITING_COOKIES
    
    async def receive_cookies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive cookies"""
        cookies = update.message.text.strip()
        context.user_data['cookies'] = cookies
        
        if context.user_data['automation_type'] == 'inbox':
            await update.message.reply_text(
                "✅ Cookies received!\n\n"
                "Now send the **Chat ID**.\n\n"
                "*How to get Chat ID:*\n"
                "Open the conversation → URL:\n"
                "facebook.com/messages/t/`CHAT_ID`"
            )
            return WAITING_INBOX_CHAT_ID
        else:
            await update.message.reply_text(
                "✅ Cookies received!\n\n"
                "Now send the **Group ID**.\n\n"
                "*How to get Group ID:*\n"
                "Open the group → URL:\n"
                "facebook.com/groups/`GROUP_ID`"
            )
            return WAITING_GROUP_ID
    
    async def receive_inbox_chat_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive chat ID"""
        chat_id = update.message.text.strip()
        context.user_data['chat_id'] = chat_id
        
        await update.message.reply_text(
            "✅ Chat ID received!\n\n"
            "Send **Name Prefix** (optional, send 0 to skip)\n"
            "Example: [END TO END]"
        )
        return WAITING_NAME_PREFIX
    
    async def receive_name_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive name prefix"""
        name_prefix = update.message.text.strip()
        if name_prefix == "0":
            name_prefix = ""
        context.user_data['name_prefix'] = name_prefix
        
        await update.message.reply_text(
            "✅ Name prefix saved!\n\n"
            "Send **Delay** between messages (seconds)\n"
            "Example: 30"
        )
        return WAITING_DELAY
    
    async def receive_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive delay"""
        try:
            delay = int(update.message.text.strip())
            context.user_data['delay'] = delay
        except:
            await update.message.reply_text("Please send a valid number")
            return WAITING_DELAY
        
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
        """Receive messages and save config"""
        messages = update.message.text.strip()
        
        user_id = context.user_data['user_id']
        chat_id = context.user_data['chat_id']
        name_prefix = context.user_data['name_prefix']
        delay = context.user_data['delay']
        cookies = context.user_data['cookies']
        
        save_inbox_config(user_id, chat_id, name_prefix, delay, messages, cookies)
        
        await update.message.reply_text(
            f"✅ **Inbox Automation Configured!** ✅\n\n"
            f"Chat ID: `{chat_id}`\n"
            f"Delay: `{delay}s`\n"
            f"Messages: `{len([m for m in messages.split('\\n') if m.strip()])}`\n\n"
            f"Use /start to configure more\n"
            f"Use /status to check status\n"
            f"Use /stop to stop automation",
            parse_mode='Markdown'
        )
        
        # Ask to start
        keyboard = [[InlineKeyboardButton("▶️ Start Automation", callback_data="start_inbox")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Start automation now?", reply_markup=reply_markup)
        
        return ConversationHandler.END
    
    async def receive_group_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive group ID"""
        group_id = update.message.text.strip()
        context.user_data['group_id'] = group_id
        
        await update.message.reply_text(
            "✅ Group ID received!\n\n"
            "Send **Delay** between messages (seconds)\n"
            "Example: 30"
        )
        return WAITING_GROUP_DELAY
    
    async def receive_group_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive group delay"""
        try:
            delay = int(update.message.text.strip())
            context.user_data['delay'] = delay
        except:
            await update.message.reply_text("Please send a valid number")
            return WAITING_GROUP_DELAY
        
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
        """Receive group messages and save config"""
        messages = update.message.text.strip()
        
        user_id = context.user_data['user_id']
        group_id = context.user_data['group_id']
        delay = context.user_data['delay']
        cookies = context.user_data['cookies']
        
        save_group_config(user_id, group_id, delay, messages, cookies)
        
        await update.message.reply_text(
            f"✅ **Group Automation Configured!** ✅\n\n"
            f"Group ID: `{group_id}`\n"
            f"Delay: `{delay}s`\n"
            f"Messages: `{len([m for m in messages.split('\\n') if m.strip()])}`\n\n"
            f"Use /start to configure more\n"
            f"Use /status to check status\n"
            f"Use /stop to stop automation",
            parse_mode='Markdown'
        )
        
        # Ask to start
        keyboard = [[InlineKeyboardButton("▶️ Start Automation", callback_data="start_group")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Start automation now?", reply_markup=reply_markup)
        
        return ConversationHandler.END
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show status"""
        user = update.effective_user
        user_id, _, approved = get_or_create_user(user.id, user.username)
        
        inbox_config = get_inbox_config(user_id)
        group_config = get_group_config(user_id)
        
        status_text = f"📊 **Your Status**\n\n"
        status_text += f"User: {user.first_name}\n"
        status_text += f"Approved: {'✅ Yes' if approved else '❌ No'}\n\n"
        
        if inbox_config:
            status_text += f"**📥 Inbox Automation:**\n"
            status_text += f"• Chat ID: `{inbox_config['chat_id'][:20]}...`\n"
            status_text += f"• Status: {'🟢 Running' if inbox_config['running'] else '🔴 Stopped'}\n"
            status_text += f"• Messages Sent: {inbox_config['message_count']}\n\n"
        
        if group_config:
            status_text += f"**👥 Group Automation:**\n"
            status_text += f"• Group ID: `{group_config['group_id'][:20]}...`\n"
            status_text += f"• Status: {'🟢 Running' if group_config['running'] else '🔴 Stopped'}\n"
            status_text += f"• Messages Sent: {group_config['message_count']}\n\n"
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop all automations"""
        user = update.effective_user
        user_id, _, _ = get_or_create_user(user.id, user.username)
        
        # Stop inbox
        inbox_config = get_inbox_config(user_id)
        if inbox_config and inbox_config['running']:
            update_inbox_running(user_id, 0)
        
        # Stop group
        group_config = get_group_config(user_id)
        if group_config and group_config['running']:
            update_group_running(user_id, 0)
        
        await update.message.reply_text(
            "🛑 **All automations stopped!**\n\n"
            "Use /start to configure and start again."
        )
    
    async def approve_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Approve user (admin only)"""
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
        """List all users (admin only)"""
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
        """Help command"""
        help_text = """
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

For support: """ + OWNER_FACEBOOK
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation"""
        await update.message.reply_text("❌ Cancelled. Use /start to begin again.")
        return ConversationHandler.END
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Error: {context.error}")
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again."
            )
    
    def run(self):
        """Run the bot"""
        init_db()
        logger.info("Starting Facebook Automation Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    # Add these imports at the top
from flask import Flask
import threading

# Create a simple Flask app for health check
health_app = Flask(__name__)

@health_app.route('/')
@health_app.route('/health')
def health_check():
    return "Bot is running!", 200

def run_health_server():
    """Run Flask server for Render health checks"""
    port = int(os.environ.get('PORT', 8080))
    health_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Initialize database
    init_db()
    
    # Start health check server in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Start Telegram bot
    logger.info("Starting Facebook Automation Bot...")
    bot = AutomationBot()
    bot.run()
