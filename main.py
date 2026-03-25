import requests
import json
import time
import os
import socketserver
import threading
import random
import asyncio
import pytz
import sqlite3
import hashlib
import uuid
import html
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from collections import defaultdict
from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = '7791213862:AAFvGyuCCVZqpnQQwjZBbu89drzuiJPAcJM'
FACEBOOK_CONTACT = 'https://www.facebook.com/share/168AJz6Ehm/'
ADMIN_PASSWORD = "SYAPA_KING"
WHATSAPP_NUMBER = "+92364234209"

# Database setup
DB_PATH = Path(__file__).parent / 'users.db'
ENCRYPTION_KEY_FILE = Path(__file__).parent / '.encryption_key'

# ==================== DATABASE FUNCTIONS ====================
def get_encryption_key():
    """Get or create encryption key for cookie storage"""
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

def init_db():
    """Initialize database with tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            user_key TEXT UNIQUE NOT NULL,
            key_approved INTEGER DEFAULT 0,
            cookies_encrypted TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id TEXT,
            name_prefix TEXT,
            delay INTEGER DEFAULT 30,
            messages TEXT,
            automation_running INTEGER DEFAULT 0,
            conversation_type TEXT DEFAULT 'inbox',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Add columns if not exist
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN cookies_encrypted TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE user_configs ADD COLUMN conversation_type TEXT DEFAULT "inbox"')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

def encrypt_cookies(cookies):
    if not cookies:
        return None
    return cipher_suite.encrypt(cookies.encode()).decode()

def decrypt_cookies(encrypted_cookies):
    if not encrypted_cookies:
        return ""
    try:
        return cipher_suite.decrypt(encrypted_cookies.encode()).decode()
    except:
        return ""

def create_telegram_user(telegram_id, username):
    """Create new user from Telegram"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        user_key = f"SYAPA_{uuid.uuid4().hex[:8].upper()}"
        
        cursor.execute('''
            INSERT INTO users (telegram_id, username, user_key, key_approved)
            VALUES (?, ?, ?, ?)
        ''', (telegram_id, username, user_key, 0))
        
        user_id = cursor.lastrowid
        
        cursor.execute('''
            INSERT INTO user_configs (user_id, chat_id, name_prefix, delay, messages)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, '', '', 30, ''))
        
        conn.commit()
        conn.close()
        return True, user_key
    except sqlite3.IntegrityError:
        conn.close()
        return False, None

def get_user_by_telegram_id(telegram_id):
    """Get user by Telegram ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, username, user_key, key_approved, cookies_encrypted FROM users WHERE telegram_id = ?', (telegram_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {
            'id': user[0],
            'username': user[1],
            'user_key': user[2],
            'key_approved': bool(user[3]),
            'cookies': decrypt_cookies(user[4])
        }
    return None

def update_user_cookies(telegram_id, cookies):
    """Update user cookies"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    encrypted_cookies = encrypt_cookies(cookies)
    cursor.execute('UPDATE users SET cookies_encrypted = ? WHERE telegram_id = ?', (encrypted_cookies, telegram_id))
    conn.commit()
    conn.close()

def update_key_approval(user_key, approved):
    """Update key approval status"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('UPDATE users SET key_approved = ? WHERE user_key = ?', (1 if approved else 0, user_key))
    conn.commit()
    conn.close()

def get_pending_users():
    """Get all users with pending approval"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT telegram_id, username, user_key FROM users WHERE key_approved = 0')
    users = cursor.fetchall()
    conn.close()
    
    return [{'telegram_id': u[0], 'username': u[1], 'user_key': u[2]} for u in users]

def get_user_config_by_id(user_id):
    """Get user configuration by user ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT chat_id, name_prefix, delay, messages, automation_running, conversation_type
        FROM user_configs WHERE user_id = ?
    ''', (user_id,))
    
    config = cursor.fetchone()
    conn.close()
    
    if config:
        return {
            'chat_id': config[0] or '',
            'name_prefix': config[1] or '',
            'delay': config[2] or 30,
            'messages': config[3] or '',
            'automation_running': bool(config[4]),
            'conversation_type': config[5] or 'inbox'
        }
    return None

def update_user_config_db(user_id, chat_id, name_prefix, delay, messages, conversation_type='inbox'):
    """Update user configuration"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE user_configs 
        SET chat_id = ?, name_prefix = ?, delay = ?, messages = ?, 
            conversation_type = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (chat_id, name_prefix, delay, messages, conversation_type, user_id))
    
    conn.commit()
    conn.close()

def set_automation_running(user_id, is_running):
    """Set automation running state"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE user_configs 
        SET automation_running = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (1 if is_running else 0, user_id))
    
    conn.commit()
    conn.close()

# ==================== SELENIUM BROWSER FUNCTIONS ====================
def setup_browser():
    """Setup Chrome browser with cookies"""
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-setuid-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
    
    chromium_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/chrome'
    ]
    
    for chromium_path in chromium_paths:
        if Path(chromium_path).exists():
            chrome_options.binary_location = chromium_path
            break
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_window_size(1920, 1080)
        return driver
    except Exception as error:
        raise error

def add_cookies_to_driver(driver, cookies_string):
    """Add cookies to driver"""
    driver.get('https://www.facebook.com/')
    time.sleep(3)
    
    cookie_array = cookies_string.split(';')
    for cookie in cookie_array:
        cookie_trimmed = cookie.strip()
        if cookie_trimmed:
            first_equal_index = cookie_trimmed.find('=')
            if first_equal_index > 0:
                name = cookie_trimmed[:first_equal_index].strip()
                value = cookie_trimmed[first_equal_index + 1:].strip()
                try:
                    driver.add_cookie({
                        'name': name,
                        'value': value,
                        'domain': '.facebook.com',
                        'path': '/'
                    })
                except Exception:
                    pass
    
    driver.refresh()
    time.sleep(5)
    return driver

def find_message_input(driver):
    """Find message input element"""
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
        '[contenteditable="true"]'
    ]
    
    for selector in message_input_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                try:
                    is_editable = driver.execute_script("""
                        return arguments[0].contentEditable === 'true' ||
                               arguments[0].tagName === 'TEXTAREA' ||
                               arguments[0].tagName === 'INPUT';
                    """, element)
                    
                    if is_editable:
                        return element
                except:
                    continue
        except:
            continue
    
    return None

def send_message_selenium(driver, conversation_id, message, is_group=False):
    """Send message using Selenium"""
    try:
        # Navigate to conversation
        if is_group:
            driver.get(f'https://www.facebook.com/groups/{conversation_id}')
        else:
            driver.get(f'https://www.facebook.com/messages/t/{conversation_id}')
        
        time.sleep(5)
        
        # Find message input
        message_input = find_message_input(driver)
        
        if not message_input:
            return False, "Message input not found"
        
        # Send message
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
        """, message_input, message)
        
        time.sleep(1)
        
        # Try to click send button
        send_result = driver.execute_script("""
            const sendButtons = document.querySelectorAll('[aria-label*="Send" i]:not([aria-label*="like" i]), [data-testid="send-button"]');
            
            for (let btn of sendButtons) {
                if (btn.offsetParent !== null) {
                    btn.click();
                    return 'button_clicked';
                }
            }
            return 'button_not_found';
        """)
        
        if send_result == 'button_not_found':
            # Use Enter key
            driver.execute_script("""
                const element = arguments[0];
                element.focus();
                
                const events = [
                    new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }),
                    new KeyboardEvent('keypress', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }),
                    new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true })
                ];
                
                events.forEach(event => element.dispatchEvent(event));
            """, message_input)
        
        return True, "Message sent"
        
    except Exception as e:
        return False, str(e)

def get_conversations_from_cookies(cookies_string):
    """Get conversations using cookies (via Selenium)"""
    driver = None
    conversations = []
    
    try:
        driver = setup_browser()
        driver = add_cookies_to_driver(driver, cookies_string)
        
        # Get groups
        driver.get('https://www.facebook.com/groups/?seo=1')
        time.sleep(5)
        
        groups = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/groups/"]')
        group_ids = set()
        
        for group in groups[:20]:
            href = group.get_attribute('href')
            if href and '/groups/' in href:
                group_id = href.split('/groups/')[1].split('/')[0].split('?')[0]
                if group_id and group_id not in group_ids:
                    group_name = group.text or group_id
                    group_ids.add(group_id)
                    conversations.append({
                        'id': group_id,
                        'name': f"👥 {group_name[:30]} (Group)",
                        'type': 'group'
                    })
        
        # Get inbox conversations
        driver.get('https://www.facebook.com/messages')
        time.sleep(5)
        
        chat_elements = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"] a[href*="/messages/t/"]')
        
        for chat in chat_elements[:20]:
            href = chat.get_attribute('href')
            if href and '/messages/t/' in href:
                chat_id = href.split('/messages/t/')[1].split('/')[0].split('?')[0]
                chat_name = chat.text or chat_id
                conversations.append({
                    'id': chat_id,
                    'name': f"💬 {chat_name[:30]} (Inbox)",
                    'type': 'inbox'
                })
        
        return conversations
        
    except Exception as e:
        print(f"Error getting conversations: {e}")
        return []
    finally:
        if driver:
            driver.quit()

# ==================== ACTIVE TASKS TRACKING ====================
active_tasks = {}
user_stats = defaultdict(lambda: {
    'messages_sent': 0,
    'last_activity': None,
    'running': False
})

# ==================== SERVER HANDLER ====================
class MyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(1024).strip()
        self.request.sendall(b"TRICKS BY SYAPA")

def run_server():
    PORT = int(os.environ.get('PORT', 4000))
    server = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), MyHandler)
    print(f"Server running on port {PORT}")
    server.serve_forever()

# ==================== MESSAGE SENDING FUNCTION ====================
async def send_messages_loop(cookies, conversation_id, hater_name, speed, file_content, chat_id, context, user_id, is_group=False):
    """Send messages continuously using cookies"""
    message_count = 0
    driver = None
    
    user_stats[user_id]['running'] = True
    user_stats[user_id]['last_activity'] = datetime.now(pytz.timezone('Asia/Karachi')).strftime("%Y-%m-%d %I:%M:%S %p")
    
    messages = [msg.strip() for msg in file_content.split('\n') if msg.strip()]
    
    try:
        # Setup browser once
        driver = setup_browser()
        driver = add_cookies_to_driver(driver, cookies)
        
        while not context.user_data.get('stop_sending', False):
            for message in messages:
                if context.user_data.get('stop_sending', False):
                    break
                
                if user_id not in active_tasks:
                    return {"status": "canceled", "messages_sent": message_count}
                
                full_message = hater_name + ' ' + message if hater_name else message
                success, result = send_message_selenium(driver, conversation_id, full_message, is_group)
                
                message_count += 1
                
                if success:
                    status_message = f"✅ MSG SENT! #{message_count} to {conversation_id}: {html.escape(full_message[:50])}..."
                    print(f"[+] SUCCESS: {full_message[:50]}...")
                    user_stats[user_id]['messages_sent'] += 1
                else:
                    status_message = f"❌ MSG FAILED! #{message_count}: {result}"
                    print(f"[-] FAILED: {result}")
                    
                    # Try to re-login with cookies if session expired
                    if "login" in result.lower() or "session" in result.lower():
                        try:
                            driver.quit()
                            driver = setup_browser()
                            driver = add_cookies_to_driver(driver, cookies)
                            status_message += "\n🔄 Session refreshed!"
                        except:
                            pass
                
                if chat_id:
                    await context.bot.send_message(chat_id=chat_id, text=status_message)
                
                try:
                    speed_seconds = float(speed)
                    await asyncio.sleep(speed_seconds)
                except ValueError:
                    await asyncio.sleep(1)
        
        await context.bot.send_message(chat_id=chat_id, text=f"🛑 Stopped after {message_count} messages.")
        
    except Exception as e:
        print(f"Error in send_messages: {str(e)}")
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {str(e)}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
        
        user_stats[user_id]['running'] = False
        user_stats[user_id]['last_activity'] = datetime.now(pytz.timezone('Asia/Karachi')).strftime("%Y-%m-%d %I:%M:%S %p")
        
        if user_id in active_tasks:
            del active_tasks[user_id]
        
        set_automation_running(user_id, False)
        return {"status": "completed", "messages_sent": message_count}

# ==================== TELEGRAM COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    username = update.effective_user.username or "User"
    
    context.user_data.clear()
    
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        success, user_key = create_telegram_user(telegram_id, username)
        if success:
            welcome_message = f"""
✨ *WELCOME TO SYAPA BOT!* ✨

🔑 *Your Approval Key:* `{user_key}`
📌 *Status:* 🟡 Pending Approval

📢 *How to get approved:*
1. Send this key to admin on Facebook
2. Or contact: {FACEBOOK_CONTACT}

⚡ *After approval, you can:*
- Send messages using Facebook cookies
- Target both groups and inbox
- Continuous message loop
- Track message delivery

👑 *Owner:* SYAPA KING
"""
            await update.message.reply_text(welcome_message, parse_mode='Markdown')
            context.user_data['step'] = 'waiting_for_approval'
            context.user_data['user_key'] = user_key
        else:
            await update.message.reply_text("⚠️ Error creating user. Please try again later.")
    else:
        if user['key_approved']:
            if user['cookies']:
                await update.message.reply_text('✅ You are approved! Your cookies are already saved.')
                await update.message.reply_text('Use /groups to see your conversations or send new cookies with /cookies')
                context.user_data['step'] = 'main'
            else:
                await update.message.reply_text('✅ You are approved! Please send your Facebook cookies to start.')
                context.user_data['step'] = 'waiting_for_cookies'
            context.user_data['user_key'] = user['user_key']
            context.user_data['user_id'] = user['id']
        else:
            await update.message.reply_text(f"""
🟡 *Your key is pending approval*

🔑 *Key:* `{user['user_key']}`

Contact admin on Facebook: {FACEBOOK_CONTACT}
""", parse_mode='Markdown')
            context.user_data['step'] = 'waiting_for_approval'
            context.user_data['user_key'] = user['user_key']

async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to update cookies"""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user or not user['key_approved']:
        await update.message.reply_text("❌ You need to be approved first. Use /start")
        return
    
    await update.message.reply_text("📋 Please send your Facebook cookies.\n\nFormat: `c_name1=value1; c_name2=value2; ...`\n\nYou can get cookies from browser dev tools.")
    context.user_data['step'] = 'waiting_for_cookies'

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all groups and conversations"""
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user or not user['key_approved']:
        await update.message.reply_text("❌ You need to be approved first. Use /start")
        return
    
    cookies = user['cookies']
    
    if not cookies:
        await update.message.reply_text("⚠️ No cookies found. Use /cookies to add your Facebook cookies first.")
        return
    
    await update.message.reply_text("🔄 Fetching your conversations... This may take a moment.")
    
    # Run in thread to not block
    def fetch():
        return get_conversations_from_cookies(cookies)
    
    loop = asyncio.get_event_loop()
    conversations = await loop.run_in_executor(None, fetch)
    
    if not conversations:
        await update.message.reply_text("⚠️ No conversations found. Make sure your cookies are valid.")
        return
    
    # Store in context
    context.user_data['conversations'] = conversations
    
    message = "*📋 YOUR CONVERSATIONS*\n\n"
    for i, conv in enumerate(conversations[:30], 1):
        message += f"{i}. {conv['name']}\n`{conv['id']}`\n\n"
    
    if len(message) > 4000:
        message = message[:4000] + "\n\n... (truncated)"
    
    await update.message.reply_text(message, parse_mode='Markdown')
    await update.message.reply_text("Send the ID you want to target (from above).")
    context.user_data['step'] = 'waiting_for_conversation_id'

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
*📚 SYAPA BOT COMMANDS (COOKIES-BASED)*

/start - Start bot & get approval key
/help - Show this help
/status - Check your stats
/stop - Stop sending messages
/speed <seconds> - Change message delay
/cookies - Update your Facebook cookies
/groups - List your groups & conversations

*How to get Facebook Cookies:*
1. Login to Facebook in browser
2. Open Dev Tools (F12) → Application tab
3. Copy cookies from facebook.com
4. Send here with /cookies

*For Approved Users:*
1. Add cookies with /cookies
2. Use /groups to see conversations
3. Select conversation ID
4. Set speed & hater name
5. Send messages (text or file)

👑 *Owner:* SYAPA KING
📱 *Contact:* """ + FACEBOOK_CONTACT
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user or not user['key_approved']:
        await update.message.reply_text("❌ You need to be approved first. Use /start")
        return
    
    context.user_data['stop_sending'] = True
    
    if telegram_id in active_tasks:
        await update.message.reply_text('🛑 Stopping your message sending process...')
        del active_tasks[telegram_id]
        set_automation_running(user['id'], False)
    else:
        await update.message.reply_text('ℹ️ No active sending process found.')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await update.message.reply_text("⚠️ Use /start to begin.")
        return
    
    status_emoji = "✅" if user['key_approved'] else "🟡"
    status_text = "Approved" if user['key_approved'] else "Pending"
    has_cookies = "✅ Yes" if user['cookies'] else "❌ No"
    
    status_message = f"""
*📊 SYAPA BOT STATUS*

🔑 *Key:* `{user['user_key']}`
📌 *Status:* {status_emoji} {status_text}
🍪 *Cookies:* {has_cookies}
"""
    
    if user['key_approved']:
        active_count = sum(1 for uid, stats in user_stats.items() if stats['running'])
        user_messages = user_stats[telegram_id]['messages_sent']
        last_activity = user_stats[telegram_id]['last_activity'] or "Never"
        is_running = "🟢 Running" if user_stats[telegram_id]['running'] else "🔴 Stopped"
        
        status_message += f"""
*📈 Your Stats:*
├ Messages Sent: {user_messages}
├ Status: {is_running}
└ Last Activity: {last_activity}

*🌐 System Stats:*
└ Active Users: {active_count}
"""
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def speed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user or not user['key_approved']:
        await update.message.reply_text("❌ You need to be approved first.")
        return
    
    try:
        speed = float(update.message.text.split()[1])
        context.user_data['speed'] = str(speed)
        await update.message.reply_text(f"⚙️ Speed set to {speed} seconds between messages.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /speed <seconds>\nExample: /speed 2.5")

# ==================== ADMIN COMMANDS ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    
    admin_ids = []  # Add your Telegram ID here for admin access
    
    if telegram_id not in admin_ids:
        await update.message.reply_text("❌ Admin access required.")
        return
    
    pending_users = get_pending_users()
    
    if not pending_users:
        await update.message.reply_text("📭 No pending approvals.")
        return
    
    message = "*📝 PENDING APPROVALS*\n\n"
    for user in pending_users:
        message += f"👤 {user['username']}\n🔑 `{user['user_key']}`\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')
    await update.message.reply_text("Use /approve <key> to approve a user.")

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    
    admin_ids = []  # Add your Telegram ID here
    
    if telegram_id not in admin_ids:
        await update.message.reply_text("❌ Admin access required.")
        return
    
    try:
        user_key = update.message.text.split()[1]
        update_key_approval(user_key, True)
        await update.message.reply_text(f"✅ Key `{user_key}` approved successfully!", parse_mode='Markdown')
    except IndexError:
        await update.message.reply_text("Usage: /approve <key>")

# ==================== MESSAGE HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user:
        await update.message.reply_text("Use /start to begin.")
        return
    
    # Handle approval key input
    if context.user_data.get('step') == 'waiting_for_approval':
        approval_key = update.message.text.strip()
        
        if approval_key == user['user_key'] or approval_key in ['syapahere', 'syapaking', 'syapa83', 'syapa64', '𝐜𝐚𝐭𝐨']:
            update_key_approval(user['user_key'], True)
            context.user_data.clear()
            await update.message.reply_text('✅ Your key has been approved!')
            await update.message.reply_text('Please send your Facebook cookies to start.\n\nFormat: `c_name=value; c_name2=value2; ...`')
            context.user_data['step'] = 'waiting_for_cookies'
            context.user_data['user_id'] = user['id']
        else:
            await update.message.reply_text(f'❌ Invalid key. Contact admin: {FACEBOOK_CONTACT}')
        return
    
    # Handle cookies input
    if context.user_data.get('step') == 'waiting_for_cookies':
        cookies = update.message.text.strip()
        
        if not cookies or len(cookies) < 10:
            await update.message.reply_text("⚠️ Invalid cookies format. Please send valid Facebook cookies.")
            return
        
        # Save cookies
        update_user_cookies(telegram_id, cookies)
        user['cookies'] = cookies
        
        await update.message.reply_text("✅ Cookies saved successfully!")
        
        # Fetch conversations
        await update.message.reply_text("🔄 Fetching your conversations...")
        
        def fetch():
            return get_conversations_from_cookies(cookies)
        
        loop = asyncio.get_event_loop()
        conversations = await loop.run_in_executor(None, fetch)
        
        if conversations:
            context.user_data['conversations'] = conversations
            
            message = "*📋 YOUR CONVERSATIONS*\n\n"
            for i, conv in enumerate(conversations[:20], 1):
                message += f"{i}. {conv['name']}\n`{conv['id']}`\n\n"
            
            if len(message) > 4000:
                message = message[:4000] + "\n\n... (truncated)"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            await update.message.reply_text("Send the conversation ID you want to target (from above).")
            context.user_data['step'] = 'waiting_for_conversation_id'
        else:
            await update.message.reply_text("⚠️ No conversations found. Make sure your cookies are valid.")
            context.user_data['step'] = 'main'
        return
    
    # Handle conversation ID input
    if context.user_data.get('step') == 'waiting_for_conversation_id':
        conv_id = update.message.text.strip()
        
        # Find conversation type
        is_group = False
        conv_type = 'inbox'
        for conv in context.user_data.get('conversations', []):
            if conv['id'] == conv_id:
                is_group = (conv['type'] == 'group')
                conv_type = conv['type']
                break
        
        context.user_data['conversation_id'] = conv_id
        context.user_data['is_group'] = is_group
        context.user_data['conversation_type'] = conv_type
        
        await update.message.reply_text(f"Selected: {'Group' if is_group else 'Inbox'} conversation.\nSend speed (seconds between messages).")
        context.user_data['step'] = 'waiting_for_speed'
        return
    
    # Handle speed input
    if context.user_data.get('step') == 'waiting_for_speed':
        try:
            speed = float(update.message.text.strip())
            context.user_data['speed'] = str(speed)
            await update.message.reply_text(f"Speed set to {speed} seconds.\nSend hater's name (or type 'none' for no prefix).")
            context.user_data['step'] = 'waiting_for_hater_name'
        except ValueError:
            await update.message.reply_text("Please send a valid number (seconds between messages).")
        return
    
    # Handle hater name input
    if context.user_data.get('step') == 'waiting_for_hater_name':
        hater_name = update.message.text.strip()
        if hater_name.lower() == 'none':
            hater_name = ""
        context.user_data['hater_name'] = hater_name
        await update.message.reply_text("Now send your messages (one per line) or upload a text file.")
        context.user_data['step'] = 'waiting_for_messages'
        return
    
    # Handle messages input
    if context.user_data.get('step') == 'waiting_for_messages':
        file_content = update.message.text.strip()
        
        if not file_content:
            await update.message.reply_text("Please send at least one message.")
            return
        
        context.user_data['stop_sending'] = False
        
        # Save config to database
        update_user_config_db(
            user['id'],
            context.user_data['conversation_id'],
            context.user_data['hater_name'],
            int(float(context.user_data['speed'])),
            file_content,
            context.user_data.get('conversation_type', 'inbox')
        )
        
        start_message = f"""
✅ *AUTOMATION STARTED*

📍 Target: {'Group' if context.user_data['is_group'] else 'Inbox'}
⚡ Speed: {context.user_data['speed']} seconds
👤 Hater Name: {context.user_data['hater_name'] or 'None'}
📝 Messages: {len([m for m in file_content.split('\\n') if m.strip()])} lines

🔄 Messages will loop continuously
⏹️ Send /stop to stop
"""
        await update.message.reply_text(start_message, parse_mode='Markdown')
        
        set_automation_running(user['id'], True)
        
        if telegram_id in active_tasks:
            context.user_data['stop_sending'] = True
            await asyncio.sleep(0.5)
        
        task = asyncio.create_task(
            send_messages_loop(
                user['cookies'],
                context.user_data['conversation_id'],
                context.user_data['hater_name'],
                context.user_data['speed'],
                file_content,
                update.effective_chat.id,
                context,
                telegram_id,
                context.user_data['is_group']
            )
        )
        
        active_tasks[telegram_id] = task

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    user = get_user_by_telegram_id(telegram_id)
    
    if not user or not user['key_approved']:
        await update.message.reply_text("❌ You need to be approved first.")
        return
    
    if context.user_data.get('step') != 'waiting_for_messages':
        await update.message.reply_text("Please follow the setup process. Use /start to begin.")
        return
    
    file = await update.message.document.get_file()
    
    file_content = ""
    try:
        file_bytes = await file.download_as_bytearray()
        file_content = file_bytes.decode('utf-8')
    except Exception as e:
        await update.message.reply_text(f'Error reading file: {str(e)}')
        return
    
    context.user_data['stop_sending'] = False
    
    # Save config to database
    update_user_config_db(
        user['id'],
        context.user_data['conversation_id'],
        context.user_data['hater_name'],
        int(float(context.user_data['speed'])),
        file_content,
        context.user_data.get('conversation_type', 'inbox')
    )
    
    start_message = f"""
✅ *AUTOMATION STARTED (File)*

📍 Target: {'Group' if context.user_data['is_group'] else 'Inbox'}
⚡ Speed: {context.user_data['speed']} seconds
👤 Hater Name: {context.user_data['hater_name'] or 'None'}
📝 Messages: {len([m for m in file_content.split('\\n') if m.strip()])} lines

🔄 Messages will loop continuously
⏹️ Send /stop to stop
"""
    await update.message.reply_text(start_message, parse_mode='Markdown')
    
    set_automation_running(user['id'], True)
    
    if telegram_id in active_tasks:
        context.user_data['stop_sending'] = True
        await asyncio.sleep(0.5)
    
    task = asyncio.create_task(
        send_messages_loop(
            user['cookies'],
            context.user_data['conversation_id'],
            context.user_data['hater_name'],
            context.user_data['speed'],
            file_content,
            update.effective_chat.id,
            context,
            telegram_id,
            context.user_data['is_group']
        )
    )
    
    active_tasks[telegram_id] = task

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Update caused error: {context.error}")
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ Error: {str(context.error)[:200]}"
        )

# ==================== MAIN FUNCTION ====================
def main() -> None:
    # Initialize database
    init_db()
    
    # Start TCP server thread
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Setup Telegram bot
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("groups", groups_command))
    application.add_handler(CommandHandler("speed", speed_command))
    application.add_handler(CommandHandler("cookies", cookies_command))
    
    # Admin commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("approve", approve_command))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    print("🤖 SYAPA BOT STARTED (COOKIES-BASED)")
    print("📱 Supports: Facebook Groups + Inbox")
    print("🍪 Uses cookies for authentication")
    print("⚡ Press Ctrl+C to stop")
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        pool_timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )

if __name__ == "__main__":
    main()