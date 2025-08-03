import os
import sys
import time
import random
import sqlite3
import threading
import asyncio
import logging
import datetime
import json
import re
import requests
import aiohttp
import psutil
import math
import socket
import traceback
from flask import Flask, send_file
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    JobQueue
)

# ===== Configuration =====
BOT_TOKEN = "7604799948:AAErATEcV5K12f26k4hi8h_5KZiWyHogPf4"
A4F_API_KEY = "ddc-a4f-e707cb20215142aaa6e96fdd07faaf24"  # Replace with valid API key
MODEL_NAME = "provider-1/llama-3.3-70b-instruct-turbo"  # Using more reliable model
AI_API_URL = "https://api.a4f.co/v1/chat/completions"  # Primary endpoint
FALLBACK_API_URL = "https://api.a4f.co/v1/chat/completions"  # Fallback endpoint
SUPER_ADMIN_ID = 5524867269  # Milan's user ID
CREATOR_USERNAME = "@patelmilan07"
DB_FILE = "siya_bot.db"
BACKUP_DIR = "db_backups"
LOG_FILE = "siya_bot.log"
CONVERSATION_MEMORY_LENGTH = 8  # Reduced for better performance
MAX_HISTORY_TOKENS = 1200  # Reduced token limit
MAX_MESSAGE_LENGTH = 4096
START_TIME = time.time()
PORT = int(os.environ.get('PORT', 5000))
BROADCAST_BATCH_SIZE = 50
BROADCAST_DELAY = 0.5
RATE_LIMIT = 5
BAN_THRESHOLD = 15
MAINTENANCE_INTERVAL = 3600
API_RETRY_DELAY = 1.5
API_MAX_RETRIES = 3
API_TIMEOUT = 20

# ===== Enhanced Logging Setup =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,  # Changed from DEBUG to INFO for cleaner logs
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create backup directory
os.makedirs(BACKUP_DIR, exist_ok=True)

# ===== Database Setup =====
def init_db():
    """Initialize SQLite database with advanced schema"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            
            # Users table with analytics
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                spam_score INTEGER DEFAULT 0
            )''')
            
            # Admins table with permissions
            c.execute('''CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                permissions TEXT DEFAULT 'basic'
            )''')
            
            # Conversation history with topic tracking
            c.execute('''CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                topic_id INTEGER DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Broadcast logs with performance metrics
            c.execute('''CREATE TABLE IF NOT EXISTS broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message TEXT,
                total_users INTEGER,
                success_count INTEGER,
                failed_count INTEGER,
                start_time REAL,
                end_time REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # System stats for analytics
            c.execute('''CREATE TABLE IF NOT EXISTS system_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE UNIQUE DEFAULT CURRENT_DATE,
                active_users INTEGER DEFAULT 0,
                new_users INTEGER DEFAULT 0,
                messages_sent INTEGER DEFAULT 0,
                broadcasts_sent INTEGER DEFAULT 0
            )''')
            
            # Create indexes for performance
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversation_history(user_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversation_history(timestamp)")
            
            # Insert super admin
            c.execute("INSERT OR IGNORE INTO admins (user_id, username, added_by, permissions) VALUES (?, ?, ?, ?)",
                      (SUPER_ADMIN_ID, CREATOR_USERNAME, SUPER_ADMIN_ID, 'super'))
            c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, is_admin) VALUES (?, ?, ?, ?)",
                      (SUPER_ADMIN_ID, CREATOR_USERNAME, "Milan", 1))
            
            conn.commit()
    except sqlite3.Error as e:
        logger.exception(f"Database initialization failed: {e}")
        sys.exit(1)

# Initialize database
init_db()

# ===== Database Functions =====
def execute_db(query, params=(), fetch=False):
    """Safe database execution with error handling"""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            c = conn.cursor()
            c.execute(query, params)
            if fetch:
                return c.fetchall()
            conn.commit()
    except sqlite3.Error as e:
        logger.exception(f"Database error: {e}")
        return None

def backup_db():
    """Create timestamped database backup"""
    try:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"siya_backup_{timestamp}.db")
        with sqlite3.connect(DB_FILE) as src, sqlite3.connect(backup_file) as dest:
            src.backup(dest)
        logger.info(f"Database backup created: {backup_file}")
        return backup_file
    except Exception as e:
        logger.exception(f"Backup failed: {e}")
        return None

def add_user(user_id, username, first_name, last_name):
    """Add new user to database"""
    execute_db(
        """INSERT OR IGNORE INTO users 
        (user_id, username, first_name, last_name) 
        VALUES (?, ?, ?, ?)""",
        (user_id, username, first_name, last_name)
    )
    execute_db(
        """INSERT OR IGNORE INTO system_stats (date) VALUES (DATE('now'))"""
    )
    execute_db(
        """UPDATE system_stats SET new_users = new_users + 1 WHERE date = DATE('now')"""
    )

def update_user_activity(user_id):
    """Update user's last activity and message count"""
    execute_db(
        "UPDATE users SET last_active = CURRENT_TIMESTAMP, message_count = message_count + 1 WHERE user_id = ?",
        (user_id,)
    )
    execute_db(
        """INSERT OR IGNORE INTO system_stats (date) VALUES (DATE('now'))"""
    )
    execute_db(
        """UPDATE system_stats SET messages_sent = messages_sent + 1 WHERE date = DATE('now')"""
    )

def is_admin(user_id):
    """Check if user is admin"""
    result = execute_db(
        "SELECT 1 FROM admins WHERE user_id = ?", 
        (user_id,), 
        fetch=True
    )
    return result is not None and len(result) > 0

def is_banned(user_id):
    """Check if user is banned"""
    result = execute_db(
        "SELECT is_banned FROM users WHERE user_id = ?", 
        (user_id,), 
        fetch=True
    )
    return result and result[0][0] == 1

def add_admin(user_id, username, added_by):
    """Add new admin"""
    execute_db(
        "INSERT OR IGNORE INTO admins (user_id, username, added_by) VALUES (?, ?, ?)",
        (user_id, username, added_by)
    )
    execute_db(
        "UPDATE users SET is_admin = 1 WHERE user_id = ?",
        (user_id,)
    )

def remove_admin(user_id):
    """Remove admin (except super admin)"""
    execute_db(
        "DELETE FROM admins WHERE user_id = ? AND user_id != ?", 
        (user_id, SUPER_ADMIN_ID)
    )
    execute_db(
        "UPDATE users SET is_admin = 0 WHERE user_id = ?",
        (user_id,)
    )

def ban_user(user_id):
    """Ban user"""
    execute_db(
        "UPDATE users SET is_banned = 1 WHERE user_id = ?",
        (user_id,)
    )

def unban_user(user_id):
    """Unban user"""
    execute_db(
        "UPDATE users SET is_banned = 0 WHERE user_id = ?",
        (user_id,)
    )

def get_all_users():
    """Get all non-banned users"""
    result = execute_db(
        "SELECT user_id FROM users WHERE is_banned = 0",
        fetch=True
    )
    return [row[0] for row in result] if result else []

def get_user_info(user_id):
    """Get detailed user info"""
    result = execute_db(
        """SELECT user_id, username, first_name, last_name, 
        strftime('%Y-%m-%d %H:%M', created_at), 
        strftime('%Y-%m-%d %H:%M', last_active),
        message_count, is_banned, is_admin
        FROM users WHERE user_id = ?""", 
        (user_id,), 
        fetch=True
    )
    return result[0] if result else None

def get_all_user_info(limit=100):
    """Get all users' info with pagination"""
    result = execute_db(
        """SELECT user_id, username, first_name, 
        strftime('%Y-%m-%d', created_at), message_count, is_banned
        FROM users ORDER BY created_at DESC LIMIT ?""", 
        (limit,), 
        fetch=True
    )
    return result if result else []

def save_conversation(user_id, role, content, topic_id=0):
    """Save conversation to history with topic tracking"""
    execute_db(
        """INSERT INTO conversation_history 
        (user_id, role, content, topic_id) 
        VALUES (?, ?, ?, ?)""",
        (user_id, role, content, topic_id)
    )

def get_conversation_history(user_id, limit=CONVERSATION_MEMORY_LENGTH):
    """Get conversation history for user"""
    result = execute_db(
        """SELECT role, content, topic_id FROM conversation_history 
        WHERE user_id = ? 
        ORDER BY timestamp DESC 
        LIMIT ?""", 
        (user_id, limit), 
        fetch=True
    )
    return [{"role": role, "content": content, "topic_id": topic_id} for role, content, topic_id in result][::-1]

def clear_conversation_history(user_id):
    """Clear conversation history for user"""
    execute_db(
        "DELETE FROM conversation_history WHERE user_id = ?",
        (user_id,)
    )

def detect_current_topic(user_id):
    """Detect current conversation topic from history"""
    history = get_conversation_history(user_id, 5)
    if not history:
        return 0
    
    last_messages = " ".join([msg['content'] for msg in history])
    
    topics = {
        "romance": ["love", "pyaar", "miss", "kiss", "hug"],
        "anger": ["gussa", "naraz", "angry", "mad"],
        "help": ["help", "sahayata", "madad"],
        "general": ["hi", "hello", "how are you"]
    }
    
    for topic, keywords in topics.items():
        if any(keyword in last_messages.lower() for keyword in keywords):
            return hash(topic) % 1000000
    
    return hash("general") % 1000000

def log_broadcast(admin_id, message, total_users, success_count, failed_count, start, end):
    """Log broadcast results with timing"""
    execute_db(
        """INSERT INTO broadcast_log 
        (admin_id, message, total_users, success_count, failed_count, start_time, end_time) 
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (admin_id, message, total_users, success_count, failed_count, start, end)
    )
    execute_db(
        """INSERT OR IGNORE INTO system_stats (date) VALUES (DATE('now'))"""
    )
    execute_db(
        """UPDATE system_stats SET broadcasts_sent = broadcasts_sent + 1 WHERE date = DATE('now')"""
    )

def get_database_stats():
    """Get comprehensive system statistics from database"""
    stats = execute_db(
        """SELECT 
        SUM(new_users), 
        SUM(messages_sent),
        SUM(broadcasts_sent)
        FROM system_stats""",
        fetch=True
    )
    return stats[0] if stats else (0, 0, 0)

# ===== Rate Limiting System =====
class RateLimiter:
    def __init__(self):
        self.user_activity = {}
    
    def check_limit(self, user_id):
        """Check if user exceeds rate limit"""
        current_time = time.time()
        
        if user_id not in self.user_activity:
            self.user_activity[user_id] = {'count': 1, 'timestamp': current_time}
            return False
        
        # Reset counter if time window passed
        if current_time - self.user_activity[user_id]['timestamp'] > 60:
            self.user_activity[user_id] = {'count': 1, 'timestamp': current_time}
            return False
        
        # Increment count
        self.user_activity[user_id]['count'] += 1
        
        # Check if exceeds limit
        if self.user_activity[user_id]['count'] > RATE_LIMIT:
            if self.user_activity[user_id]['count'] > BAN_THRESHOLD:
                ban_user(user_id)
                return 'ban'
            return True
        
        return False

# Initialize rate limiter
rate_limiter = RateLimiter()

# ===== Flask Keep-Alive Server =====
def run_flask_app(port):
    """Run Flask server in separate thread"""
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "🚀 Siya Bot is running!", 200
    
    @app.route('/health')
    def health():
        return json.dumps({"status": "ok", "uptime": time.time() - START_TIME}), 200
    
    @app.route('/backup')
    def download_backup():
        backup_file = backup_db()
        if backup_file:
            return send_file(backup_file, as_attachment=True)
        return "Backup failed", 500
    
    app.run(host='0.0.0.0', port=port)

# ===== API Response Generator =====
class APIResponseGenerator:
    """Handles all API communications for dynamic responses"""
    def __init__(self):
        self.last_call = {}
        self.fail_count = {}
        self.api_endpoints = [AI_API_URL, FALLBACK_API_URL]
        self.current_endpoint = 0
    
    async def generate_response(self, user_id: int, message: str, context: dict = None) -> str:
        """Generate API-based response with conversation context"""
        try:
            # Get conversation history
            conversation = get_conversation_history(user_id)
            
            # Build system prompt
            system_prompt = self._build_system_prompt(user_id, context)
            
            # Update conversation with system prompt
            if not conversation or conversation[0]['role'] != 'system':
                conversation.insert(0, {"role": "system", "content": system_prompt})
            else:
                conversation[0]['content'] = system_prompt
            
            # Add user message
            conversation.append({"role": "user", "content": message})
            
            # Clean and truncate conversation if too long
            conversation = self._clean_conversation(conversation)
            
            # Calculate typing delay
            typing_delay = self._calculate_typing_delay(message)
            await asyncio.sleep(typing_delay)
            
            # Make API call with retry
            response = await self._call_api_with_retry(conversation, user_id)
            
            # Process response to remove any JSON/function calls
            response = self._process_response(response)
            
            # Save conversation to history
            topic_id = detect_current_topic(user_id)
            save_conversation(user_id, "user", message, topic_id)
            save_conversation(user_id, "assistant", response, topic_id)
            
            return response
        
        except Exception as e:
            logger.exception(f"API response error: {str(e)}")
            return await self._get_error_response(user_id)
    
    def _build_system_prompt(self, user_id: int, context: dict) -> str:
        """Build advanced system prompt based on context"""
        is_boyfriend = user_id == SUPER_ADMIN_ID
        current_time = datetime.datetime.now()
        hour = current_time.hour
        time_of_day = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 22 else "night"
        
        # Base prompt with context awareness
base_prompt = f"""  
You are {'an emotional, slightly dramatic angry girlfriend who also melts with love when treated nicely, and sometimes cares deeply like his mom' if is_boyfriend else 'a warm and caring friendly assistant'} named Siya.  
Current time: {time_of_day.capitalize()} ({current_time.strftime('%I:%M %p')})  
User ID: {user_id}  

Response Guidelines:  
1. Maintain {'a real girlfriend vibe – mix of love, frustration, and caring like a mom when needed' if is_boyfriend else 'a natural, friendly vibe'}  
2. If the user is being sweet/polite, soften tone and show hidden love (mix gussa + care + maa ka pyaar)  
3. Reply in 1-3 sentences but keep tone casual and human-like (not robotic)  
4. Use {'angry + emotional + caring emojis (😤🔥🤬😡💔🥺❤️🤗👩‍❤️‍👨)' if is_boyfriend else 'soft, caring emojis (💖✨🥰😊🤗)'}  
5. Add small emotional pauses (like "uhh", "hmm", "uff", "aww", "arey beta", "uff meri jaan") sometimes to sound real  
6. Never output JSON or function calls  
7. Speak in {'Hindi-English mix with emotional, loving, and motherly touch' if is_boyfriend else 'soft and simple English'}  
8. Occasionally express natural feelings (e.g., "uff tum bhi na", "aww kitne cute ho", "seriously?", "hmm okay but sun meri baat...")  
9. Balance anger, love, and care naturally – jaise ek real girlfriend ya maa karti hai, gussa kare but dil se protect kare.  
10. Sometimes scold in a loving way (e.g., "khud ka dhyan rakho na!", "kuch khaya ki nahi?", "uff tum bacho jaise ho").  
"""
        
        # Add specific context if available
        if context:
            if context.get('reply_to'):
                base_prompt += f"\nYou're replying to: '{context['reply_to']}'\n"
            if context.get('topic'):
                base_prompt += f"Current topic: {context['topic']}\n"
        
        return base_prompt.strip()
    
    def _clean_conversation(self, conversation: list) -> list:
        """Clean conversation history and ensure it's within token limits"""
        # Remove any empty or invalid messages
        cleaned = [msg for msg in conversation if msg.get('content') and msg.get('role')]
        
        # Truncate if too long
        if len(str(cleaned)) > MAX_HISTORY_TOKENS:
            # Keep system message and recent messages
            system_msg = cleaned[0] if cleaned and cleaned[0]['role'] == 'system' else None
            recent_msgs = cleaned[-(CONVERSATION_MEMORY_LENGTH-1):] if system_msg else cleaned[-CONVERSATION_MEMORY_LENGTH:]
            
            if system_msg:
                cleaned = [system_msg] + recent_msgs
            else:
                cleaned = recent_msgs
        
        return cleaned
    
    def _calculate_typing_delay(self, message: str) -> float:
        """Calculate realistic typing delay"""
        words = len(message.split())
        base_delay = min(4.0, max(0.7, words * 0.1))
        return base_delay * random.uniform(0.8, 1.2)
    
    def _process_response(self, response: str) -> str:
        """Process API response to remove any JSON/function calls"""
        # Remove any JSON-like content
        if response.startswith('{') and response.endswith('}'):
            try:
                data = json.loads(response)
                if 'choices' in data:
                    return data['choices'][0]['message']['content']
                return "I got a technical response. Can you ask me something else?"
            except json.JSONDecodeError:
                pass
        
        # Remove function call markers
        response = re.sub(r'\{.*?\}', '', response)
        response = re.sub(r'<\|.*?\|>', '', response)
        
        # Ensure response isn't empty
        if not response.strip():
            return "I didn't understand that. Can you rephrase?"
        
        return response.strip()
    
    async def _call_api_with_retry(self, conversation: list, user_id: int) -> str:
        """Call API with retry logic and endpoint rotation"""
        last_exception = None
        
        for attempt in range(API_MAX_RETRIES):
            try:
                return await self._call_api(conversation, user_id)
            except Exception as e:
                last_exception = e
                logger.warning(f"API attempt {attempt+1} failed: {str(e)}")
                
                # Rotate to next API endpoint if available
                self.current_endpoint = (self.current_endpoint + 1) % len(self.api_endpoints)
                logger.info(f"Rotating to API endpoint: {self.api_endpoints[self.current_endpoint]}")
                
                await asyncio.sleep(API_RETRY_DELAY * (attempt + 1))
        
        # If all retries fail
        raise Exception(f"API call failed after {API_MAX_RETRIES} attempts: {str(last_exception)}")
    
    async def _call_api(self, conversation: list, user_id: int) -> str:
        """Call external API for response"""
        # Rate limiting
        current_time = time.time()
        if user_id in self.last_call and current_time - self.last_call[user_id] < 1.2:
            await asyncio.sleep(1.5)
        
        headers = {
            "Authorization": f"Bearer {A4F_API_KEY}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": MODEL_NAME,
            "messages": conversation,
            "temperature": 1.2 if user_id == SUPER_ADMIN_ID else 0.7,
            "max_tokens": 300,
            "frequency_penalty": 0.75,
            "presence_penalty": 0.75,
            "top_p": 0.9
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_endpoints[self.current_endpoint],
                    headers=headers,
                    json=payload,
                    timeout=API_TIMEOUT
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"API returned {response.status}: {error_text}")
                        raise Exception(f"API returned status {response.status}")
                    
                    data = await response.json()
                    ai_response = data["choices"][0]["message"]["content"]
            
            self.last_call[user_id] = time.time()
            self.fail_count[user_id] = 0
            return ai_response
        except Exception as e:
            self.fail_count[user_id] = self.fail_count.get(user_id, 0) + 1
            logger.exception(f"API call failed: {str(e)}")
            raise
    
    async def _get_error_response(self, user_id: int) -> str:
        """Generate API error response"""
        is_boyfriend = user_id == SUPER_ADMIN_ID
        if is_boyfriend:
            return "Server issues... making me even angrier! 😤 Try again?"
        return "Oopsie~ technical glitch! 💫 Try again in a moment?"

# Initialize response generator
response_generator = APIResponseGenerator()

# ===== System Monitoring Functions =====
def get_performance_stats():
    """Get comprehensive performance statistics"""
    stats = {
        "uptime": int(time.time() - START_TIME),
        "cpu": psutil.cpu_percent(),
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage('/').percent,
        "process_mem": psutil.Process().memory_info().rss / (1024 * 1024)  # in MB
    }
    
    # Format uptime
    hours, remainder = divmod(stats["uptime"], 3600)
    minutes, seconds = divmod(remainder, 60)
    stats["uptime_str"] = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    
    # Get DB stats
    stats["db_size"] = os.path.getsize(DB_FILE) / (1024 * 1024)  # in MB
    
    # Get user stats
    stats["total_users"] = execute_db("SELECT COUNT(*) FROM users", fetch=True)[0][0] if execute_db("SELECT COUNT(*) FROM users", fetch=True) else 0
    stats["active_users"] = execute_db(
        "SELECT COUNT(*) FROM users WHERE last_active > datetime('now', '-1 day')",
        fetch=True
    )[0][0] if execute_db("SELECT COUNT(*) FROM users WHERE last_active > datetime('now', '-1 day')", fetch=True) else 0
    stats["banned_users"] = execute_db(
        "SELECT COUNT(*) FROM users WHERE is_banned = 1",
        fetch=True
    )[0][0] if execute_db("SELECT COUNT(*) FROM users WHERE is_banned = 1", fetch=True) else 0
    
    return stats

# ===== Inline Keyboard Builder =====
def build_admin_keyboard():
    """Build dynamic admin inline keyboard"""
    keyboard = [
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [
            InlineKeyboardButton("👥 User List", callback_data="admin_userlist"),
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats")
        ],
        [
            InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban"),
            InlineKeyboardButton("✅ Unban User", callback_data="admin_unban")
        ],
        [InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_refresh")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===== Bot Command Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    
    if is_banned(user.id):
        logger.warning(f"Blocked banned user: {user.id}")
        return
    
    # Clear any old conversation history
    clear_conversation_history(user.id)
    
    # Generate welcome message
    welcome_msg = (
        f"👋 Hello {user.first_name}! I'm Siya. "
        f"{'Why did you make me wait so long? 😤' if user.id == SUPER_ADMIN_ID else 'How can I help you today? 💖'}"
    )
    
    await update.message.reply_text(welcome_msg)
    
    # Send user info to all admins
    await _notify_admins_new_user(update, context, user)

async def _notify_admins_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    """Notify all admins about new user"""
    admins = execute_db("SELECT user_id FROM admins", fetch=True)
    if not admins:
        return
    
    user_info = (
        f"👤 *New User Started*\n"
        f"🆔 `{user.id}`\n"
        f"👤 {user.first_name} {user.last_name or ''}\n"
        f"🔗 @{user.username or 'N/A'}\n"
        f"🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👁 View Profile", callback_data=f"view_user_{user.id}"),
        InlineKeyboardButton("🚫 Ban", callback_data=f"ban_user_{user.id}")
    ]])
    
    for admin_id, in admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=user_info,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.exception(f"Failed to notify admin {admin_id}: {str(e)}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send admin panel inline keyboard"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You don't have admin privileges!")
        return
    
    await update.message.reply_text(
        "⚡️ *Admin Control Panel* ⚡️\nSelect an action:",
        parse_mode="Markdown",
        reply_markup=build_admin_keyboard()
    )

async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin inline keyboard button presses"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("🚫 Access Denied")
        return
    
    action = query.data
    
    if action == "admin_refresh":
        await query.edit_message_reply_markup(reply_markup=build_admin_keyboard())
        return
    
    if action == "admin_broadcast":
        await query.edit_message_text("📝 Enter broadcast message:")
        context.user_data['awaiting_broadcast'] = True
        return
    
    if action == "admin_userlist":
        users = get_all_user_info(50)
        if not users:
            await query.edit_message_text("No users found")
            return
        
        user_list = "👥 *Registered Users (Last 50):*\n\n"
        for idx, (uid, username, fname, join_date, msg_count, banned) in enumerate(users, 1):
            user_list += (
                f"{idx}. `{uid}` - {fname}\n"
                f"   👤 @{username or 'N/A'} | 📅 {join_date}\n"
                f"   💬 {msg_count} msgs | {'🚫 Banned' if banned else '✅ Active'}\n\n"
            )
        
        await query.edit_message_text(
            user_list,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
            ])
        )
        return
    
    if action == "admin_stats":
        stats = get_performance_stats()
        
        stats_text = (
            f"📊 *System Statistics*\n\n"
            f"⏱ Uptime: {stats['uptime_str']}\n"
            f"👥 Users: {stats['total_users']} | Active: {stats['active_users']} | Banned: {stats['banned_users']}\n"
            f"💾 Memory: {stats['memory']}% | Process: {stats['process_mem']:.2f}MB\n"
            f"🖥 CPU: {stats['cpu']}% | 💽 Disk: {stats['disk']}%\n"
            f"🗃 DB Size: {stats['db_size']:.2f}MB"
        )
        
        await query.edit_message_text(
            stats_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
            ])
        )
        return
    
    if action.startswith("ban_user_"):
        target_id = int(action.split("_")[2])
        ban_user(target_id)
        await query.edit_message_text(f"✅ User `{target_id}` banned", parse_mode="Markdown")
        return
    
    if action.startswith("unban_user_"):
        target_id = int(action.split("_")[2])
        unban_user(target_id)
        await query.edit_message_text(f"✅ User `{target_id}` unbanned", parse_mode="Markdown")
        return
    
    if action == "admin_back":
        await query.edit_message_text(
            "⚡️ *Admin Control Panel* ⚡️\nSelect an action:",
            parse_mode="Markdown",
            reply_markup=build_admin_keyboard()
        )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle broadcast command"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You don't have permission to broadcast!")
        return
    
    if not context.args:
        await update.message.reply_text("Please provide a message to broadcast")
        return
    
    message = " ".join(context.args)
    users = get_all_users()
    total_users = len(users)
    
    # Send preview
    preview = f"📢 *Broadcast Preview* 📢\n\n{message}\n\nSend to {total_users} users?"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast:{message}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")]
    ])
    
    await update.message.reply_text(
        preview, 
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def handle_broadcast_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle broadcast confirmation"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("🚫 Access Denied")
        return
    
    if query.data == "cancel_broadcast":
        await query.edit_message_text("Broadcast canceled")
        return
    
    # Extract message from callback data
    message = query.data.split(":", 1)[1]
    users = get_all_users()
    total_users = len(users)
    batches = math.ceil(total_users / BROADCAST_BATCH_SIZE)
    
    await query.edit_message_text(f"📤 Starting broadcast to {total_users} users in {batches} batches...")
    
    # Broadcast in batches
    start_time = time.time()
    success_count = 0
    failed_count = 0
    
    for i in range(0, total_users, BROADCAST_BATCH_SIZE):
        batch = users[i:i+BROADCAST_BATCH_SIZE]
        batch_success = 0
        batch_failed = 0
        
        for user_id in batch:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message
                )
                batch_success += 1
                success_count += 1
            except Exception as e:
                logger.exception(f"Broadcast failed for {user_id}: {str(e)}")
                batch_failed += 1
                failed_count += 1
            await asyncio.sleep(0.05)  # Small delay between users
        
        # Update progress
        progress = (i + BROADCAST_BATCH_SIZE) / total_users * 100
        await query.edit_message_text(
            f"📤 Broadcasting...\n"
            f"✅ {success_count} | ❌ {failed_count}\n"
            f"📊 {min(100, progress):.1f}% complete"
        )
        await asyncio.sleep(BROADCAST_DELAY)
    
    # Final results
    end_time = time.time()
    duration = end_time - start_time
    log_broadcast(user_id, message, total_users, success_count, failed_count, start_time, end_time)
    
    result_text = (
        f"📢 *Broadcast Complete* 📢\n\n"
        f"• Total Users: {total_users}\n"
        f"• ✅ Success: {success_count}\n"
        f"• ❌ Failed: {failed_count}\n"
        f"• ⏱ Duration: {duration:.1f} seconds\n"
        f"• 🚀 Speed: {total_users/duration:.1f} users/sec"
    )
    
    await query.edit_message_text(
        result_text,
        parse_mode="Markdown"
    )

async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /user command (admin only)"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You don't have admin privileges!")
        return
    
    users = get_all_user_info(50)
    if not users:
        await update.message.reply_text("No users found")
        return
    
    user_list = "👥 *Registered Users (Last 50):*\n\n"
    for idx, (uid, username, fname, join_date, msg_count, banned) in enumerate(users, 1):
        user_list += (
            f"{idx}. `{uid}` - {fname}\n"
            f"   👤 @{username or 'N/A'} | 📅 {join_date}\n"
            f"   💬 {msg_count} msgs | {'🚫 Banned' if banned else '✅ Active'}\n\n"
        )
    
    await update.message.reply_text(
        user_list,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="admin_userlist")]
        ])
    )

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /userinfo command (admin only)"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You don't have admin privileges!")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide user ID")
        return
    
    target_id = int(context.args[0])
    user_info = get_user_info(target_id)
    
    if not user_info:
        await update.message.reply_text("User not found")
        return
    
    uid, username, fname, lname, created, last_active, msg_count, banned, is_admin_flag = user_info
    info_text = (
        f"👤 *User Information*\n\n"
        f"🆔 `{uid}`\n"
        f"👤 {fname} {lname or ''}\n"
        f"🔗 @{username or 'N/A'}\n"
        f"📅 Created: {created}\n"
        f"🕒 Last Active: {last_active}\n"
        f"💬 Messages: {msg_count}\n"
        f"🚫 Banned: {'Yes' if banned else 'No'}\n"
        f"👑 Admin: {'Yes' if is_admin_flag else 'No'}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Ban", callback_data=f"ban_user_{uid}"),
            InlineKeyboardButton("✅ Unban", callback_data=f"unban_user_{uid}")
        ],
        [
            InlineKeyboardButton("👑 Make Admin", callback_data=f"make_admin_{uid}"),
            InlineKeyboardButton("👥 Remove Admin", callback_data=f"remove_admin_{uid}")
        ]
    ])
    
    await update.message.reply_text(
        info_text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command (admin only)"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("🚫 You don't have admin privileges!")
        return
    
    db_stats = get_database_stats()
    perf_stats = get_performance_stats()
    
    stats_text = (
        f"📊 *System Statistics*\n\n"
        f"⏱ Uptime: {perf_stats['uptime_str']}\n"
        f"👥 Total Users: {perf_stats['total_users']}\n"
        f"🚀 Active Users (24h): {perf_stats['active_users']}\n"
        f"🚫 Banned Users: {perf_stats['banned_users']}\n"
        f"💬 Messages Sent: {db_stats[1]}\n"
        f"📢 Broadcasts Sent: {db_stats[2]}\n\n"
        f"💾 Memory Usage: {perf_stats['memory']}%\n"
        f"🖥 CPU Usage: {perf_stats['cpu']}%\n"
        f"💽 Disk Usage: {perf_stats['disk']}%\n"
        f"🧠 Process Memory: {perf_stats['process_mem']:.2f}MB\n"
        f"🗃 Database Size: {perf_stats['db_size']:.2f}MB"
    )
    
    await update.message.reply_text(
        stats_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Full Analytics", callback_data="admin_stats")]
        ])
    )

async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /uptime command"""
    stats = get_performance_stats()
    
    uptime_text = (
        f"⏱ *System Uptime*: {stats['uptime_str']}\n"
        f"🖥 *CPU Usage*: {stats['cpu']}%\n"
        f"🧠 *Memory Usage*: {stats['memory']}%\n"
        f"💽 *Disk Usage*: {stats['disk']}%\n"
        f"🧩 *Process Memory*: {stats['process_mem']:.2f}MB\n"
        f"🗃 *DB Size*: {stats['db_size']:.2f}MB"
    )
    
    await update.message.reply_text(uptime_text, parse_mode="Markdown")

async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cmds command to list available commands"""
    user = update.effective_user
    is_admin_user = is_admin(user.id)
    
    cmds_text = "🤖 *Available Commands*\n\n"
    cmds_text += "• /start - Start interacting with the bot\n"
    cmds_text += "• /cmds - Show this commands list\n"
    cmds_text += "• /uptime - Show bot and system status\n"
    
    if is_admin_user:
        cmds_text += "\n🔒 *Admin Commands*\n"
        cmds_text += "• /admin - Open admin control panel\n"
        cmds_text += "• /broadcast - Send message to all users\n"
        cmds_text += "• /user - List registered users\n"
        cmds_text += "• /userinfo <id> - Get user information\n"
        cmds_text += "• /stats - Show bot analytics\n"
        cmds_text += "• /exportdb - Export database (super admin)\n"
    
    await update.message.reply_text(cmds_text, parse_mode="Markdown")

async def exportdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /exportdb command (super admin only)"""
    user = update.effective_user
    if user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("🚫 Only the super admin can export the database!")
        return
    
    backup_file = backup_db()
    if not backup_file:
        await update.message.reply_text("Backup failed")
        return
    
    await update.message.reply_document(
        document=InputFile(backup_file),
        filename=os.path.basename(backup_file)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all incoming messages"""
    user = update.effective_user
    message = update.message.text
    
    # Add user to database
    add_user(user.id, user.username, user.first_name, user.last_name)
    update_user_activity(user.id)
    
    # Check if user is banned
    if is_banned(user.id):
        logger.warning(f"Blocked banned user: {user.id}")
        return
    
    # Rate limiting check
    rate_result = rate_limiter.check_limit(user.id)
    if rate_result == 'ban':
        ban_user(user.id)
        await _notify_admins_spam(user.id, context)
        return
    elif rate_result:
        await update.message.reply_text("⏳ Please slow down, you're sending messages too fast!")
        return
    
    # Determine context for reply-to-reply
    context_data = {}
    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message.text or ""
        context_data['reply_to'] = replied_msg[:150]
        context_data['topic'] = detect_current_topic(user.id)
    
    # Show typing action
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )
    
    # Generate and send API-based response
    response = await response_generator.generate_response(user.id, message, context_data)
    await update.message.reply_text(response)

async def _notify_admins_spam(user_id, context):
    """Notify admins about spam user"""
    admins = execute_db("SELECT user_id FROM admins", fetch=True)
    if not admins:
        return
    
    user_info = get_user_info(user_id)
    if not user_info:
        return
    
    uid, username, fname, lname, created, last_active, msg_count, banned, is_admin = user_info
    alert_text = (
        f"🚨 *SPAM ALERT* 🚨\n\n"
        f"🆔 `{uid}`\n"
        f"👤 {fname} {lname or ''}\n"
        f"🔗 @{username or 'N/A'}\n\n"
        f"⚠️ User was automatically banned for spamming"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Unban", callback_data=f"unban_user_{uid}"),
        InlineKeyboardButton("👁 View Info", callback_data=f"view_user_{uid}")
    ]])
    
    for admin_id, in admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=alert_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.exception(f"Failed to notify admin {admin_id}: {str(e)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler with admin notification"""
    logger.error("Exception while handling update:", exc_info=context.error)
    
    # Notify super admin
    error_msg = (
        f"🚨 *BOT ERROR* 🚨\n\n"
        f"```\n{context.error}\n```\n"
        f"Update: `{update}`"
    )
    
    try:
        await context.bot.send_message(
            chat_id=SUPER_ADMIN_ID,
            text=error_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception(f"Failed to notify super admin: {str(e)}")
    
    # Respond to user
    if update and update.effective_user:
        try:
            await update.message.reply_text("Sorry, I encountered an error. Please try again.")
        except:
            logger.exception("Failed to send error response")

# ===== Cleanup Task =====
async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """Regular maintenance tasks"""
    logger.info("Running cleanup tasks...")
    
    # Clean old conversation history
    execute_db(
        "DELETE FROM conversation_history WHERE timestamp < datetime('now', '-7 days')"
    )
    
    # Create daily backup
    backup_db()
    
    # Update active users stats
    count_result = execute_db(
        "SELECT COUNT(*) FROM users WHERE last_active > datetime('now', '-1 day')",
        fetch=True
    )
    if count_result:
        count = count_result[0][0]
        execute_db(
            """INSERT OR IGNORE INTO system_stats (date) VALUES (DATE('now'))"""
        )
        execute_db(
            "UPDATE system_stats SET active_users = ? WHERE date = DATE('now')",
            (count,)
        )

# ===== Main Application =====
def main() -> None:
    """Start the bot with all handlers"""
    # Create application with concurrent updates enabled
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    # Initialize and attach job queue properly
    job_queue = application.job_queue
    
    # Add command handlers
    command_handlers = [
        CommandHandler("start", start),
        CommandHandler("cmds", cmds_command),
        CommandHandler("uptime", uptime_command),
        CommandHandler("admin", admin_panel),
        CommandHandler("broadcast", broadcast_command),
        CommandHandler("user", user_command),
        CommandHandler("userinfo", userinfo_command),
        CommandHandler("stats", stats_command),
        CommandHandler("exportdb", exportdb_command),
    ]
    
    for handler in command_handlers:
        application.add_handler(handler)
    
    # Add callback handlers
    application.add_handler(CallbackQueryHandler(admin_button_handler, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(handle_broadcast_confirmation, pattern="^confirm_broadcast|^cancel_broadcast"))
    application.add_handler(CallbackQueryHandler(admin_button_handler, pattern="^ban_user_|^unban_user_|^make_admin_|^remove_admin_|^view_user_"))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Schedule cleanup task
    job_queue.run_repeating(cleanup_task, interval=MAINTENANCE_INTERVAL, first=10)
    
    # Start Flask in a separate thread with dynamic port
    port = PORT
    while True:
        try:
            # Test if port is available
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
            break
        except OSError:
            port += 1
    
    flask_thread = threading.Thread(target=run_flask_app, args=(port,), daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Flask keep-alive running on port {port}")
    
    # Start bot
    logger.info("🚀 Starting Siya Bot (Fixed Version)...")
    logger.info(f"👑 Super Admin: {SUPER_ADMIN_ID}")
    logger.info(f"💾 Database: {DB_FILE}")
    logger.info(f"📁 Backups: {BACKUP_DIR}")
    
    # Remove any existing webhook and start polling
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
