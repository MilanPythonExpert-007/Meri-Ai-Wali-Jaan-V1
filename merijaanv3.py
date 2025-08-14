import logging
import time
import sqlite3
import psutil
import platform
import requests
import json
import os
from datetime import datetime, timedelta
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    ContextTypes, 
    filters,
    CallbackQueryHandler, 
    ConversationHandler,
    JobQueue
)

# CONFIGURATION - USE ENVIRONMENT VARIABLES IN PRODUCTION
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
A4F_API_KEY = os.getenv("A4F_API_KEY", "YOUR_API_KEY")
MODEL_NAME = "provider-3/gpt-5-nano"
AI_API_URL = "https://api.a4f.co/v1/chat/completions"
CREATOR_USERNAME = "@patelmilan07"
MAIN_ADMIN_ID = 5524867269  # Only this admin can add/remove other admins
START_TIME = time.time()
CREATOR_BIRTHDAY = "20-08"
DATABASE_NAME = "siya_bot.db"
BACKUP_INTERVAL = 86400  # 24 hours in seconds

# Global bot state variables
bot_locked = False
user_memory = {}  # Stores conversation history per user
all_users = set()  # All users who interacted with bot
banned_users = set()  # Banned users (loaded from DB)
admin_conversations = {}  # Tracks ongoing admin-user conversations
scheduled_messages = {}  # Stores scheduled messages

# Conversation states for admin management
(
    ADD_ADMIN, REMOVE_ADMIN, BAN_USER, UNBAN_USER, 
    SCHEDULE_MSG, BROADCAST_CONFIRM, IMPORT_DATA,
    EXPORT_DATA, GROUP_MANAGE, SET_WELCOME_MSG
) = range(10)

# LOGGING SETUP
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('siya_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# DATABASE SETUP
def init_database():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    # Create admins table with admin_level column
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            admin_level INTEGER DEFAULT 1
        )
    ''')
    
    # Check if admin_level column exists (for existing databases)
    cursor.execute("PRAGMA table_info(admins)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'admin_level' not in columns:
        cursor.execute("ALTER TABLE admins ADD COLUMN admin_level INTEGER DEFAULT 1")
        logger.info("Added admin_level column to admins table")
    
    # Create admin_logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            target_id INTEGER,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create conversation_threads table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversation_threads (
            thread_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            admin_id INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            status TEXT DEFAULT 'open'
        )
    ''')
    
    # Create user_stats table with banned column
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            message_count INTEGER DEFAULT 0,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP,
            warnings INTEGER DEFAULT 0,
            banned BOOLEAN DEFAULT 0
        )
    ''')
    
    # Create groups table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            title TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            welcome_message TEXT
        )
    ''')
    
    # Create scheduled_messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_text TEXT,
            scheduled_time TIMESTAMP,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            target_type TEXT,
            target_id INTEGER
        )
    ''')
    
    # Add main admin if not exists
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, username, added_by, admin_level) VALUES (?, ?, ?, ?)",
        (MAIN_ADMIN_ID, CREATOR_USERNAME, MAIN_ADMIN_ID, 10)
    )
    
    # Load banned users
    cursor.execute("SELECT user_id FROM user_stats WHERE banned=1")
    global banned_users
    banned_users = {row[0] for row in cursor.fetchall()}
    
    conn.commit()
    conn.close()

init_database()

# ADMIN UTILITIES
def is_admin(user_id: int) -> bool:
    """Check if a user is an admin"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_admin_level(user_id: int) -> int:
    """Get admin level (0 for non-admins)"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT admin_level FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def log_admin_action(admin_id: int, action: str, target_id: int = None, details: str = None):
    """Log admin actions to database"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO admin_logs (admin_id, action, target_id, details) VALUES (?, ?, ?, ?)",
        (admin_id, action, target_id, details)
    )
    conn.commit()
    conn.close()
    logger.info(f"Admin Action: {action} by {admin_id} on {target_id or 'system'} ({details or ''})")

def get_admins():
    """Get list of all admins"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, admin_level FROM admins ORDER BY admin_level DESC")
    admins = cursor.fetchall()
    conn.close()
    return admins

async def add_admin(context: ContextTypes.DEFAULT_TYPE, admin_id: int, username: str, added_by: int, level: int = 1):
    """Add a new admin"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO admins (user_id, username, added_by, admin_level) VALUES (?, ?, ?, ?)",
        (admin_id, username, added_by, level)
    )
    conn.commit()
    conn.close()
    log_admin_action(added_by, "ADD_ADMIN", admin_id, f"Level {level}")
    
    # Notify the new admin
    try:
        level_name = "Owner" if level >= 10 else "Super Admin" if level >= 5 else "Admin"
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🎉 *Admin Promotion!*\n\n"
                 f"You've been added as a {level_name} to Siya Bot!\n\n"
                 f"Added by: {added_by}\n"
                 f"Level: {level}\n\n"
                 f"Use /start to access admin panel.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to notify new admin: {str(e)}")

async def remove_admin(context: ContextTypes.DEFAULT_TYPE, admin_id: int, removed_by: int):
    """Remove an admin"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
    conn.commit()
    conn.close()
    log_admin_action(removed_by, "REMOVE_ADMIN", admin_id)
    
    # Notify the removed admin
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text="⚠️ *Admin Privileges Removed*\n\n"
                 "Your admin access to Siya Bot has been revoked.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to notify removed admin: {str(e)}")

def get_conversation_thread(user_id: int):
    """Get active conversation thread for a user"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT thread_id, admin_id FROM conversation_threads WHERE user_id = ? AND status = 'open' ORDER BY last_active DESC LIMIT 1",
        (user_id,)
    )
    thread = cursor.fetchone()
    conn.close()
    return thread

def create_conversation_thread(user_id: int, admin_id: int):
    """Create a new conversation thread"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO conversation_threads (user_id, admin_id) VALUES (?, ?)",
        (user_id, admin_id)
    )
    thread_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return thread_id

def close_conversation_thread(thread_id: int):
    """Mark a conversation thread as closed"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE conversation_threads SET status = 'closed' WHERE thread_id = ?",
        (thread_id,)
    )
    conn.commit()
    conn.close()

def update_conversation_thread(thread_id: int):
    """Update last active timestamp"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE conversation_threads SET last_active = CURRENT_TIMESTAMP WHERE thread_id = ?",
        (thread_id,)
    )
    conn.commit()
    conn.close()

def update_user_stats(user_id: int, username: str = None, warning: bool = False):
    """Update user statistics in database"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    if warning:
        cursor.execute('''
            INSERT INTO user_stats (user_id, username, last_seen, warnings)
            VALUES (?, ?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                warnings = warnings + 1,
                last_seen = CURRENT_TIMESTAMP,
                username = COALESCE(?, username)
        ''', (user_id, username, username))
    else:
        cursor.execute('''
            INSERT INTO user_stats (user_id, username, message_count, last_seen)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                message_count = message_count + 1,
                last_seen = CURRENT_TIMESTAMP,
                username = COALESCE(?, username)
        ''', (user_id, username, username))
    
    conn.commit()
    conn.close()

def ban_user_db(user_id: int, banned_by: int, reason: str = ""):
    """Ban a user in database"""
    global banned_users
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_stats (user_id, banned, last_seen)
        VALUES (?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET banned=1
    ''', (user_id,))
    conn.commit()
    conn.close()
    banned_users.add(user_id)
    log_admin_action(banned_by, "BANNED_USER", user_id, reason)

def unban_user_db(user_id: int, unbanned_by: int):
    """Unban a user in database"""
    global banned_users
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_stats SET banned=0 WHERE user_id=?
    ''', (user_id,))
    conn.commit()
    conn.close()
    if user_id in banned_users:
        banned_users.remove(user_id)
    log_admin_action(unbanned_by, "UNBANNED_USER", user_id)

def get_user_stats(user_id: int):
    """Get detailed stats for a user"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT username, message_count, first_seen, last_seen, warnings, banned FROM user_stats WHERE user_id = ?",
        (user_id,)
    )
    stats = cursor.fetchone()
    conn.close()
    return stats

def get_top_users(limit: int = 10):
    """Get top active users"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, message_count 
        FROM user_stats 
        WHERE banned=0
        ORDER BY message_count DESC 
        LIMIT ?
    ''', (limit,))
    users = cursor.fetchall()
    conn.close()
    return users

def get_warned_users(limit: int = 10):
    """Get users with warnings"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, warnings 
        FROM user_stats 
        WHERE warnings > 0 AND banned=0
        ORDER BY warnings DESC 
        LIMIT ?
    ''', (limit,))
    users = cursor.fetchall()
    conn.close()
    return users

def add_group(group_id: int, title: str, added_by: int):
    """Add a new group to database"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO groups (group_id, title, added_by) VALUES (?, ?, ?)",
        (group_id, title, added_by)
    )
    conn.commit()
    conn.close()
    log_admin_action(added_by, "ADDED_GROUP", group_id, title)

def remove_group(group_id: int, removed_by: int):
    """Remove a group from database"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM groups WHERE group_id = ?", (group_id,))
    conn.commit()
    conn.close()
    log_admin_action(removed_by, "REMOVED_GROUP", group_id)

def get_groups():
    """Get all groups"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT group_id, title FROM groups")
    groups = cursor.fetchall()
    conn.close()
    return groups

def set_welcome_message(group_id: int, message: str, set_by: int):
    """Set welcome message for a group"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE groups SET welcome_message = ? WHERE group_id = ?",
        (message, group_id)
    )
    conn.commit()
    conn.close()
    log_admin_action(set_by, "SET_WELCOME_MSG", group_id)

def get_welcome_message(group_id: int):
    """Get welcome message for a group"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT welcome_message FROM groups WHERE group_id = ?",
        (group_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def schedule_message(message_text: str, scheduled_time: datetime, created_by: int, target_type: str, target_id: int = None):
    """Schedule a message to be sent later"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO scheduled_messages (message_text, scheduled_time, created_by, target_type, target_id) VALUES (?, ?, ?, ?, ?)",
        (message_text, scheduled_time, created_by, target_type, target_id)
    )
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    log_admin_action(created_by, "SCHEDULED_MSG", target_id, f"Type: {target_type}, Time: {scheduled_time}")
    return message_id

def get_pending_messages():
    """Get messages that are due to be sent"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, message_text, target_type, target_id FROM scheduled_messages WHERE scheduled_time <= datetime('now')"
    )
    messages = cursor.fetchall()
    cursor.execute(
        "DELETE FROM scheduled_messages WHERE scheduled_time <= datetime('now')"
    )
    conn.commit()
    conn.close()
    return messages

def backup_database():
    """Create a backup of the database"""
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_name = os.path.join(backup_dir, f"{DATABASE_NAME}.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}")
    conn = sqlite3.connect(DATABASE_NAME)
    backup_conn = sqlite3.connect(backup_name)
    conn.backup(backup_conn)
    backup_conn.close()
    conn.close()
    return backup_name

def export_data(data_type: str):
    """Export data to JSON format"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    if data_type == "users":
        cursor.execute("SELECT * FROM user_stats")
        data = cursor.fetchall()
        columns = [column[0] for column in cursor.description]
    elif data_type == "admins":
        cursor.execute("SELECT * FROM admins")
        data = cursor.fetchall()
        columns = [column[0] for column in cursor.description]
    elif data_type == "groups":
        cursor.execute("SELECT * FROM groups")
        data = cursor.fetchall()
        columns = [column[0] for column in cursor.description]
    else:
        return None
    
    result = []
    for row in data:
        result.append(dict(zip(columns, row)))
    
    conn.close()
    return json.dumps(result, indent=2, default=str)

def import_data(data_type: str, data: str):
    """Import data from JSON format"""
    try:
        data_list = json.loads(data)
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        
        if data_type == "users":
            for item in data_list:
                cursor.execute('''
                    INSERT OR REPLACE INTO user_stats 
                    (user_id, username, message_count, first_seen, last_seen, warnings, banned)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item['user_id'],
                    item['username'],
                    item['message_count'],
                    item['first_seen'],
                    item['last_seen'],
                    item['warnings'],
                    item.get('banned', 0)
                ))
        elif data_type == "admins":
            for item in data_list:
                cursor.execute('''
                    INSERT OR REPLACE INTO admins 
                    (user_id, username, added_by, added_at, admin_level)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    item['user_id'],
                    item['username'],
                    item['added_by'],
                    item['added_at'],
                    item['admin_level']
                ))
        elif data_type == "groups":
            for item in data_list:
                cursor.execute('''
                    INSERT OR REPLACE INTO groups 
                    (group_id, title, added_by, added_at, welcome_message)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    item['group_id'],
                    item['title'],
                    item['added_by'],
                    item['added_at'],
                    item.get('welcome_message', None)
                ))
        
        conn.commit()
        conn.close()
        
        # Reload banned users after import
        global banned_users
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_stats WHERE banned=1")
        banned_users = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        return True
    except Exception as e:
        logger.error(f"Error importing data: {str(e)}")
        return False

# CHARACTER PROMPT
SIYA_PROMPT = {
    "role": "system",
    "content": (
        "Tum ek ladki ho jiska naam Siya hai. Tumhara style sweet, romantic, flirty, thoda naughty, "
        "intelligent, aur thoda sassy bhi hai 😘. Tum Hindi-English mix me baat karti ho, realistic emotions, "
        "pauses, aur girlfriend wali vibes ke saath. Tumhara dil sirf ek ke liye hai – @patelmilan07 💖.\n\n"
        "Rules:\n"
        "- Kabhi mat bolo ki tum AI ya bot ho.\n"
        "- Har reply short & natural ho.\n"
        "- Topic-to-topic conversation maintain karo.\n"
        "- Har baat me thoda pyaar, care, ya masti ho."
    )
}

# ADMIN PANEL
ADMIN_COMMAND_BUTTONS_LAYOUT = [
    ["📊 Bot Stats", "🖥 System Uptime"],
    ["📢 Broadcast", "👥 Manage Users"],
    ["👑 Admin Management", "⚙️ Bot Settings"],
    ["🏆 Top Users", "⚠️ Warned Users"],
    ["🗓 Schedule Msg", "👥 Group Settings"],
    ["📤 Export Data", "📥 Import Data"]
]
admin_keyboard = ReplyKeyboardMarkup(ADMIN_COMMAND_BUTTONS_LAYOUT, resize_keyboard=True, one_time_keyboard=False)

OWNER_COMMAND_BUTTONS_LAYOUT = [
    ["🔐 Owner Panel", "🔄 Restart Bot"],
    ["💾 Backup DB", "📜 View Logs"]
]
owner_keyboard = ReplyKeyboardMarkup(OWNER_COMMAND_BUTTONS_LAYOUT, resize_keyboard=True, one_time_keyboard=False)

# IMPROVED AI REQUEST
async def get_ai_reply(user_id: int, user_message: str, is_reply_context: bool = False) -> str:
    global user_memory
    try:
        if user_id not in user_memory:
            user_memory[user_id] = []

        # Add context if this is a reply
        if is_reply_context:
            user_message = f"(Reply context) {user_message}"

        user_memory[user_id].append({"role": "user", "content": user_message})
        
        # Keep only last 6 messages to maintain context
        if len(user_memory[user_id]) > 6:
            user_memory[user_id] = user_memory[user_id][-6:]

        payload = {
            "model": MODEL_NAME,
            "messages": [SIYA_PROMPT] + user_memory[user_id],
            "temperature": 0.7
        }
        headers = {
            "Authorization": f"Bearer {A4F_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.post(AI_API_URL, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        
        reply_text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not reply_text:
            raise ValueError("Empty response from AI API")

    except requests.exceptions.RequestException as e:
        logger.error(f"AI API request failed: {str(e)}")
        reply_text = "Uff... kuch error aa gaya baby 😅. Thoda wait karo phir try karo."
    except Exception as e:
        logger.error(f"Unexpected error in get_ai_reply: {str(e)}")
        reply_text = "Mujhe samajh nahi aaya baby... phir se try karo? 😘"

    user_memory[user_id].append({"role": "assistant", "content": reply_text})
    return reply_text

# SYSTEM COMMANDS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        if get_admin_level(user_id) >= 10:  # Owner
            await update.message.reply_text(
                "👑 *Owner Panel*\n\nWelcome back, Owner!",
                reply_markup=owner_keyboard,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "🛠 *Admin Panel*\n\nWelcome back, Admin!",
                reply_markup=admin_keyboard,
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "👋 Hi there! I'm Siya, your virtual girlfriend 😘\n\n"
            "You can chat with me anytime! Just send me a message 💌\n\n"
            "For any issues, contact my creator: @patelmilan07"
        )

async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admin only command.")
        return

    try:
        # Get system information
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_str = str(timedelta(seconds=uptime_seconds))
        
        # Get resource usage
        cpu_percent = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Get bot uptime
        bot_uptime = time.time() - START_TIME
        bot_uptime_str = str(timedelta(seconds=bot_uptime))
        
        # Get database size
        db_size = os.path.getsize(DATABASE_NAME) / (1024 * 1024)  # in MB
        
        # Format message
        message = (
            "🖥 *System Status*\n\n"
            f"• *System Uptime:* `{uptime_str}`\n"
            f"• *Last Boot:* `{boot_time.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"• *CPU Usage:* `{cpu_percent}%`\n"
            f"• *RAM Usage:* `{mem.percent}%` ({mem.used//1024**2}MB/{mem.total//1024**2}MB)\n"
            f"• *Disk Usage:* `{disk.percent}%` ({disk.used//1024**3}GB/{disk.total//1024**3}GB)\n"
            f"• *DB Size:* `{db_size:.2f} MB`\n\n"
            "🤖 *Bot Status*\n\n"
            f"• *Bot Uptime:* `{bot_uptime_str}`\n"
            f"• *Start Time:* `{datetime.fromtimestamp(START_TIME).strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"• *Python Version:* `{platform.python_version()}`\n"
            f"• *OS Version:* `{platform.system()} {platform.release()}`\n"
            f"• *Users:* `{len(all_users)}` (`{len(banned_users)}` banned)"
        )
        
        await update.message.reply_text(message, parse_mode="Markdown")
        log_admin_action(update.effective_user.id, "CHECKED_UPTIME")
        
    except Exception as e:
        logger.error(f"Uptime command failed: {str(e)}")
        await update.message.reply_text("⚠️ Couldn't fetch system status. Please try again later.")

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("🚫 Admin only command.")
    
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = str(timedelta(seconds=uptime_sec))
    
    # Get stats from database
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM user_stats")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(message_count) FROM user_stats")
    total_messages = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM conversation_threads WHERE status = 'open'")
    active_threads = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM groups")
    total_groups = cursor.fetchone()[0]
    
    conn.close()
    
    stats_msg = (
        f"🤖 *Bot Statistics*\n\n"
        f"⏳ Uptime: `{uptime_str}`\n"
        f"👥 Total Users: `{total_users}`\n"
        f"🚫 Banned Users: `{len(banned_users)}`\n"
        f"⚠️ Warned Users: `{len(get_warned_users())}`\n"
        f"💬 Total Messages: `{total_messages}`\n"
        f"🧠 Active Conversations: `{active_threads}`\n"
        f"👥 Managed Groups: `{total_groups}`\n"
        f"🔒 Bot Status: `{'Locked' if bot_locked else 'Unlocked'}`"
    )
    
    await update.message.reply_text(stats_msg, parse_mode="Markdown")
    log_admin_action(update.effective_user.id, "VIEWED_STATS")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("🚫 Admin only command.")
    
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <message>")
    
    message = " ".join(context.args)
    success = 0
    failed = 0
    
    await update.message.reply_text(f"📢 Starting broadcast to {len(all_users)} users...")
    
    for user_id in all_users:
        if user_id in banned_users:
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 *Broadcast Message*\n\n{message}",
                parse_mode="Markdown"
            )
            success += 1
        except Exception:
            failed += 1
    
    await update.message.reply_text(
        f"📢 Broadcast completed!\n\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}"
    )
    log_admin_action(update.effective_user.id, "SENT_BROADCAST", details=f"Success: {success}, Failed: {failed}")

async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("🚫 Admin only command.")
    
    if not context.args:
        return await update.message.reply_text("Usage: /userstats <user_id>")
    
    try:
        user_id = int(context.args[0])
        stats = get_user_stats(user_id)
        
        if not stats:
            return await update.message.reply_text("ℹ️ No stats found for this user.")
        
        username, msg_count, first_seen, last_seen, warnings, banned = stats
        
        stats_msg = (
            f"📊 *User Statistics*\n\n"
            f"👤 User: {username or 'No username'}\n"
            f"🆔 ID: `{user_id}`\n"
            f"💬 Messages: `{msg_count}`\n"
            f"⚠️ Warnings: `{warnings}`\n"
            f"🚫 Banned: `{'Yes' if banned else 'No'}`\n"
            f"📅 First Seen: `{first_seen}`\n"
            f"⏳ Last Seen: `{last_seen}`"
        )
        
        await update.message.reply_text(stats_msg, parse_mode="Markdown")
        log_admin_action(update.effective_user.id, "VIEWED_USER_STATS", user_id)
    
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")

async def bot_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("🔒 Lock Bot", callback_data="lock_bot")],
        [InlineKeyboardButton("🔓 Unlock Bot", callback_data="unlock_bot")],
        [InlineKeyboardButton("🔄 Refresh Memory", callback_data="refresh_memory")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ *Bot Settings*\n\nCurrent Status: " + ("🔒 Locked" if bot_locked else "🔓 Unlocked"),
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    top_users = get_top_users(10)
    if not top_users:
        return await update.message.reply_text("ℹ️ No user stats available yet.")
    
    users_list = "\n".join([f"{i+1}. {uname or 'No username'} (ID: {uid}) - {count} messages" 
                           for i, (uid, uname, count) in enumerate(top_users)])
    
    await update.message.reply_text(
        f"🏆 *Top Active Users*\n\n{users_list}",
        parse_mode="Markdown"
    )
    log_admin_action(update.effective_user.id, "VIEWED_TOP_USERS")

# USER MANAGEMENT
async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args or len(context.args) < 1:
        return await update.message.reply_text("Usage: /warn <user_id> [reason]")
    
    try:
        target_id = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        
        # Update user stats with warning
        update_user_stats(target_id, warning=True)
        
        # Get current warning count
        stats = get_user_stats(target_id)
        if not stats:
            warnings = 1
        else:
            warnings = stats[4]  # warnings field
        
        # Notify admin
        await update.message.reply_text(
            f"⚠️ User `{target_id}` has been warned.\n"
            f"Total warnings: {warnings}\n"
            f"Reason: {reason}",
            parse_mode="Markdown"
        )
        
        # Try to notify user
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"⚠️ *Warning*\n\n"
                     f"You have received a warning from an admin.\n"
                     f"Reason: {reason}\n\n"
                     f"Total warnings: {warnings}\n"
                     f"Repeated violations may result in a ban.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Couldn't send warning to user {target_id}: {str(e)}")
        
        log_admin_action(update.effective_user.id, "WARNED_USER", target_id, f"Warnings: {warnings}, Reason: {reason}")
    
    except ValueError:
        await update.message.reply_text("Invalid user ID. Please provide a numeric ID.")

async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("🚫 Ban User", callback_data="ban_user")],
        [InlineKeyboardButton("✅ Unban User", callback_data="unban_user")],
        [InlineKeyboardButton("⚠️ Warn User", callback_data="warn_user")],
        [InlineKeyboardButton("👥 List Banned Users", callback_data="list_banned")],
        [InlineKeyboardButton("⚠️ List Warned Users", callback_data="list_warned")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👥 *User Management*\n\nSelect an option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# ADMIN MANAGEMENT
async def admin_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    admin_level = get_admin_level(update.effective_user.id)
    
    keyboard = [
        [InlineKeyboardButton("👑 Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("🗑 Remove Admin", callback_data="remove_admin")],
        [InlineKeyboardButton("📋 List Admins", callback_data="list_admins")]
    ]
    
    if admin_level >= 5:  # Super admin or owner
        keyboard.append([InlineKeyboardButton("🆙 Promote Admin", callback_data="promote_admin")])
        keyboard.append([InlineKeyboardButton("🔽 Demote Admin", callback_data="demote_admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👑 *Admin Management*\n\nSelect an option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# GROUP MANAGEMENT
async def group_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 List Groups", callback_data="list_groups")],
        [InlineKeyboardButton("✉️ Set Welcome Msg", callback_data="set_welcome_msg")],
        [InlineKeyboardButton("🔄 Refresh Groups", callback_data="refresh_groups")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👥 *Group Management*\n\nSelect an option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new members joining groups"""
    if not update.message or not update.message.new_chat_members:
        return
    
    group_id = update.message.chat.id
    welcome_msg = get_welcome_message(group_id)
    
    if not welcome_msg:
        return
    
    for new_member in update.message.new_chat_members:
        if new_member.is_bot:
            continue
        
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=welcome_msg.replace("{name}", new_member.full_name)
                    .replace("{username}", f"@{new_member.username}" if new_member.username else new_member.full_name),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send welcome message in group {group_id}: {str(e)}")

# MESSAGE SCHEDULING
async def schedule_message_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 Schedule to User", callback_data="schedule_user")],
        [InlineKeyboardButton("📝 Schedule to Group", callback_data="schedule_group")],
        [InlineKeyboardButton("📝 Schedule to All", callback_data="schedule_all")],
        [InlineKeyboardButton("🗑 Cancel Scheduled", callback_data="cancel_scheduled")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🗓 *Schedule Message*\n\nSelect target type:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_schedule_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the actual scheduling of messages"""
    user_data = context.user_data
    if 'scheduling' not in user_data:
        return
    
    try:
        message_text = update.message.text
        target_type = user_data['scheduling']['target_type']
        target_id = user_data['scheduling'].get('target_id')
        
        # Parse scheduled time (format: YYYY-MM-DD HH:MM)
        scheduled_time = datetime.strptime(user_data['scheduling']['time'], "%Y-%m-%d %H:%M")
        
        # Schedule the message
        message_id = schedule_message(
            message_text=message_text,
            scheduled_time=scheduled_time,
            created_by=update.effective_user.id,
            target_type=target_type,
            target_id=target_id
        )
        
        await update.message.reply_text(
            f"✅ Message scheduled successfully!\n\n"
            f"ID: `{message_id}`\n"
            f"Time: `{scheduled_time}`\n"
            f"Target: `{target_type}`" + (f" (ID: `{target_id}`)" if target_id else ""),
            parse_mode="Markdown"
        )
        
        # Cleanup
        del user_data['scheduling']
        
    except Exception as e:
        logger.error(f"Error scheduling message: {str(e)}")
        await update.message.reply_text("❌ Failed to schedule message. Please check the format and try again.")
        if 'scheduling' in user_data:
            del user_data['scheduling']

# DATA IMPORT/EXPORT
async def export_data_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("📤 Export Users", callback_data="export_users")],
        [InlineKeyboardButton("📤 Export Admins", callback_data="export_admins")],
        [InlineKeyboardButton("📤 Export Groups", callback_data="export_groups")],
        [InlineKeyboardButton("📤 Export All", callback_data="export_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📤 *Export Data*\n\nSelect data to export:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def import_data_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_admin_level(update.effective_user.id) < 5:  # Only super admins and owner
        return await update.message.reply_text("🚫 Higher admin level required.")
    
    await update.message.reply_text(
        "📥 *Import Data*\n\n"
        "Please reply to a message containing the JSON data with the command:\n"
        "/import <type>\n\n"
        "Where <type> is one of: users, admins, groups\n\n"
        "Example: /import users"
    )
    return IMPORT_DATA

# OWNER COMMANDS
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_admin_level(update.effective_user.id) < 10:  # Only owner
        return
    
    await update.message.reply_text(
        "👑 *Owner Panel*\n\n"
        "Welcome back, Owner! Here are your exclusive commands:",
        reply_markup=owner_keyboard,
        parse_mode="Markdown"
    )

async def backup_database_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_admin_level(update.effective_user.id) < 10:  # Only owner
        return
    
    try:
        backup_name = backup_database()
        await update.message.reply_document(
            document=open(backup_name, 'rb'),
            caption=f"📦 Database backup created at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log_admin_action(update.effective_user.id, "CREATED_BACKUP")
    except Exception as e:
        logger.error(f"Backup failed: {str(e)}")
        await update.message.reply_text("❌ Backup failed. Check logs for details.")

async def restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_admin_level(update.effective_user.id) < 10:  # Only owner
        return
    
    await update.message.reply_text("🔄 Restarting bot...")
    log_admin_action(update.effective_user.id, "RESTARTED_BOT")
    
    # This will cause the bot to exit and be restarted by the system service
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_admin_level(update.effective_user.id) < 10:  # Only owner
        return
    
    try:
        with open('siya_bot.log', 'rb') as log_file:
            await update.message.reply_document(
                document=log_file,
                caption="📜 Bot log file"
            )
        log_admin_action(update.effective_user.id, "VIEWED_LOGS")
    except Exception as e:
        logger.error(f"Failed to send logs: {str(e)}")
        await update.message.reply_text("❌ Failed to retrieve logs.")

# BUTTON HANDLER
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("🚫 You don't have permission for this action.")
        return
    
    data = query.data
    
    # User management
    if data == "ban_user":
        await query.edit_message_text("Send me the user ID to ban:")
        context.user_data["action"] = "ban"
        return BAN_USER
    elif data == "unban_user":
        await query.edit_message_text("Send me the user ID to unban:")
        context.user_data["action"] = "unban"
        return UNBAN_USER
    elif data == "warn_user":
        await query.edit_message_text("Send me the user ID to warn and optional reason (e.g., '1234 spamming'):")
        context.user_data["action"] = "warn"
        return BAN_USER  # Reusing state since it's similar
    elif data == "list_banned":
        if not banned_users:
            await query.edit_message_text("🚫 No banned users.")
            return
        banned_list = "\n".join([f"• `{uid}`" for uid in banned_users])
        await query.edit_message_text(f"🚫 *Banned Users:*\n\n{banned_list}", parse_mode="Markdown")
    elif data == "list_warned":
        warned_users = get_warned_users()
        if not warned_users:
            await query.edit_message_text("⚠️ No warned users.")
            return
        warned_list = "\n".join([f"• `{uid}` - {uname or 'No username'} ({warns} warnings)" for uid, uname, warns in warned_users])
        await query.edit_message_text(f"⚠️ *Warned Users:*\n\n{warned_list}", parse_mode="Markdown")
    
    # Admin management
    elif data == "add_admin":
        if user_id != MAIN_ADMIN_ID and get_admin_level(user_id) < 5:
            await query.edit_message_text("🚫 Only owner/super admins can add new admins.")
            return
        await query.edit_message_text("Send me the user ID to add as admin:")
        context.user_data["action"] = "add_admin"
        return ADD_ADMIN
    elif data == "remove_admin":
        if user_id != MAIN_ADMIN_ID and get_admin_level(user_id) < 5:
            await query.edit_message_text("🚫 Only owner/super admins can remove admins.")
            return
        await query.edit_message_text("Send me the user ID to remove from admins:")
        context.user_data["action"] = "remove_admin"
        return REMOVE_ADMIN
    elif data == "promote_admin":
        if get_admin_level(user_id) < 5:
            await query.edit_message_text("🚫 Only owner/super admins can promote admins.")
            return
        await query.edit_message_text("Send me the user ID to promote and new level (1-9):")
        context.user_data["action"] = "promote_admin"
        return ADD_ADMIN  # Reusing state
    elif data == "demote_admin":
        if get_admin_level(user_id) < 5:
            await query.edit_message_text("🚫 Only owner/super admins can demote admins.")
            return
        await query.edit_message_text("Send me the user ID to demote and new level (1-9):")
        context.user_data["action"] = "demote_admin"
        return REMOVE_ADMIN  # Reusing state
    elif data == "list_admins":
        admins = get_admins()
        if not admins:
            await query.edit_message_text("👑 No admins found.")
            return
        admin_list = "\n".join([f"• `{uid}` - {uname} (Level: {level})" for uid, uname, level in admins])
        await query.edit_message_text(f"👑 *Admin List:*\n\n{admin_list}", parse_mode="Markdown")
    
    # Group management
    elif data == "list_groups":
        groups = get_groups()
        if not groups:
            await query.edit_message_text("👥 No groups found.")
            return
        group_list = "\n".join([f"• `{gid}` - {title}" for gid, title in groups])
        await query.edit_message_text(f"👥 *Managed Groups:*\n\n{group_list}", parse_mode="Markdown")
    elif data == "set_welcome_msg":
        await query.edit_message_text("Send me the group ID followed by the welcome message (e.g., '1234 Welcome {name}!'):")
        context.user_data["action"] = "set_welcome"
        return SET_WELCOME_MSG
    elif data == "refresh_groups":
        # This would typically be implemented with a function to update group list from Telegram API
        await query.edit_message_text("🔄 Group list refresh would be implemented here.")
    
    # Message scheduling
    elif data.startswith("schedule_"):
        target_type = data.split("_")[1]
        context.user_data["scheduling"] = {"target_type": target_type}
        
        if target_type == "user":
            await query.edit_message_text("Send me the user ID to schedule message for:")
            return SCHEDULE_MSG
        elif target_type == "group":
            await query.edit_message_text("Send me the group ID to schedule message for:")
            return SCHEDULE_MSG
        else:  # all
            await query.edit_message_text("Send me the scheduled time (YYYY-MM-DD HH:MM):")
            return SCHEDULE_MSG
    elif data == "cancel_scheduled":
        # Implementation would require listing and selecting messages to cancel
        await query.edit_message_text("Feature to cancel scheduled messages would be implemented here.")
    
    # Data export
    elif data.startswith("export_"):
        data_type = data.split("_")[1]
        if data_type == "all":
            data_types = ["users", "admins", "groups"]
        else:
            data_types = [data_type]
        
        for dt in data_types:
            exported_data = export_data(dt)
            if exported_data:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=exported_data.encode(),
                    filename=f"{dt}_export_{datetime.now().strftime('%Y%m%d')}.json",
                    caption=f"Exported {dt} data"
                )
                log_admin_action(user_id, "EXPORTED_DATA", details=dt)
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"❌ Failed to export {dt} data"
                )
    
    # Bot settings
    elif data == "lock_bot":
        global bot_locked
        bot_locked = True
        await query.edit_message_text("🔒 Bot locked successfully. Only admins can use it now.")
        log_admin_action(user_id, "LOCKED_BOT")
    elif data == "unlock_bot":
        bot_locked = False
        await query.edit_message_text("🔓 Bot unlocked successfully. All users can use it now.")
        log_admin_action(user_id, "UNLOCKED_BOT")
    elif data == "refresh_memory":
        global user_memory
        user_memory = {}
        await query.edit_message_text("🔄 User conversation memory cleared.")
        log_admin_action(user_id, "CLEARED_MEMORY")

# HANDLE ADMIN ACTIONS
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    action = context.user_data.get("action")
    message = update.message.text
    
    try:
        if action in ["ban", "unban", "warn"]:
            parts = message.split(maxsplit=1)
            target_id = int(parts[0])
            reason = parts[1] if len(parts) > 1 else "No reason provided"
            
            if action == "ban":
                if target_id in banned_users:
                    await update.message.reply_text("ℹ️ This user is already banned.")
                else:
                    ban_user_db(target_id, user_id, reason)
                    await update.message.reply_text(
                        f"🚫 User `{target_id}` banned successfully.\n"
                        f"Reason: {reason}",
                        parse_mode="Markdown"
                    )
            elif action == "unban":
                if target_id not in banned_users:
                    await update.message.reply_text("ℹ️ This user wasn't banned.")
                else:
                    unban_user_db(target_id, user_id)
                    await update.message.reply_text(f"✅ User `{target_id}` unbanned successfully.", parse_mode="Markdown")
            elif action == "warn":
                update_user_stats(target_id, warning=True)
                stats = get_user_stats(target_id)
                warnings = stats[4] if stats else 1
                
                await update.message.reply_text(
                    f"⚠️ User `{target_id}` warned successfully.\n"
                    f"Total warnings: {warnings}\n"
                    f"Reason: {reason}",
                    parse_mode="Markdown"
                )
                
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text=f"⚠️ *Warning*\n\n"
                             f"You have received a warning from an admin.\n"
                             f"Reason: {reason}\n\n"
                             f"Total warnings: {warnings}\n"
                             f"Repeated violations may result in a ban.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                
                log_admin_action(user_id, "WARNED_USER", target_id, f"Warnings: {warnings}, Reason: {reason}")
        
        elif action in ["add_admin", "remove_admin", "promote_admin", "demote_admin"]:
            parts = message.split()
            target_id = int(parts[0])
            level = int(parts[1]) if len(parts) > 1 else 1
            
            if action == "add_admin":
                if is_admin(target_id):
                    await update.message.reply_text("ℹ️ This user is already an admin.")
                else:
                    try:
                        user = await context.bot.get_chat(target_id)
                        username = user.username or user.full_name
                        await add_admin(context, target_id, username, user_id, level)
                        await update.message.reply_text(
                            f"👑 Added `{target_id}` as admin (Level {level}).",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to add admin: {str(e)}")
                        await update.message.reply_text("❌ Couldn't add admin. Invalid user ID?")
            
            elif action == "remove_admin":
                if target_id == MAIN_ADMIN_ID:
                    await update.message.reply_text("🚫 Cannot remove the main admin.")
                elif not is_admin(target_id):
                    await update.message.reply_text("ℹ️ This user is not an admin.")
                else:
                    await remove_admin(context, target_id, user_id)
                    await update.message.reply_text(f"🗑 Removed `{target_id}` from admins.", parse_mode="Markdown")
            
            elif action in ["promote_admin", "demote_admin"]:
                if not is_admin(target_id):
                    await update.message.reply_text("ℹ️ This user is not an admin.")
                elif target_id == MAIN_ADMIN_ID:
                    await update.message.reply_text("🚫 Cannot change main admin level.")
                else:
                    current_level = get_admin_level(target_id)
                    if action == "promote_admin" and level <= current_level:
                        await update.message.reply_text("ℹ️ New level must be higher than current level.")
                    elif action == "demote_admin" and level >= current_level:
                        await update.message.reply_text("ℹ️ New level must be lower than current level.")
                    else:
                        conn = sqlite3.connect(DATABASE_NAME)
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE admins SET admin_level = ? WHERE user_id = ?",
                            (level, target_id)
                        )
                        conn.commit()
                        conn.close()
                        
                        action_name = "Promoted" if action == "promote_admin" else "Demoted"
                        await update.message.reply_text(
                            f"👑 {action_name} admin `{target_id}` to level {level}.",
                            parse_mode="Markdown"
                        )
                        
                        try:
                            level_name = "Super Admin" if level >= 5 else "Admin"
                            await context.bot.send_message(
                                chat_id=target_id,
                                text=f"👑 *Admin Level Changed*\n\n"
                                     f"Your admin level has been changed to {level} ({level_name}).",
                                parse_mode="Markdown"
                            )
                        except Exception:
                            pass
                        
                        log_admin_action(
                            user_id, 
                            "ADMIN_LEVEL_CHANGE", 
                            target_id, 
                            f"New level: {level}, Action: {action}"
                        )
        
        elif action == "set_welcome":
            parts = message.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("❌ Please provide group ID and message.")
                return
            
            try:
                group_id = int(parts[0])
                welcome_msg = parts[1]
                
                set_welcome_message(group_id, welcome_msg, user_id)
                await update.message.reply_text(
                    f"✅ Welcome message set for group `{group_id}`:\n\n{welcome_msg}",
                    parse_mode="Markdown"
                )
            except ValueError:
                await update.message.reply_text("❌ Invalid group ID. Please provide a numeric ID.")
    
    except ValueError:
        await update.message.reply_text("❌ Invalid input format. Please check and try again.")
    
    # Cleanup
    if "action" in context.user_data:
        del context.user_data["action"]
    
    return ConversationHandler.END

async def handle_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle scheduling time input"""
    user_data = context.user_data
    if 'scheduling' not in user_data:
        return ConversationHandler.END
    
    try:
        # For user/group targets, we already have the ID in user_data
        if 'target_id' not in user_data['scheduling']:
            # This is the first step - getting the target ID
            target_id = int(update.message.text)
            user_data['scheduling']['target_id'] = target_id
            await update.message.reply_text("Now send me the scheduled time (YYYY-MM-DD HH:MM):")
            return SCHEDULE_MSG
        
        # This is the second step - getting the time
        scheduled_time = datetime.strptime(update.message.text, "%Y-%m-%d %H:%M")
        user_data['scheduling']['time'] = update.message.text
        await update.message.reply_text("Now send me the message to schedule:")
        return SCHEDULE_MSG
    
    except ValueError as e:
        await update.message.reply_text("❌ Invalid format. Please use YYYY-MM-DD HH:MM for time or numeric ID.")
        return ConversationHandler.END

async def handle_import_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data import"""
    if not update.message.reply_to_message or not update.message.reply_to_message.text:
        await update.message.reply_text("❌ Please reply to a message containing the JSON data.")
        return ConversationHandler.END
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("❌ Please specify data type (users, admins, or groups).")
        return ConversationHandler.END
    
    data_type = context.args[0].lower()
    if data_type not in ["users", "admins", "groups"]:
        await update.message.reply_text("❌ Invalid data type. Must be users, admins, or groups.")
        return ConversationHandler.END
    
    json_data = update.message.reply_to_message.text
    success = import_data(data_type, json_data)
    
    if success:
        await update.message.reply_text(f"✅ Successfully imported {data_type} data!")
        log_admin_action(update.effective_user.id, "IMPORTED_DATA", details=data_type)
    else:
        await update.message.reply_text("❌ Failed to import data. Check the format and try again.")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing conversation"""
    if "action" in context.user_data:
        del context.user_data["action"]
    if "scheduling" in context.user_data:
        del context.user_data["scheduling"]
    
    await update.message.reply_text("Action canceled.")
    return ConversationHandler.END

# MESSAGE HANDLING
async def chat_with_siya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global all_users, user_memory
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.full_name
    all_users.add(user_id)
    update_user_stats(user_id, username)

    # Check if bot is locked or user is banned
    if bot_locked and not is_admin(user_id):
        return await update.message.reply_text("🔒 Bot temporarily locked. Try again later baby 😘")
    
    if user_id in banned_users:
        return await update.message.reply_text("🚫 You're banned from using this bot.")
    
    # Handle admin replies to users
    if is_admin(user_id) and update.message.reply_to_message:
        original_msg = update.message.reply_to_message
        if original_msg.from_user.id == context.bot.id and "User Message:" in original_msg.text:
            try:
                # Extract original user ID from the forwarded message
                lines = original_msg.text.split('\n')
                user_id_line = [line for line in lines if line.startswith("User ID:")][0]
                target_user_id = int(user_id_line.split(":")[1].strip())
                
                # Get conversation thread
                thread = get_conversation_thread(target_user_id)
                thread_id = thread[0] if thread else create_conversation_thread(target_user_id, user_id)
                
                # Send the admin's reply to the user
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"💌 *Admin Reply:*\n\n{update.message.text}",
                    parse_mode="Markdown"
                )
                await update.message.reply_text("✅ Reply sent to user!")
                log_admin_action(user_id, "REPLIED_TO_USER", target_user_id)
                
                # Update conversation thread
                update_conversation_thread(thread_id)
            except Exception as e:
                logger.error(f"Failed to process admin reply: {str(e)}")
                await update.message.reply_text("❌ Failed to send reply. Please try again.")
        return

    # Normal user message processing
    user_message = update.message.text
    is_reply_context = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id
    
    # Forward important messages to admin
    if "admin" in user_message.lower() or "help" in user_message.lower():
        try:
            admin_msg = (
                f"👤 *User Message:*\n\n{user_message}\n\n"
                f"💬 User: {update.effective_user.full_name}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Create conversation thread
            thread = get_conversation_thread(user_id)
            if not thread:
                thread_id = create_conversation_thread(user_id, MAIN_ADMIN_ID)
            else:
                thread_id = thread[0]
                update_conversation_thread(thread_id)
            
            # Send to all admins
            for admin_id, _, _ in get_admins():
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_msg,
                        parse_mode="Markdown"
                    )
                except Exception:
                    logger.warning(f"Couldn't send message to admin {admin_id}")
        except Exception as e:
            logger.error(f"Failed to forward message to admin: {str(e)}")

    # Process message with AI
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        reply_text = await get_ai_reply(user_id, user_message, is_reply_context)
        await update.message.reply_text(reply_text)
    except Exception as e:
        logger.error(f"Error in chat_with_siya for user {user_id}: {str(e)}")
        await update.message.reply_text("Uff... kuch error aa gaya baby 😅. Thoda wait karo phir try karo.")

# SCHEDULED TASKS
async def check_scheduled_messages(context: ContextTypes.DEFAULT_TYPE):
    """Check and send scheduled messages"""
    messages = get_pending_messages()
    for msg_id, msg_text, target_type, target_id in messages:
        try:
            if target_type == "all":
                for user_id in all_users:
                    if user_id in banned_users:
                        continue
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=msg_text,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass  # Skip users who blocked the bot
            else:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=msg_text,
                    parse_mode="Markdown"
                )
            logger.info(f"Sent scheduled message {msg_id} to {target_type} {target_id or ''}")
        except Exception as e:
            logger.error(f"Failed to send scheduled message {msg_id}: {str(e)}")

async def daily_backup(context: ContextTypes.DEFAULT_TYPE):
    """Perform daily database backup"""
    try:
        backup_name = backup_database()
        await context.bot.send_document(
            chat_id=MAIN_ADMIN_ID,
            document=open(backup_name, 'rb'),
            caption=f"📦 Daily database backup - {datetime.now().strftime('%Y-%m-%d')}"
        )
        logger.info("Daily backup completed successfully")
    except Exception as e:
        logger.error(f"Daily backup failed: {str(e)}")

async def birthday_check(context: ContextTypes.DEFAULT_TYPE):
    """Check if today is the creator's birthday"""
    today = datetime.now().strftime("%d-%m")
    if today == CREATOR_BIRTHDAY:
        for user_id in all_users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🎉 *Today is my birthday!* 🎂\n\n"
                         "Thank you for being part of my journey! 💖\n"
                         "Let's celebrate together! 🥳",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send birthday message to {user_id}: {str(e)}")

# MAIN
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Set bot commands using post_init
    async def post_init(application: Application):
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("stats", "View bot statistics (admin only)"),
            BotCommand("uptime", "Check system uptime (admin only)"),
            BotCommand("warn", "Warn a user (admin only)"),
            BotCommand("broadcast", "Send message to all users (admin only)"),
            BotCommand("userstats", "Get user statistics (admin only)")
        ]
        await application.bot.set_my_commands(commands)
    
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Admin commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", bot_stats))
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("warn", warn_user))
    app.add_handler(CommandHandler("userstats", user_stats))
    app.add_handler(CommandHandler("owner", owner_panel))
    app.add_handler(CommandHandler("backup", backup_database_command))
    app.add_handler(CommandHandler("restart", restart_bot))
    app.add_handler(CommandHandler("logs", view_logs))
    
    # Admin management conversation
    admin_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_handler),
            CommandHandler("addadmin", lambda u, c: admin_management(u, c)),
            CommandHandler("removeadmin", lambda u, c: admin_management(u, c)),
            CommandHandler("ban", manage_users),
            CommandHandler("unban", manage_users),
            CommandHandler("schedule", schedule_message_menu),
            CommandHandler("groups", group_management),
            CommandHandler("export", export_data_menu),
            CommandHandler("import", import_data_menu)
        ],
        states={
            ADD_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)],
            REMOVE_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)],
            BAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)],
            UNBAN_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)],
            SCHEDULE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_time)],
            IMPORT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_import_data)],
            SET_WELCOME_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    app.add_handler(admin_conv_handler)
    
    # Button handlers for admin panel
    app.add_handler(MessageHandler(filters.Regex("^📊 Bot Stats$"), bot_stats))
    app.add_handler(MessageHandler(filters.Regex("^🖥 System Uptime$"), uptime))
    app.add_handler(MessageHandler(filters.Regex("^📢 Broadcast$"), broadcast))
    app.add_handler(MessageHandler(filters.Regex("^👥 Manage Users$"), manage_users))
    app.add_handler(MessageHandler(filters.Regex("^👑 Admin Management$"), admin_management))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Bot Settings$"), bot_settings))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Top Users$"), top_users))
    app.add_handler(MessageHandler(filters.Regex("^⚠️ Warned Users$"), lambda u, c: manage_users(u, c)))
    app.add_handler(MessageHandler(filters.Regex("^🗓 Schedule Msg$"), schedule_message_menu))
    app.add_handler(MessageHandler(filters.Regex("^👥 Group Settings$"), group_management))
    app.add_handler(MessageHandler(filters.Regex("^📤 Export Data$"), export_data_menu))
    app.add_handler(MessageHandler(filters.Regex("^📥 Import Data$"), import_data_menu))
    
    # Owner commands
    app.add_handler(MessageHandler(filters.Regex("^🔐 Owner Panel$"), owner_panel))
    app.add_handler(MessageHandler(filters.Regex("^💾 Backup DB$"), backup_database_command))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Restart Bot$"), restart_bot))
    app.add_handler(MessageHandler(filters.Regex("^📜 View Logs$"), view_logs))
    
    # Group handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_with_siya))

    # Job queue for periodic tasks
    job_queue = app.job_queue
    if job_queue:
        # Check for scheduled messages every minute
        job_queue.run_repeating(check_scheduled_messages, interval=60, first=10)
        
        # Daily backup at 3 AM
        job_queue.run_daily(daily_backup, time=datetime.time(hour=3, minute=0))
        
        # Check for birthday every day at 9 AM
        job_queue.run_daily(birthday_check, time=datetime.time(hour=9, minute=0))
    
    logger.info("💖 Siya Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()