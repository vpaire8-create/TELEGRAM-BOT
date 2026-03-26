import requests
import json
import time
import os
import socketserver
import threading
import asyncio
import pytz
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from collections import defaultdict
import uuid
import html
import hashlib
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import sys

# Telegram bot token
TELEGRAM_BOT_TOKEN = '7791213862:AAFvGyuCCVZqpnQQwjZBbu89drzuiJPAcJM'

# Dictionary to track active tasks for each user
active_tasks = {}

# List of approved keys
APPROVED_KEYS = ['syapahere', 'syapaking', 'syapa83', 'syapa64', '𝐜𝐚𝐭𝐨']

# Your Facebook contact
FACEBOOK_CONTACT = 'https://www.facebook.com/share/168AJz6Ehm/'

# Dictionary to track user approval status and automation state
user_approval_status = {}
user_automation_state = defaultdict(lambda: {
    'running': False,
    'message_count': 0,
    'logs': [],
    'current_thread': None
})

# Dictionary to track user statistics
user_stats = defaultdict(lambda: {
    'messages_sent': 0,
    'last_activity': None,
    'running': False,
    'type': None  # 'group' or 'inbox'
})

class MyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b"Bot is running!")

def run_server():
    PORT = int(os.environ.get('PORT', 4000))
    try:
        server = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), MyHandler)
        print(f"Health check server running on port {PORT}")
        server.serve_forever()
    except Exception as e:
        print(f"Server error: {e}")

def validate_token(token):
    try:
        response = requests.get(f"https://graph.facebook.com/v20.0/me?access_token={token}", timeout=10)
        data = response.json()
        return 'id' in data
    except Exception as e:
        print(f"Token validation error: {str(e)}")
        return False

def fetch_groups(token):
    try:
        response = requests.get(
            f"https://graph.facebook.com/v20.0/me/conversations?fields=name,id&access_token={token}&limit=100",
            timeout=10
        )
        data = response.json()
        
        if 'error' in data:
            print(f"Facebook API error: {data['error']['message']}")
            return []
        
        conversations = data.get('data', [])
        return conversations
    except Exception as e:
        print(f"Error fetching groups: {str(e)}")
        return []

def setup_browser(automation_state=None):
    log_message('Setting up Chrome browser...', automation_state)
    
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
            log_message(f'Found Chromium at: {chromium_path}', automation_state)
            break
    
    chromedriver_paths = [
        '/usr/bin/chromedriver',
        '/usr/local/bin/chromedriver'
    ]
    
    driver_path = None
    for driver_candidate in chromedriver_paths:
        if Path(driver_candidate).exists():
            driver_path = driver_candidate
            log_message(f'Found ChromeDriver at: {driver_path}', automation_state)
            break
    
    try:
        if driver_path:
            service = Service(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            log_message('Chrome started with detected ChromeDriver!', automation_state)
        else:
            driver = webdriver.Chrome(options=chrome_options)
            log_message('Chrome started with default driver!', automation_state)
        
        driver.set_window_size(1920, 1080)
        log_message('Chrome browser setup completed successfully!', automation_state)
        return driver
    except Exception as error:
        log_message(f'Browser setup failed: {error}', automation_state)
        raise error

def log_message(msg, automation_state=None):
    timestamp = time.strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    sys.stdout.flush()
    
    if automation_state:
        automation_state['logs'].append(formatted_msg)

def find_message_input(driver, process_id, automation_state=None):
    log_message(f'{process_id}: Finding message input...', automation_state)
    time.sleep(10)
    
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
    except Exception:
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
                        try:
                            element.click()
                            time.sleep(0.5)
                        except:
                            pass
                        return element
                except Exception:
                    continue
        except Exception:
            continue
    
    return None

def send_group_messages_selenium(config, automation_state, user_id, process_id='AUTO'):
    driver = None
    try:
        log_message(f'{process_id}: Starting group automation...', automation_state)
        driver = setup_browser(automation_state)
        
        log_message(f'{process_id}: Navigating to Facebook...', automation_state)
        driver.get('https://www.facebook.com/')
        time.sleep(8)
        
        # Add cookies if provided
        if config.get('cookies') and config['cookies'].strip():
            log_message(f'{process_id}: Adding cookies...', automation_state)
            cookie_array = config['cookies'].split(';')
            for cookie in cookie_array:
                cookie_trimmed = cookie.strip()
                if cookie_trimmed:
                    first_equal_index = cookie_trimmed.find('=')
                    if first_equal_index > 0:
                        name = cookie_trimmed[:first_equal_index].strip()
                        value = cookie_trimmed[first_equal_index + 1:].strip()
                        try:
                            driver.add_cookie({'name': name, 'value': value, 'domain': '.facebook.com', 'path': '/'})
                        except Exception:
                            pass
        
        # Open conversation
        if config.get('tid'):
            chat_id = config['tid'].strip()
            log_message(f'{process_id}: Opening conversation {chat_id}...', automation_state)
            driver.get(f'https://www.facebook.com/messages/t/{chat_id}')
        else:
            log_message(f'{process_id}: Opening messages...', automation_state)
            driver.get('https://www.facebook.com/messages')
        
        time.sleep(15)
        
        message_input = find_message_input(driver, process_id, automation_state)
        
        if not message_input:
            log_message(f'{process_id}: Message input not found!', automation_state)
            automation_state['running'] = False
            return 0
        
        delay = int(config.get('speed', 30))
        messages_sent = 0
        messages_list = [msg.strip() for msg in config['messages'].split('\n') if msg.strip()]
        
        if not messages_list:
            messages_list = ['Hello!']
        
        message_rotation_index = 0
        
        while automation_state['running']:
            message = messages_list[message_rotation_index % len(messages_list)]
            message_rotation_index += 1
            
            if config.get('hater_name'):
                message_to_send = f"{config['hater_name']} {message}"
            else:
                message_to_send = message
            
            try:
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
                """, message_input, message_to_send)
                
                time.sleep(1)
                
                sent = driver.execute_script("""
                    const sendButtons = document.querySelectorAll('[aria-label*="Send" i]:not([aria-label*="like" i]), [data-testid="send-button"]');
                    for (let btn of sendButtons) {
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return 'button_clicked';
                        }
                    }
                    return 'button_not_found';
                """)
                
                if sent == 'button_not_found':
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
                
                messages_sent += 1
                automation_state['message_count'] = messages_sent
                user_stats[user_id]['messages_sent'] += 1
                
                log_message(f'{process_id}: Message #{messages_sent} sent. Waiting {delay}s...', automation_state)
                time.sleep(delay)
                
            except Exception as e:
                log_message(f'{process_id}: Send error: {str(e)[:100]}', automation_state)
                time.sleep(5)
        
        log_message(f'{process_id}: Automation stopped. Total messages: {messages_sent}', automation_state)
        return messages_sent
        
    except Exception as e:
        log_message(f'{process_id}: Fatal error: {str(e)}', automation_state)
        automation_state['running'] = False
        return 0
    finally:
        if driver:
            try:
                driver.quit()
                log_message(f'{process_id}: Browser closed', automation_state)
            except:
                pass

async def send_messages_from_file(token, tid, hater_name, speed, file_content, chat_id, context, user_id, automation_type='group'):
    message_count = 0
    headers = {"Content-Type": "application/json"}

    user_stats[user_id]['running'] = True
    user_stats[user_id]['type'] = automation_type
    user_stats[user_id]['last_activity'] = datetime.now(pytz.timezone('Asia/Karachi')).strftime("%Y-%m-%d %I:%M:%S %p")
    
    messages = [msg.strip() for msg in file_content.split('\n') if msg.strip()]

    try:
        if automation_type == 'group':
            # Use Selenium for group messages
            config = {
                'cookies': '',
                'tid': tid,
                'hater_name': hater_name,
                'speed': speed,
                'messages': file_content
            }
            
            automation_state = user_automation_state[user_id]
            automation_state['running'] = True
            automation_state['message_count'] = 0
            automation_state['logs'] = []
            
            # Run Selenium automation
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                send_group_messages_selenium,
                config,
                automation_state,
                user_id,
                f'AUTO-{user_id}'
            )
            
            message_count = automation_state['message_count']
        else:
            # Use Graph API for inbox messages
            while not context.user_data.get('stop_sending', False):
                for message in messages:
                    if context.user_data.get('stop_sending', False):
                        break
                    
                    if user_id not in active_tasks:
                        return {"status": "canceled", "messages_sent": message_count}
                    
                    url = f"https://graph.facebook.com/v20.0/{tid}/"
                    full_message = hater_name + ' ' + message
                    parameters = {'access_token': token, 'message': full_message}
                    
                    try:
                        response = requests.post(url, json=parameters, headers=headers, timeout=10)
                        message_count += 1
                        
                        if response.status_code == 200:
                            status_message = f"✅ Message sent! #{message_count} to {tid}: {html.escape(full_message)}"
                            user_stats[user_id]['messages_sent'] += 1
                        else:
                            status_message = f"❌ Message failed! #{message_count} to {tid}: {html.escape(full_message)}"
                        
                        if chat_id:
                            await context.bot.send_message(chat_id=chat_id, text=status_message)
                    except Exception as e:
                        print(f"Error sending message: {str(e)}")
                        if chat_id:
                            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {str(e)}")
                    
                    try:
                        speed_seconds = float(speed)
                        await asyncio.sleep(speed_seconds)
                    except ValueError:
                        await asyncio.sleep(1)
            
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=f"🛑 Stopped after {message_count} messages.")
    except Exception as e:
        print(f"Error in send_messages: {str(e)}")
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Error: {str(e)}")
    finally:
        user_stats[user_id]['running'] = False
        user_stats[user_id]['last_activity'] = datetime.now(pytz.timezone('Asia/Karachi')).strftime("%Y-%m-%d %I:%M:%S %p")
        
        if user_id in active_tasks:
            del active_tasks[user_id]
        return {"status": "completed", "messages_sent": message_count}

async def generate_unique_key(user_id):
    existing_key = await get_user_key(user_id)
    if existing_key:
        return existing_key
    
    unique_key = f"syapa_{uuid.uuid4().hex[:8]}"
    
    all_keys = await get_all_keys()
    while unique_key in all_keys:
        unique_key = f"syapa_{uuid.uuid4().hex[:8]}"
    
    with open('users.txt', 'a') as f:
        f.write(f"{user_id}:{unique_key}\n")
    
    return unique_key

async def get_user_key(user_id):
    try:
        with open('users.txt', 'r') as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        uid, key = line.split(':')
                        if str(user_id) == uid:
                            return key.strip()
                    except ValueError:
                        continue
    except FileNotFoundError:
        with open('users.txt', 'w') as f:
            f.write("# User ID : Key mapping\n# Format: user_id:key\n")
    return None

async def get_all_keys():
    keys = set()
    try:
        with open('users.txt', 'r') as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        _, key = line.split(':')
                        keys.add(key.strip())
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    return keys

async def is_key_approved(key):
    if key in APPROVED_KEYS:
        return True
    
    try:
        with open('approved.txt', 'r') as f:
            lines = f.readlines()
            approved_keys = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
            return key in approved_keys
    except FileNotFoundError:
        with open('approved.txt', 'w') as f:
            f.write("# Approved keys\n# One key per line\n")
    
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    username = update.effective_user.username or "User"
    
    context.user_data.clear()
    
    user_key = await generate_unique_key(user_id)
    
    if not await get_user_key(user_id):
        with open('users.txt', 'a') as f:
            f.write(f"{user_id}:{user_key}\n")
        welcome_message = f"""
*WELCOME TO THE SYAPA BOT!* ✨

*Your Approval Key:* `{user_key}`
*Status:* 🟡 Pending

*To get approved:*
1. Contact @SYAPAKING on Facebook
2. Send your approval key
3. Wait for approval confirmation

*Facebook Contact:* {FACEBOOK_CONTACT}

*Owner:* 👿*SYAPA KING*👿
"""
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
        context.user_data['step'] = 'waiting_for_approval'
        return
    
    if await is_key_approved(user_key):
        await update.message.reply_text('✅ You are approved! Please choose automation type:\n\n/inbox - For inbox/message automation\n/group - For group chat automation')
        context.user_data['step'] = 'choose_type'
    else:
        vip_message = f"""
*❌ ACCESS DENIED!* ❌

*Your VIP Key:* `{user_key}`
*Status:* ❌ Not Approved

*Please contact the owner for approval:*
📲 {FACEBOOK_CONTACT}

*Owner:* 👿*SYAPA KING*👿
"""
        await update.message.reply_text(vip_message, parse_mode='Markdown')
        context.user_data['step'] = 'waiting_for_approval'

async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    user_key = await get_user_key(user_id)
    if not user_key or not await is_key_approved(user_key):
        await update.message.reply_text("You need to be approved first. Use /start to begin.")
        return
    
    context.user_data['automation_type'] = 'inbox'
    await update.message.reply_text('Please send your Facebook token for inbox automation:')
    context.user_data['step'] = 'waiting_for_token'

async def group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    user_key = await get_user_key(user_id)
    if not user_key or not await is_key_approved(user_key):
        await update.message.reply_text("You need to be approved first. Use /start to begin.")
        return
    
    context.user_data['automation_type'] = 'group'
    await update.message.reply_text('Please send your Facebook cookies for group automation (optional - you can skip with "skip"):')
    context.user_data['step'] = 'waiting_for_cookies'

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
*Available Commands:*

/start - Start the bot and get your approval key
/help - Show this help message
/status - Check your approval status and stats
/stop - Stop the automation process
/inbox - Start inbox automation (uses Facebook token)
/group - Start group automation (uses Selenium)

*For Approved Users:*

*Inbox Automation:*
- Send Facebook token to get your conversations
- Select conversation TID
- Configure speed and messages
- Uses Graph API for faster messaging

*Group Automation:*
- Send Facebook cookies (optional)
- Enter group/conversation ID
- Configure speed and messages
- Uses Selenium for reliable delivery

*Support:* {FACEBOOK_CONTACT}

*Owner:* 👿*SYAPA KING*👿
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    user_key = await get_user_key(user_id)
    if not user_key or not await is_key_approved(user_key):
        await update.message.reply_text("You need to be approved to use this service.")
        return
    
    context.user_data['stop_sending'] = True
    
    # Stop Selenium automation if running
    if user_id in user_automation_state:
        user_automation_state[user_id]['running'] = False
    
    if user_id in active_tasks:
        await update.message.reply_text('🛑 Stopping your automation process...')
        del active_tasks[user_id]
    else:
        await update.message.reply_text('ℹ️ You don\'t have any active automation process.')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_key = await get_user_key(user_id)
    
    if not user_key:
        await update.message.reply_text("⚠️ You haven't started the bot yet. Use /start to begin.")
        return
    
    is_approved = await is_key_approved(user_key)
    status_emoji = "✅" if is_approved else "🟡"
    status_text = "Approved" if is_approved else "Pending"
    
    status_message = f"""
*Bot Status Report* 📊

*Your Status:*
Key: `{user_key}`
Status: {status_emoji} {status_text}
"""
    
    if is_approved:
        active_users = sum(1 for uid, stats in user_stats.items() if stats['running'])
        user_messages = user_stats[user_id]['messages_sent']
        last_activity = user_stats[user_id]['last_activity'] or "Never"
        automation_type = user_stats[user_id]['type'] or "Not running"
        
        status_message += f"""
*Your Stats:*
Messages Sent: {user_messages}
Last Activity: {last_activity}
Automation Type: {automation_type}

*System Stats:*
Active Users: {active_users}
"""
    
    status_message += f"""
*Support:*
Facebook: {FACEBOOK_CONTACT}
Owner: *👿SYAPA KING👿*
"""
    
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if 'step' in context.user_data and context.user_data['step'] == 'waiting_for_approval':
        approval_key = update.message.text.strip()
        
        if await is_key_approved(approval_key):
            await update.message.reply_text('✅ Your key has been approved! You can now use the bot.')
            await update.message.reply_text('Please choose automation type:\n\n/inbox - For inbox/message automation\n/group - For group chat automation')
            context.user_data['step'] = 'choose_type'
        else:
            await update.message.reply_text('❌ Invalid approval key. Please contact the admin on Facebook:')
            await update.message.reply_text(f'📱 {FACEBOOK_CONTACT}')
        return
    
    user_key = await get_user_key(user_id)
    if not user_key or not await is_key_approved(user_key):
        await update.message.reply_text("You need to be approved to use this service. Use /start to begin the approval process.")
        return
    
    if 'step' not in context.user_data:
        context.user_data['step'] = 'choose_type'
        await update.message.reply_text('Please choose automation type:\n\n/inbox - For inbox/message automation\n/group - For group chat automation')
        return
    
    # Inbox automation flow
    if context.user_data.get('automation_type') == 'inbox':
        if context.user_data['step'] == 'waiting_for_token':
            token = update.message.text.strip()
            if not validate_token(token):
                await update.message.reply_text("⚠️ Invalid token. Please check and try again.")
                return
            
            groups = fetch_groups(token)
            context.user_data['token'] = token
            
            if groups:
                group_list = "*Available Groups/Conversations:*\n\n"
                for i, group in enumerate(groups[:10], 1):  # Show first 10 only
                    group_name = html.escape(group.get('name', 'Unnamed Conversation'))
                    group_id = group.get('id', 'N/A')
                    group_list += f"{i}. {group_name}\nID: `{group_id}`\n\n"
                
                if len(groups) > 10:
                    group_list += f"\n*... and {len(groups) - 10} more conversations*"
                
                await update.message.reply_text(group_list, parse_mode='Markdown')
                await update.message.reply_text('Please send the TID you want to use from the list above.')
                context.user_data['step'] = 'waiting_for_tid'
            else:
                await update.message.reply_text("No conversations found. Please make sure you have the correct token.")
            
        elif context.user_data['step'] == 'waiting_for_tid':
            context.user_data['tid'] = update.message.text.strip()
            await update.message.reply_text('TID received. Now please send the speed (in seconds between messages, default 30):')
            context.user_data['step'] = 'waiting_for_speed'
            
        elif context.user_data['step'] == 'waiting_for_speed':
            context.user_data['speed'] = update.message.text.strip()
            await update.message.reply_text('Speed received. Now please send the hater\'s name (prefix):')
            context.user_data['step'] = 'waiting_for_hater_name'
            
        elif context.user_data['step'] == 'waiting_for_hater_name':
            context.user_data['hater_name'] = update.message.text.strip()
            await update.message.reply_text('Hater name received. Now please send your messages (one per line):')
            context.user_data['step'] = 'waiting_for_messages'
            
        elif context.user_data['step'] == 'waiting_for_messages':
            context.user_data['messages'] = update.message.text
            
            start_message = f"""
*Inbox Automation Started* ✅
*TID:* {context.user_data['tid']}
*Speed:* {context.user_data['speed']} seconds
*Hater:* {context.user_data['hater_name']}

This will continue until you send /stop command.
"""
            
            await update.message.reply_text(start_message, parse_mode='Markdown')
            
            context.user_data['stop_sending'] = False
            
            if user_id in active_tasks:
                context.user_data['stop_sending'] = True
                await asyncio.sleep(0.5)
            
            chat_id = update.effective_chat.id
            sms_task = asyncio.create_task(
                send_messages_from_file(
                    context.user_data['token'],
                    context.user_data['tid'],
                    context.user_data['hater_name'],
                    context.user_data['speed'],
                    context.user_data['messages'],
                    chat_id,
                    context,
                    user_id,
                    'inbox'
                )
            )
            
            active_tasks[user_id] = sms_task
    
    # Group automation flow
    elif context.user_data.get('automation_type') == 'group':
        if context.user_data['step'] == 'waiting_for_cookies':
            cookies = update.message.text.strip()
            if cookies.lower() == 'skip':
                cookies = ''
            context.user_data['cookies'] = cookies
            await update.message.reply_text('Please send the group/conversation ID or TID:')
            context.user_data['step'] = 'waiting_for_group_tid'
            
        elif context.user_data['step'] == 'waiting_for_group_tid':
            context.user_data['tid'] = update.message.text.strip()
            await update.message.reply_text('Please send the speed (in seconds between messages, default 30):')
            context.user_data['step'] = 'waiting_for_group_speed'
            
        elif context.user_data['step'] == 'waiting_for_group_speed':
            context.user_data['speed'] = update.message.text.strip()
            await update.message.reply_text('Please send the hater\'s name (prefix):')
            context.user_data['step'] = 'waiting_for_group_hater'
            
        elif context.user_data['step'] == 'waiting_for_group_hater':
            context.user_data['hater_name'] = update.message.text.strip()
            await update.message.reply_text('Please send your messages (one per line):')
            context.user_data['step'] = 'waiting_for_group_messages'
            
        elif context.user_data['step'] == 'waiting_for_group_messages':
            context.user_data['messages'] = update.message.text
            
            start_message = f"""
*Group Automation Started* ✅
*TID:* {context.user_data['tid']}
*Speed:* {context.user_data['speed']} seconds
*Hater:* {context.user_data['hater_name']}

This will continue until you send /stop command.
*Note:* Group automation uses Selenium and may be slower than inbox automation.
"""
            
            await update.message.reply_text(start_message, parse_mode='Markdown')
            
            if user_id in active_tasks:
                context.user_data['stop_sending'] = True
                await asyncio.sleep(0.5)
            
            config = {
                'cookies': context.user_data.get('cookies', ''),
                'tid': context.user_data['tid'],
                'hater_name': context.user_data['hater_name'],
                'speed': context.user_data['speed'],
                'messages': context.user_data['messages']
            }
            
            chat_id = update.effective_chat.id
            sms_task = asyncio.create_task(
                send_messages_from_file(
                    config.get('token', ''),
                    config['tid'],
                    config['hater_name'],
                    config['speed'],
                    config['messages'],
                    chat_id,
                    context,
                    user_id,
                    'group'
                )
            )
            
            active_tasks[user_id] = sms_task

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Update caused error: {context.error}")
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ An error occurred: {str(context.error)}"
        )

def main() -> None:
    """Main function to run the bot"""
    # Start health check server in a separate thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("inbox", inbox_command))
    application.add_handler(CommandHandler("group", group_command))
    
    # Add message handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    print("Bot started successfully! Press Ctrl+C to stop.")
    
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
