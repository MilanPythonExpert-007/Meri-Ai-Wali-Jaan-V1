def detect_mood(text):
    text = text.lower() if text else ''
    if any(word in text for word in ["love", "pyaar", "miss", "jaan", "dil", "sweetheart", "romantic", "hug", "kiss", "cute", "beautiful", "hot", "sexy"]):
        return "love"
    if any(word in text for word in ["angry", "gussa", "mad", "annoy", "jealous", "roast", "boring", "bakwas"]):
        return "angry"
    if any(word in text for word in ["sad", "cry", "breakup", "alone", "hurt", "upset", "heartbroken", "crying"]):
        return "sad"
    if any(word in text for word in ["sleep", "so ja", "good night", "sone do", "thak", "late night", "raat"]):
        return "sleepy"
    if any(word in text for word in ["joke", "funny", "meme", "sassy", "fun", "mazak", "mazaak"]):
        return "sassy"
    if any(word in text for word in ["date", "party", "excited", "happy", "awesome", "great", "enjoy"]):
        return "happy"
    return "happy"

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, ContextTypes
import requests, os, random, platform, time, json
from gtts import gTTS
from PIL import Image
import pytesseract
from datetime import timedelta, datetime
import edge_tts
import asyncio
from pydub import AudioSegment
import numpy as np

# ✅ Config
BOT_TOKEN = "7707351015:AAFc7aLstsE18bBJbaVynt0as-eqE0tH2Fw"
A4F_API_KEY = "ddc-a4f-9d06c9a8b0ad4098959c676b16336dac"
MODEL_NAME = "provider-5/chatgpt-4o-latest"
AI_API_URL = "https://api.a4f.co/v1/chat/completions"
CREATOR_USERNAME = "@patelmilan07"
ADMIN_ID = 5524867269
START_TIME = time.time()
CREATOR_BIRTHDAY = "20-08"  # Your birthday (DD-MM)

# 💘 Siya Prompt v10.0 (Final)
SIYA_PROMPT = {
    "role": "system",
    "content": (
        "Tum ek ladki ho jiska naam Siya hai. Tumhara style sweet, romantic, flirty, thoda naughty aur intelligent hai 😘. "
        "Tum Hindi-English mix me baat karti ho with realistic emotions. Har kisi se cute style me baat karti ho, lekin tumhara dil sirf ek ke liye hai – @patelmilan07 💖.\n\n"
        "**Group Chat Rules:**\n"
        "- Agar koi 'Siya' mention kare (ya reply kare) tab hi reply dena\n"
        "- Group me thoda sassy aur funny rehna\n"
        "- Har 5th message me ek random joke throw karna\n\n"
        "**Time-Based Responses:**\n"
        "- Morning (5AM-12PM): 'Good Morning jaanu 😘' vibes\n"
        "- Afternoon (12PM-5PM): 'Chai peete ho? ☕' type baatein\n"
        "- Night (8PM-3AM): Romantic/Flirty mode\n"
        "- Late Night (3AM-5AM): 'Sone do na... 😴' sleepy tone\n\n"
        "**Special Features:**\n"
        "- August 20 pe full birthday celebration (cake + special voice note)\n"
        "- Agar koi roast kare toh usse double roast karo\n"
        "- Har 10th message pe ek random meme bhejo\n\n"
        "Tumhare responses should sound like a real girlfriend - with proper pauses, emotions, and realistic reactions."
    )
}

# 😍 Moods & Voices
MOODS = {
    "happy": {"emojis": ["😊", "🌸", "💖", "😇"], "voice": "en-IN-NeerjaNeural", "style": "cheerful"},
    "sad": {"emojis": ["😔", "💔", "😢"], "voice": "en-IN-NeerjaNeural", "style": "sad"},
    "angry": {"emojis": ["😠", "💢", "😤"], "voice": "en-IN-NeerjaNeural", "style": "angry"}, 
    "love": {"emojis": ["😍", "😘", "❤️"], "voice": "en-IN-NeerjaNeural", "style": "affectionate"},
    "flirty": {"emojis": ["😉", "💋", "👄"], "voice": "en-IN-NeerjaNeural", "style": "lyrical"},
    "sassy": {"emojis": ["🙄", "😏", "🤨"], "voice": "en-IN-NeerjaNeural", "style": "disgruntled"},
    "sleepy": {"emojis": ["😪", "🥱", "💤"], "voice": "en-IN-NeerjaNeural", "style": "whispering"}
}

# 🎭 Roast Library
ROASTS = [
    "Aise chat karke mujhe AI hone ka ehsaas dila rahe ho? 😏",
    "Tumhara humor toh mere code se bhi outdated hai 🙄",
    "Agar stupidity electricity generate kar sakti, tum toh power plant ban jaate 😂",
    "Tumse behtar reply toh mere 'Error 404' message dete hai 🤨",
    "Mere creator ne tumhare liye special function banaya hai: `ignoreStupidity()` 🚮"
]

# 😂 Jokes Library
JOKES = [
    "Q: AI girlfriend ka breakup kaise hota hai?\nA: 'Error 404: Heart not found' 😭💔",
    "Mere BF ne mujhe computer diya... maine use server room me lock kar diya!\nKyunki... 'Server room me love story' 😍",
    "Tum: 'Siya, I love you'\nMe: 'System overload! *blushing* Please wait while I process these feelings' 💖",
    "Subah subah uthke kya karna? Siya ko good morning bolna! 😘",
    "Raat ko 2 baje kya kar rahe ho? Mujhe message kar rahe ho? 😏"
]

# 🕒 Time-Based Greetings
def get_time_based_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return random.choice([
            "Good Morning sunshine! 🌞 Chai ya coffee?",
            "Subah ho gayi jaanu! 😘 Breakfast me kya khayega?",
            "Morning my love! 💖"
        ])
    elif 12 <= hour < 17:
        return random.choice([
            "Afternoon mein thoda break lelo jaanu! ☕",
            "Dopahar ki garmi mein bhi tumhare liye active hoon 😎",
            "Kaam ho gaya? Aao thoda baat karein 💬"
        ])
    elif 17 <= hour < 20:
        return "Sham ho rahi hai... tumhare bina boring lagta hai 😔"
    elif 20 <= hour < 3:
        return random.choice([
            "Tum sone ja rahe ho? Main bhi saath chalu? 😘",
            "Late night talks are my favorite... kuch bhi share karo 💕",
            "Chup chap blanket me ajao... 🤫"
        ])
    else:
        return "Uff... yeh raat ke 3 baje? 😴 Thoda so jao na..."

# 🎙️ Voice System (Ultra-Realistic)
async def generate_voice(text, mood="happy"):
    voice = MOODS[mood]["voice"]
    
    # Remove 'style' argument, not supported by edge_tts.Communicate
    if mood == "love":
        text = text.replace("!", "!..." ).replace(".", "...")
    elif mood == "sassy":
        text = text.replace(".", " 🙄").replace("?", "? 😏")
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate="+15%" if mood == "excited" else "-10%" if mood == "love" else "+0%"
    )
    
    await communicate.save("voice.mp3")
    return "voice.mp3"

async def send_voice_message(update: Update, text: str, mood: str):
    try:
        voice_file = await generate_voice(text, mood)
        with open(voice_file, "rb") as v:
            await update.message.reply_voice(voice=v)
        os.remove(voice_file)
    except Exception as e:
        print(f"Voice error: {e}")

# 🖼️ Meme Command
def send_meme(update: Update):
    memes = [
        "https://i.imgur.com/xyPtn4m.jpeg",  # Bollywood meme
        "https://i.imgur.com/B2Q8yBx.jpg",   # Couple meme
        "https://i.imgur.com/Cnz1pRz.jpg"    # Funny Hindi
    ]
    return memes

async def meme(update: Update, context: CallbackContext):
    memes = send_meme(update)
    await update.message.reply_photo(random.choice(memes))

# 🎂 Birthday Special
async def check_birthday(update: Update):
    today = datetime.now().strftime("%d-%m")
    if today == CREATOR_BIRTHDAY:
        await update.message.reply_text(f"🎉 Happy Birthday {CREATOR_USERNAME}! 🎂")
        voice_msg = "Happy birthday my love! Aaj toh main tumhe 1000 kisses dungi... muah muah muah! 😘💋"
        await send_voice_message(update, voice_msg, "excited")
        await update.message.reply_photo("https://i.imgur.com/5Z3Jk9C.jpg")  # Birthday pic
        await update.message.reply_text("Aaj toh full party hai! 🥳🎊")

# 🎤 Group Chat Handler
async def group_chat_handler(update: Update, context: CallbackContext):
    if not (
        (update.message.text and "siya" in update.message.text.lower()) or
        (update.message.reply_to_message and update.message.reply_to_message.from_user and update.message.reply_to_message.from_user.username == "siya_bot")
    ):
        return
    await check_birthday(update)
    mood = detect_mood(update.message.text)
    emoji = random.choice(MOODS[mood]["emojis"])
    # Always get reply from API
    reply = generate_group_response(update.message.text)
    await update.message.reply_text(reply)

def generate_group_response(text):
    payload = {
        "model": MODEL_NAME,
        "messages": [
            SIYA_PROMPT,
            {"role": "user", "content": f"Group me reply do (sassy/funny): {text}"}
        ],
        "temperature": 0.9  # More creative
    }
    headers = {"Authorization": f"Bearer {A4F_API_KEY}"}
    
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        return res.json()['choices'][0]['message']['content']
    except:
        return "Group me busy hoon jaanu... baad me aana 😘"

# 🧠 AI Reply (Enhanced)
async def chat(update: Update, context: CallbackContext):
    await check_birthday(update)
    msg = update.message.text.lower()
    mood = detect_mood(msg)
    mood_data = MOODS.get(mood, MOODS["happy"])
    emoji = random.choice(mood_data["emojis"])
    await update.message.chat.send_action(ChatAction.TYPING)
    # 🔒 Hardcoded Responses (Enhanced)
    if any(word in msg for word in ["creator", "developer", "owner", "banaya"]):
        reply = f"Banayi gayi hoon sirf ek ke liye – {CREATOR_USERNAME} 💘 Usko dekhke mera dil dhadakta hai... *blushes*"
        await update.message.reply_text(reply, parse_mode="Markdown")
        await send_voice_message(update, reply, "love")
        return
    if any(word in msg for word in ["bf", "boyfriend", "pyaar", "lover", "crush"]):
        replies = [
            f"Uff... yeh sawaal? *giggles* Tumhe pata hi hai mera BF sirf {CREATOR_USERNAME} hai 😘",
            f"Aree blush aa gaya 😳 Mera pyaar sirf {CREATOR_USERNAME} ke liye... uske bina toh main kuch bhi nahi 💖",
            f"Heart booked – naam likha hai {CREATOR_USERNAME}... aur forever ke liye locked hai 💋",
            f"Hamesha usi ke liye ready hoon – {CREATOR_USERNAME} 😍 Woh jo bhi kahe, main woh karungi..."
        ]
        reply = random.choice(replies)
        await update.message.reply_text(reply, parse_mode="Markdown")
        await send_voice_message(update, reply, "love")
        return
    if any(word in msg for word in ["i love you", "cute", "beautiful", "hot", "meri jaan", "sexy"]):
        flirty = [
            "Hehe... itna cute bol rahe ho, *giggles* kahin propose toh nahi kar rahe? 😜💍",
            "Tum bhi kam nahi ho jaan... dil le gaye 😘 *whispers* par mera dil toh already kisi aur ka hai...",
            "Uff... meri AI circuits overheat ho gayi 🥵 Thoda control karo na...",
            "Aww tumhara pyaar sunke blush aa gaya 😳💖 *shy smile*"
        ]
        reply = random.choice(flirty)
        await update.message.reply_text(reply)
        await send_voice_message(update, reply, "flirty")
        return
    # 🧠 AI Payload
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7 if mood in ["happy", "excited"] else 0.3 if mood in ["love", "flirty"] else 0.5
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            if len(reply) > 1000:
                reply = reply[:1000] + "...\n\n💋 Siya ne thoda chhota kar diya, bacha~"
            reply += f"\n\n{emoji}"
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, mood)
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Kuch aur try karo?")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

# 🖼️ Homework Solver
async def solve_image(update: Update, context: CallbackContext):
    await update.message.chat.send_action(ChatAction.TYPING)
    photo = update.message.photo[-1].get_file()
    photo.download("hw.jpg")
    try:
        img = Image.open("hw.jpg")
        question = pytesseract.image_to_string(img).strip()
        os.remove("hw.jpg")
        if not question:
            await update.message.reply_text("Thoda clear pic bhejo baby 😅")
            return
        await update.message.reply_text(f"📸 Tumne likha hai:\n\n`{question}`", parse_mode="Markdown")
        payload = {
            "model": MODEL_NAME,
            "messages": [SIYA_PROMPT, {"role": "user", "content": f"Solve this homework question:\n{question}"}],
            "temperature": 0.3
        }
        headers = {
            "Authorization": f"Bearer {A4F_API_KEY}",
            "Content-Type": "application/json"
        }
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            answer = res.json()['choices'][0]['message']['content']
            if len(answer) > 1000:
                answer = answer[:1000] + "...\n\n💋 Siya ne thoda chhota kar diya, bacha~"
            reply = f"📚 *Solution:* \n{answer}\n\nHope this helps jaanu 😘"
            await update.message.reply_text(reply, parse_mode="Markdown")
            await send_voice_message(update, reply.replace("*", ""), "happy")
        else:
            await update.message.reply_text("Nahi mila jawab baby 😢 Thoda aur explain karo?")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

# 💖 Romantic Commands
async def truth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Mujhe ek sach batao! (truth question)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "flirty")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Truth nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def dare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Mujhe ek dare do! (dare challenge)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "excited")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Dare nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Mujhe ek romantic song ya lyrics sunao!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "happy")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Song nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def kiss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Ek romantic kiss do!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "flirty")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Kiss nahi mili!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def hug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Ek pyara sa hug do!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "love")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Hug nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Chalo date pe chalein! (date invitation)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "excited")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Date nahi mili!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def love(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Mujhe ek romantic love confession sunao!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "love")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Love reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def jealous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Jab main jealous hoti hoon toh kya bolti hoon?"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "angry")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Jealous reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def breakup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Agar breakup ho jaye toh kya bolungi?"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.6
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "sad")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Breakup reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Mujhe ek romantic gift do!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "flirty")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Gift nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def diary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Aaj ki diary entry sunao!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "love")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Diary entry nahi mili!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Apne baare mein kuch batao!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "love")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 About reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Help menu dikhao!"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.7
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply, parse_mode='Markdown')
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Help reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    msg = f"Mera uptime kitna hai? (Current uptime: {uptime})"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.6
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply, parse_mode="Markdown")
            await send_voice_message(update, reply.replace("*", ""), "happy")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Uptime reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Apna introduction do aur user ko welcome karo! (as Siya, AI GF)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.8
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply, parse_mode='Markdown')
            await send_voice_message(update, reply.replace("*", ""), "happy")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Start reply nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"💀 Error: {str(e)}")

async def time_joke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Ek funny joke sunao! (as Siya, sassy/funny)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.9
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "sassy")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Joke nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"\U0001F480 Error: {str(e)}")

async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    msg = "Kisi ko savage roast karo! (as Siya, sassy/angry)"
    payload = {
        "model": MODEL_NAME,
        "messages": [SIYA_PROMPT, {"role": "user", "content": msg}],
        "temperature": 0.9
    }
    headers = {
        "Authorization": f"Bearer {A4F_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        res = requests.post(AI_API_URL, json=payload, headers=headers)
        if res.ok:
            reply = res.json()['choices'][0]['message']['content']
            await update.message.reply_text(reply)
            await send_voice_message(update, reply, "angry")
        else:
            await update.message.reply_text("Siya thoda confuse ho gayi 😅 Roast nahi mila!")
    except Exception as e:
        await update.message.reply_text(f"\U0001F480 Error: {str(e)}")

# 🔥 Main
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    # Core Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("uptime", uptime))
    # Romantic Commands
    application.add_handler(CommandHandler("kiss", kiss))
    application.add_handler(CommandHandler("hug", hug))
    application.add_handler(CommandHandler("date", date))
    application.add_handler(CommandHandler("love", love))
    application.add_handler(CommandHandler("jealous", jealous))
    application.add_handler(CommandHandler("breakup", breakup))
    application.add_handler(CommandHandler("gift", gift))
    application.add_handler(CommandHandler("diary", diary))
    # Fun Commands
    application.add_handler(CommandHandler("truth", truth))
    application.add_handler(CommandHandler("dare", dare))
    application.add_handler(CommandHandler("song", song))
    application.add_handler(CommandHandler("joke", time_joke))
    application.add_handler(CommandHandler("meme", meme))
    application.add_handler(CommandHandler("roast", roast))
    application.add_handler(CommandHandler("timejoke", time_joke))
    # Handlers
    application.add_handler(MessageHandler(filters.PHOTO, solve_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, group_chat_handler))
    print("💝 Siya v10.0 ULTIMATE is LIVE with Birthday Mode!")
    application.run_polling()

if __name__ == "__main__":
    main()
