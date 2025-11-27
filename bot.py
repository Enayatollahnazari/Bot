import os
import asyncio
import re
import json
import sqlite3
import base64
from typing import Dict, List, Optional
from pyrogram import Client, filters
from pyrogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneNumberInvalid, 
    PhoneCodeExpired, ApiIdInvalid
)
from pytgcalls import PyTgCalls
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped, AudioVideoPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio, HighQualityVideo

# ==================== تنظیمات ====================
API_ID = int(os.environ.get("API_ID", 23726943))
API_HASH = os.environ.get("API_HASH", "1dcb583a80fe61341fd3c2e25b313d61")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8545444149:AAGfQS-tDBIHSPRXKT6LfmIrv3Llv8Ohamc")
OWNER_ID = int(os.environ.get("OWNER_ID", 7542685645))

# تنظیم مسیر دیتابیس برای Railway
DB_PATH = "/tmp/sessions.db" if "RAILWAY_ENVIRONMENT" in os.environ else "sessions.db"

# ==================== مدیریت دیتابیس سشن‌ها ====================
class SessionStorage:
    def __init__(self):
        self.db_path = DB_PATH
        self.init_database()
    
    def init_database(self):
        """ایجاد جدول سشن‌ها در دیتابیس"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY,
                session_string TEXT NOT NULL,
                phone_number TEXT,
                first_name TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    
    def save_session(self, name: str, session_string: str, phone_number: str = "", first_name: str = "", username: str = ""):
        """ذخیره سشن در دیتابیس"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT OR REPLACE INTO sessions (name, session_string, phone_number, first_name, username) VALUES (?, ?, ?, ?, ?)',
            (name, session_string, phone_number, first_name, username)
        )
        conn.commit()
        conn.close()
    
    def load_sessions(self):
        """بارگذاری تمام سشن‌ها از دیتابیس"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('SELECT name, session_string, phone_number, first_name, username FROM sessions')
        sessions = cursor.fetchall()
        conn.close()
        return sessions
    
    def delete_session(self, name: str):
        """حذف سشن از دیتابیس"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('DELETE FROM sessions WHERE name = ?', (name,))
        conn.commit()
        conn.close()
    
    def get_session(self, name: str):
        """دریافت سشن خاص از دیتابیس"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('SELECT session_string FROM sessions WHERE name = ?', (name,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

# ==================== مدیریت وضعیت کاربران ====================
class UserState:
    def __init__(self):
        self.states = {}
    
    def set_state(self, user_id, state, data=None):
        self.states[user_id] = {"state": state, "data": data or {}}
    
    def get_state(self, user_id):
        return self.states.get(user_id)
    
    def clear_state(self, user_id):
        if user_id in self.states:
            del self.states[user_id]

user_state = UserState()

# ==================== مدیریت سشن‌ها و ویس چت ====================
class SessionManager:
    def __init__(self):
        self.storage = SessionStorage()
        self.clients: List[Client] = []
        self.calls: Dict[str, PyTgCalls] = {}
        self.active_calls: Dict[str, Dict] = {}
        self.voice_chat_sessions: Dict[str, Dict] = {}
        self.load_sessions()
    
    def load_sessions(self):
        """بارگذاری سشن‌ها از دیتابیس"""
        sessions = self.storage.load_sessions()
        print(f"📁 پیدا شد {len(sessions)} سشن در دیتابیس")
        
        for name, session_string, phone_number, first_name, username in sessions:
            try:
                client = Client(
                    name=name,
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session_string,
                    in_memory=True
                )
                self.clients.append(client)
                
                # ایجاد PyTgCalls برای هر کلاینت
                call = PyTgCalls(client)
                self.calls[name] = call
                
                print(f"✅ سشن {name} بارگذاری شد - {first_name or 'Unknown'} ({phone_number})")
            except Exception as e:
                print(f"❌ خطا در بارگذاری {name}: {e}")
    
    async def start_all_clients(self):
        """راه‌اندازی تمام کلاینت‌ها و PyTgCalls"""
        print("🔄 راه‌اندازی اکانت‌ها و ویس چت...")
        results = []
        
        for client in self.clients:
            session_name = client.name
            try:
                if not client.is_connected:
                    await client.start()
                
                # راه‌اندازی PyTgCalls
                call = self.calls[session_name]
                if not call.is_connected:
                    await call.start()
                
                me = await client.get_me()
                status = f"🟢 {session_name} - {me.first_name} (ویس چت فعال)"
                results.append({"name": session_name, "status": "success", "info": status})
                
            except Exception as e:
                results.append({"name": session_name, "status": "error", "info": f"🔴 {session_name} - خطا: {str(e)}"})
        
        return results
    
    async def stop_all_clients(self):
        """توقف تمام کلاینت‌ها"""
        print("⏹ توقف اکانت‌ها...")
        results = []
        
        # خروج از ویس چت‌ها
        for session_name in list(self.active_calls.keys()):
            try:
                await self._leave_voice_chat(session_name)
                results.append(f"🔇 {session_name} از ویس چت خارج شد")
            except Exception as e:
                results.append(f"❌ خطا در خروج {session_name}: {e}")
        
        # توقف PyTgCalls
        for session_name, call in self.calls.items():
            try:
                if call.is_connected:
                    await call.stop()
                    results.append(f"⏹️ ویس چت {session_name} متوقف شد")
            except Exception as e:
                results.append(f"❌ خطا در توقف ویس چت {session_name}: {e}")
        
        # توقف کلاینت‌ها
        for client in self.clients:
            try:
                if client.is_connected:
                    await client.stop()
                    results.append(f"⏹️ {client.name} متوقف شد")
                else:
                    results.append(f"ℹ️ {client.name} از قبل متوقف بود")
            except Exception as e:
                results.append(f"❌ خطا در توقف {client.name}: {e}")
        
        return results
    
    async def get_status(self):
        """دریافت وضعیت تمام اکانت‌ها"""
        status_list = []
        active_count = 0
        
        for client in self.clients:
            session_name = client.name
            try:
                if client.is_connected:
                    me = await client.get_me()
                    call_status = "🎧 در ویس چت" if session_name in self.active_calls else "💤"
                    
                    # بررسی وضعیت PyTgCalls
                    call = self.calls.get(session_name)
                    pytgcalls_status = "🟢" if call and call.is_connected else "🔴"
                    
                    status_list.append(f"{pytgcalls_status} {session_name} - {me.first_name} {call_status}")
                    active_count += 1
                else:
                    status_list.append(f"🔴 {session_name} - غیرفعال")
            except Exception as e:
                status_list.append(f"🔴 {session_name} - خطا: {str(e)}")
        
        return status_list, active_count
    
    async def join_voice_chat(self, voice_chat_link: str):
        """ورود واقعی به ویس چت با PyTgCalls"""
        results = []
        successful = 0
        
        try:
            voice_chat_link = voice_chat_link.strip()
            print(f"🔗 لینک دریافت شده: {voice_chat_link}")
            
            # استخراج username از لینک
            username = self.extract_username_from_link(voice_chat_link)
            if not username:
                return ["❌ لینک ویس چت نامعتبر است"], 0
            
            print(f"🔗 تشخیص داده شد: username={username}")
            
            for client in self.clients:
                session_name = client.name
                try:
                    if not client.is_connected:
                        await client.start()
                    
                    # راه‌اندازی PyTgCalls اگر متصل نیست
                    call = self.calls[session_name]
                    if not call.is_connected:
                        await call.start()
                    
                    # گرفتن اطلاعات چت
                    chat = await client.get_chat(username)
                    print(f"📱 چت پیدا شد: {chat.title} (ID: {chat.id})")
                    
                    # اتصال به ویس چت
                    success = await self._connect_to_voice_chat(client, call, chat.id, session_name)
                    
                    if success:
                        self.active_calls[session_name] = {
                            'chat_id': chat.id,
                            'chat_title': chat.title,
                            'join_time': asyncio.get_event_loop().time(),
                            'client': client,
                            'call': call
                        }
                        me = await client.get_me()
                        results.append(f"✅ {me.first_name} به ویس چت پیوست")
                        successful += 1
                    else:
                        me = await client.get_me()
                        results.append(f"❌ {me.first_name}: نتوانست به ویس چت بپیوندد")
                    
                    await asyncio.sleep(2)  # تأخیر بین اتصال اکانت‌ها
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ خطا برای {session_name}: {error_msg}")
                    results.append(f"❌ {session_name}: {error_msg}")
                        
        except Exception as e:
            error_msg = f"❌ خطا در پردازش لینک: {str(e)}"
            print(error_msg)
            return [error_msg], 0
        
        return results, successful
    
    def extract_username_from_link(self, link: str) -> Optional[str]:
        """استخراج username از لینک ویس چت"""
        patterns = [
            r"t\.me/([^/?]+)\?videochat",
            r"t\.me/([^/?]+)\?voicechat",
            r"https://t\.me/([^/?]+)\?videochat",
            r"https://t\.me/([^/?]+)\?voicechat",
            r"t\.me/([^/?]+)",
            r"https://t\.me/([^/?]+)",
            r"@([a-zA-Z0-9_]+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, link)
            if match:
                username = match.group(1)
                if username.startswith('+'):
                    continue
                return username.lstrip('@')
        
        return None
    
    async def _connect_to_voice_chat(self, client: Client, call: PyTgCalls, chat_id: int, session_name: str) -> bool:
        """اتصال واقعی به ویس چت با PyTgCalls"""
        try:
            # بررسی وضعیت ویس چت
            try:
                group_call = await call.get_group_call(chat_id)
                if not group_call:
                    # اگر ویس چت فعال نیست، یک ویس چت جدید ایجاد می‌کنیم
                    await client.send_message(chat_id, "🎧 در حال شروع ویس چت...")
                    await asyncio.sleep(2)
            except:
                pass
            
            # اتصال به ویس چت با صدای خاموش
            await call.join_group_call(
                chat_id,
                AudioPiped(
                    "http://docs.evostream.com/sample_content/assets/sintel1m720p.mp4",
                    HighQualityAudio(),
                ),
                invite_members=True
            )
            
            print(f"✅ {session_name} با موفقیت به ویس چت پیوست")
            return True
            
        except Exception as e:
            print(f"❌ خطا در اتصال {session_name} به ویس چت: {e}")
            
            # روش جایگزین: استفاده از دستورات تلگرام
            try:
                await client.send_message(chat_id, "/join")
                await asyncio.sleep(2)
                await client.send_message(chat_id, "🎧")
                await asyncio.sleep(1)
                
                # تلاش مجدد برای اتصال
                await call.join_group_call(
                    chat_id,
                    AudioPiped(
                        "http://docs.evostream.com/sample_content/assets/sintel1m720p.mp4",
                        HighQualityAudio(),
                    ),
                    invite_members=True
                )
                print(f"✅ {session_name} با روش جایگزین به ویس چت پیوست")
                return True
            except Exception as e2:
                print(f"❌ روش جایگزین نیز برای {session_name} شکست خورد: {e2}")
                return False
    
    async def _leave_voice_chat(self, session_name: str) -> bool:
        """خروج از ویس چت"""
        try:
            if session_name in self.active_calls:
                call_data = self.active_calls[session_name]
                call = call_data['call']
                chat_id = call_data['chat_id']
                
                # خروج از ویس چت
                if call.is_connected:
                    await call.leave_group_call(chat_id)
                
                # حذف از لیست کال‌های فعال
                del self.active_calls[session_name]
                
                print(f"✅ {session_name} از ویس چت خارج شد")
                return True
            
            return False
        except Exception as e:
            print(f"❌ خطا در خروج {session_name} از ویس چت: {e}")
            return False
    
    async def leave_all_voice_chats(self):
        """خروج از تمام ویس چت‌ها"""
        results = []
        successful = 0
        
        for session_name in list(self.active_calls.keys()):
            try:
                success = await self._leave_voice_chat(session_name)
                if success:
                    results.append(f"✅ {session_name} از ویس چت خارج شد")
                    successful += 1
                else:
                    results.append(f"❌ {session_name}: خطا در خروج")
            except Exception as e:
                results.append(f"❌ {session_name}: {str(e)}")
        
        return results, successful
    
    def add_session(self, name: str, session_string: str, phone_number: str = "", first_name: str = "", username: str = ""):
        """افزودن سشن جدید به سیستم"""
        try:
            # ذخیره در دیتابیس
            self.storage.save_session(name, session_string, phone_number, first_name, username)
            
            # ایجاد کلاینت جدید
            client = Client(
                name=name,
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True
            )
            self.clients.append(client)
            
            # ایجاد PyTgCalls جدید
            call = PyTgCalls(client)
            self.calls[name] = call
            
            print(f"✅ سشن {name} به سیستم اضافه شد")
            return True
        except Exception as e:
            print(f"❌ خطا در اضافه کردن سشن {name}: {e}")
            return False
    
    def delete_session(self, name: str):
        """حذف سشن از سیستم"""
        try:
            # حذف از دیتابیس
            self.storage.delete_session(name)
            
            # حذف از لیست کلاینت‌ها
            self.clients = [client for client in self.clients if client.name != name]
            
            # حذف از لیست کال‌ها
            if name in self.calls:
                del self.calls[name]
            
            # خروج از ویس چت اگر فعال است
            if name in self.active_calls:
                del self.active_calls[name]
            
            print(f"✅ سشن {name} حذف شد")
            return True
        except Exception as e:
            print(f"❌ خطا در حذف سشن {name}: {e}")
            return False

# ==================== ایجاد نمونه‌ها ====================
session_manager = SessionManager()

# ==================== کیبوردها ====================
main_keyboard = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🔧 ساخت سشن جدید"), KeyboardButton("📋 لیست سشن‌ها")],
        [KeyboardButton("🔄 راه‌اندازی اکانت‌ها"), KeyboardButton("⏹ توقف اکانت‌ها")],
        [KeyboardButton("🎧 ورود به ویس چت"), KeyboardButton("🔇 خروج از ویس چت")],
        [KeyboardButton("📊 وضعیت ربات"), KeyboardButton("🗑️ حذف سشن")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ لغو عملیات")]],
    resize_keyboard=True
)

# ==================== ربات اصلی ====================
app = Client("main_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def is_owner(message: Message):
    return message.from_user.id == OWNER_ID

# ==================== دستورات اصلی ====================
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    if not is_owner(message):
        await message.reply_text("❌ شما دسترسی به این ربات را ندارید.")
        return
    
    await message.reply_text(
        "🤖 **ربات مدیریت اکانت‌های تلگرام - نسخه Railway**\n\n"
        "🎧 **ویژگی‌های اصلی:**\n"
        "• اتصال واقعی به ویس چت با PyTgCalls\n"
        "• ذخیره‌سازی دائمی سشن‌ها در دیتابیس\n"
        "• سازگار با محیط Railway\n"
        "• مدیریت کامل اکانت‌ها\n\n"
        "🔧 **امکانات:**\n"
        "• ساخت سشن جدید\n"
        • مدیریت اکانت‌ها\n"
        "• ورود واقعی به ویس چت\n"
        "• نمایش وضعیت لحظه‌ای\n"
        "• حذف سشن‌ها\n\n"
        "از دکمه‌های زیر استفاده کنید:",
        reply_markup=main_keyboard
    )

# ==================== ساخت سشن جدید ====================
@app.on_message(filters.regex("^🔧 ساخت سشن جدید$"))
async def create_session_command(client, message: Message):
    if not is_owner(message):
        return
    
    user_state.set_state(message.from_user.id, "waiting_session_name")
    await message.reply_text(
        "🔧 **ساخت سشن جدید - مرحله ۱/۴**\n\n"
        "لطفاً یک نام برای سشن وارد کنید:\n"
        "• فقط حروف انگلیسی و اعداد\n"
        "• مثال: `account1`, `user_session`",
        reply_markup=cancel_keyboard
    )

# ==================== لیست سشن‌ها ====================
@app.on_message(filters.regex("^📋 لیست سشن‌ها$"))
async def list_sessions_command(client, message: Message):
    if not is_owner(message):
        return
    
    status_list, active_count = await session_manager.get_status()
    
    if not status_list:
        await message.reply_text("📭 هیچ سشنی یافت نشد.", reply_markup=main_keyboard)
        return
    
    text = f"📋 **لیست اکانت‌ها**\n\n"
    text += f"🟢 فعال: {active_count} | 🔴 غیرفعال: {len(status_list) - active_count}\n"
    text += f"🎧 در ویس چت: {len(session_manager.active_calls)}\n\n"
    
    for i, status in enumerate(status_list[:15], 1):
        text += f"{i}. {status}\n"
    
    if len(status_list) > 15:
        text += f"\n... و {len(status_list) - 15} اکانت دیگر"
    
    await message.reply_text(text, reply_markup=main_keyboard)

# ==================== راه‌اندازی اکانت‌ها ====================
@app.on_message(filters.regex("^🔄 راه‌اندازی اکانت‌ها$"))
async def start_clients_command(client, message: Message):
    if not is_owner(message):
        return
    
    if not session_manager.clients:
        await message.reply_text("❌ هیچ سشنی برای راه‌اندازی وجود ندارد.", reply_markup=main_keyboard)
        return
    
    status_msg = await message.reply_text("🔄 در حال راه‌اندازی اکانت‌ها و ویس چت...")
    
    results = await session_manager.start_all_clients()
    
    success_count = sum(1 for r in results if r["status"] == "success")
    
    text = f"✅ **راه‌اندازی کامل شد**\n\n"
    text += f"• 🟢 موفق: {success_count}\n"
    text += f"• ❌ خطا: {len(results) - success_count}\n"
    text += f"• 📊 کل: {len(results)}\n"
    text += f"• 🎧 ویس چت فعال: {len(session_manager.calls)}\n\n"
    
    for result in results[:10]:
        text += f"• {result['info']}\n"
    
    if len(results) > 10:
        text += f"\n... و {len(results) - 10} اکانت دیگر"
    
    await status_msg.edit_text(text, reply_markup=main_keyboard)

# ==================== توقف اکانت‌ها ====================
@app.on_message(filters.regex("^⏹ توقف اکانت‌ها$"))
async def stop_clients_command(client, message: Message):
    if not is_owner(message):
        return
    
    if not session_manager.clients:
        await message.reply_text("❌ هیچ سشنی برای توقف وجود ندارد.", reply_markup=main_keyboard)
        return
    
    status_msg = await message.reply_text("⏹ در حال توقف اکانت‌ها و ویس چت...")
    
    results = await session_manager.stop_all_clients()
    
    text = "⏹️ **نتایج توقف اکانت‌ها**\n\n"
    for result in results[:15]:
        text += f"• {result}\n"
    
    if len(results) > 15:
        text += f"\n... و {len(results) - 15} نتیجه دیگر"
    
    await status_msg.edit_text(text, reply_markup=main_keyboard)

# ==================== ورود به ویس چت ====================
@app.on_message(filters.regex("^🎧 ورود به ویس چت$"))
async def join_voice_chat_command(client, message: Message):
    if not is_owner(message):
        return
    
    user_state.set_state(message.from_user.id, "waiting_voice_chat_link")
    await message.reply_text(
        "🎧 **ورود به ویس چت**\n\n"
        "لطفاً لینک ویس چت را ارسال کنید:\n"
        "• مثال: https://t.me/fazayimaishat?videochat\n"
        "• یا: t.me/fazayimaishat?voicechat\n"
        "• یا: @fazayimaishat\n\n"
        "⚠️ **توجه:**\n"
        "• اکانت‌ها باید عضو گروه باشند\n"
        "• ویس چت باید فعال باشد\n"
        "• اتصال واقعی با PyTgCalls برقرار می‌شود",
        reply_markup=cancel_keyboard
    )

# ==================== خروج از ویس چت ====================
@app.on_message(filters.regex("^🔇 خروج از ویس چت$"))
async def leave_voice_chat_command(client, message: Message):
    if not is_owner(message):
        return
    
    status_msg = await message.reply_text("🔇 در حال خروج از ویس چت‌ها...")
    
    results, successful = await session_manager.leave_all_voice_chats()
    
    result_text = "\n".join(results[:15])
    if len(results) > 15:
        result_text += f"\n... و {len(results) - 15} نتیجه دیگر"
    
    await status_msg.edit_text(
        f"🔇 **نتایج خروج از ویس چت:**\n\n"
        f"✅ خارج شدند: {successful}\n"
        f"📊 کل کال‌های فعال: {len(session_manager.active_calls)}\n\n"
        f"{result_text}",
        reply_markup=main_keyboard
    )

# ==================== وضعیت ربات ====================
@app.on_message(filters.regex("^📊 وضعیت ربات$"))
async def bot_status_command(client, message: Message):
    if not is_owner(message):
        return
    
    status_list, active_count = await session_manager.get_status()
    
    # اطلاعات دیتابیس
    storage = SessionStorage()
    db_sessions = storage.load_sessions()
    
    text = (
        "🤖 **وضعیت کامل ربات - Railway**\n\n"
        f"• 📁 سشن‌های بارگذاری شده: {len(session_manager.clients)}\n"
        f"• 💾 سشن‌ها در دیتابیس: {len(db_sessions)}\n"
        f"• 🟢 اکانت‌های فعال: {active_count}\n"
        f"• 🎧 PyTgCalls فعال: {len([c for c in session_manager.calls.values() if c.is_connected])}\n"
        f"• 🔊 در ویس چت: {len(session_manager.active_calls)}\n"
        f"• 👤 کاربر فعال: {message.from_user.first_name}\n\n"
    )
    
    if session_manager.active_calls:
        text += "**کال‌های فعال:**\n"
        for session_name, call_info in list(session_manager.active_calls.items())[:5]:
            duration = int(asyncio.get_event_loop().time() - call_info['join_time'])
            text += f"• {session_name} - {call_info['chat_title']} ({duration} ثانیه)\n"
    
    if status_list:
        text += "\n**آخرین وضعیت اکانت‌ها:**\n"
        for status in status_list[:5]:
            text += f"• {status}\n"
        if len(status_list) > 5:
            text += f"• ... و {len(status_list) - 5} اکانت دیگر"
    else:
        text += "📭 هیچ اکانتی بارگذاری نشده است"
    
    await message.reply_text(text, reply_markup=main_keyboard)

# ==================== حذف سشن ====================
@app.on_message(filters.regex("^🗑️ حذف سشن$"))
async def delete_session_command(client, message: Message):
    if not is_owner(message):
        return
    
    user_state.set_state(message.from_user.id, "waiting_delete_session")
    
    status_list, _ = await session_manager.get_status()
    
    if not status_list:
        await message.reply_text("❌ هیچ سشنی برای حذف وجود ندارد.", reply_markup=main_keyboard)
        user_state.clear_state(message.from_user.id)
        return
    
    text = "🗑️ **حذف سشن**\n\n"
    text += "لطفاً نام سشن مورد نظر برای حذف را وارد کنید:\n\n"
    
    for i, status in enumerate(status_list[:10], 1):
        text += f"{i}. {status}\n"
    
    if len(status_list) > 10:
        text += f"\n... و {len(status_list) - 10} سشن دیگر"
    
    await message.reply_text(text, reply_markup=cancel_keyboard)

# ==================== لغو عملیات ====================
@app.on_message(filters.regex("^❌ لغو عملیات$"))
async def cancel_command(client, message: Message):
    if not is_owner(message):
        return
    
    user_state.clear_state(message.from_user.id)
    await message.reply_text("✅ عملیات کنسل شد.", reply_markup=main_keyboard)

# ==================== پردازش پیام‌های متنی ====================
@app.on_message(filters.text & filters.private)
async def handle_text_messages(client, message: Message):
    if not is_owner(message):
        return
    
    user_id = message.from_user.id
    text = message.text.strip()
    current_state = user_state.get_state(user_id)
    
    if not current_state:
        return
    
    state = current_state["state"]
    data = current_state["data"]
    
    try:
        if state == "waiting_session_name":
            await handle_session_name(client, message, text, user_id)
        
        elif state == "waiting_phone_number":
            await handle_phone_number(client, message, text, user_id, data)
        
        elif state == "waiting_phone_code":
            await handle_phone_code(client, message, text, user_id, data)
        
        elif state == "waiting_password":
            await handle_password(client, message, text, user_id, data)
        
        elif state == "waiting_voice_chat_link":
            await handle_voice_chat_join(client, message, text, user_id)
        
        elif state == "waiting_delete_session":
            await handle_delete_session(client, message, text, user_id)
    
    except Exception as e:
        await message.reply_text(f"❌ خطا: {str(e)}", reply_markup=main_keyboard)
        user_state.clear_state(user_id)

# ==================== هندلرهای ساخت سشن ====================
async def handle_session_name(client, message, text, user_id):
    if not text.replace('_', '').isalnum():
        await message.reply_text(
            "❌ نام سشن فقط می‌تواند شامل حروف انگلیسی، اعداد و زیرخط باشد.\n"
            "لطفاً دوباره وارد کنید:",
            reply_markup=cancel_keyboard
        )
        return
    
    # بررسی وجود سشن در دیتابیس
    storage = SessionStorage()
    existing_session = storage.get_session(text)
    if existing_session:
        await message.reply_text(
            "❌ سشنی با این نام وجود دارد.\nلطفاً نام دیگری انتخاب کنید:",
            reply_markup=cancel_keyboard
        )
        return
    
    user_state.set_state(user_id, "waiting_phone_number", {"session_name": text})
    await message.reply_text(
        "📱 **ساخت سشن جدید - مرحله ۲/۴**\n\n"
        "لطفاً شماره تلفن را وارد کنید:\n"
        "• با پیش‌شماره کشور\n"
        "• مثال: +989123456789",
        reply_markup=cancel_keyboard
    )

async def handle_phone_number(client, message, text, user_id, data):
    if not text.startswith('+') or not text[1:].isdigit():
        await message.reply_text(
            "❌ شماره تلفن معتبر نیست.\nلطفاً دوباره وارد کنید:",
            reply_markup=cancel_keyboard
        )
        return
    
    session_name = data["session_name"]
    
    try:
        client_obj = Client(
            name=session_name,
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        
        await client_obj.connect()
        sent_code = await client_obj.send_code(text)
        
        user_state.set_state(user_id, "waiting_phone_code", {
            "session_name": session_name,
            "phone_number": text,
            "client": client_obj,
            "phone_code_hash": sent_code.phone_code_hash
        })
        
        await message.reply_text(
            "🔐 **ساخت سشن جدید - مرحله ۳/۴**\n\n"
            "کد تأیید برای شما ارسال شد.\n"
            "لطفاً کد ۵ رقمی را وارد کنید:",
            reply_markup=cancel_keyboard
        )
    
    except PhoneNumberInvalid:
        await message.reply_text(
            "❌ شماره تلفن نامعتبر است.\nاز ابتدا شروع کنید:",
            reply_markup=main_keyboard
        )
        user_state.clear_state(user_id)
    except Exception as e:
        await message.reply_text(
            f"❌ خطا در ارسال کد: {str(e)}\nاز ابتدا شروع کنید:",
            reply_markup=main_keyboard
        )
        user_state.clear_state(user_id)

async def handle_phone_code(client, message, text, user_id, data):
    if not text.isdigit() or len(text) != 5:
        await message.reply_text(
            "❌ کد باید ۵ رقمی باشد.\nلطفاً دوباره وارد کنید:",
            reply_markup=cancel_keyboard
        )
        return
    
    client_obj = data["client"]
    
    try:
        await client_obj.sign_in(
            phone_number=data["phone_number"],
            phone_code_hash=data["phone_code_hash"],
            phone_code=text
        )
        
        await finalize_session(client, message, user_id, data, client_obj)
    
    except SessionPasswordNeeded:
        user_state.set_state(user_id, "waiting_password", data)
        await message.reply_text(
            "🔒 **ساخت سشن جدید - مرحله ۴/۴**\n\n"
            "این اکانت دارای رمز دو مرحله‌ای است.\n"
            "لطفاً رمز عبور را وارد کنید:",
            reply_markup=cancel_keyboard
        )
    
    except PhoneCodeInvalid:
        await message.reply_text(
            "❌ کد نامعتبر است.\nلطفاً کد صحیح را وارد کنید:",
            reply_markup=cancel_keyboard
        )
    
    except Exception as e:
        await message.reply_text(
            f"❌ خطا در ورود: {str(e)}\nاز ابتدا شروع کنید:",
            reply_markup=main_keyboard
        )
        await client_obj.disconnect()
        user_state.clear_state(user_id)

async def handle_password(client, message, text, user_id, data):
    client_obj = data["client"]
    
    try:
        await client_obj.check_password(text)
        await finalize_session(client, message, user_id, data, client_obj)
    
    except Exception as e:
        await message.reply_text(
            "❌ رمز عبور نامعتبر است.\nلطفاً دوباره وارد کنید:",
            reply_markup=cancel_keyboard
        )

async def finalize_session(client, message, user_id, data, client_obj):
    """پایان فرآیند ساخت سشن"""
    try:
        me = await client_obj.get_me()
        session_string = await client_obj.export_session_string()
        
        await client_obj.disconnect()
        
        # ذخیره سشن در سیستم
        success = session_manager.add_session(
            name=data["session_name"],
            session_string=session_string,
            phone_number=data["phone_number"],
            first_name=me.first_name or "",
            username=me.username or ""
        )
        
        if success:
            success_text = (
                "🎉 **سشن با موفقیت ساخته شد!**\n\n"
                f"**👤 اطلاعات اکانت:**\n"
                f"• نام: {me.first_name or '---'}\n"
                f"• فامیلی: {me.last_name or '---'}\n"
                f"• آیدی: @{me.username or '---'}\n"
                f"• شماره: {data['phone_number']}\n\n"
                f"**💾 اطلاعات سشن:**\n"
                f"• نام: {data['session_name']}\n"
                f"• ذخیره شده در: دیتابیس Railway\n"
                f"• سشن استرینگ: {session_string[:50]}...\n\n"
                f"✅ سشن به ربات اضافه شد و آماده استفاده در ویس چت است."
            )
        else:
            success_text = "❌ خطا در ذخیره سشن در سیستم"
        
        await message.reply_text(success_text, reply_markup=main_keyboard)
        user_state.clear_state(user_id)
    
    except Exception as e:
        await message.reply_text(
            f"❌ خطا در ذخیره سشن: {str(e)}",
            reply_markup=main_keyboard
        )
        user_state.clear_state(user_id)

# ==================== هندلر ویس چت ====================
async def handle_voice_chat_join(client, message, text, user_id):
    status_msg = await message.reply_text("🎧 در حال اتصال به ویس چت...")
    
    # راه‌اندازی اکانت‌ها
    await session_manager.start_all_clients()
    
    results, successful = await session_manager.join_voice_chat(text)
    
    result_text = "\n".join(results[:15])
    if len(results) > 15:
        result_text += f"\n... و {len(results) - 15} نتیجه دیگر"
    
    await status_msg.edit_text(
        f"🎧 **نتایج ورود به ویس چت:**\n\n"
        f"✅ موفق: {successful}\n"
        f"📊 کل: {len(session_manager.clients)}\n"
        f"🔊 اتصال واقعی با PyTgCalls\n\n"
        f"{result_text}",
        reply_markup=main_keyboard
    )
    user_state.clear_state(user_id)

# ==================== هندلر حذف سشن ====================
async def handle_delete_session(client, message, text, user_id):
    """حذف سشن از سیستم"""
    try:
        success = session_manager.delete_session(text)
        
        if success:
            await message.reply_text(
                f"✅ سشن `{text}` با موفقیت حذف شد.",
                reply_markup=main_keyboard
            )
        else:
            await message.reply_text(
                f"❌ خطا در حذف سشن `{text}`.",
                reply_markup=main_keyboard
            )
    
    except Exception as e:
        await message.reply_text(
            f"❌ خطا در حذف سشن: {str(e)}",
            reply_markup=main_keyboard
        )
    
    user_state.clear_state(user_id)

# ==================== راه‌اندازی ====================
async def main():
    print("🚀 در حال راه‌اندازی ربات در Railway...")
    print(f"📊 محیط: {'Railway' if 'RAILWAY_ENVIRONMENT' in os.environ else 'Local'}")
    
    # اطلاعات دیتابیس
    storage = SessionStorage()
    sessions = storage.load_sessions()
    print(f"📁 {len(sessions)} سشن از دیتابیس بارگذاری شد")
    
    await app.start()
    
    me = await app.get_me()
    print(f"🤖 ربات: @{me.username} ({me.first_name})")
    print(f"📊 {len(session_manager.clients)} سشن بارگذاری شد")
    print(f"🎧 {len(session_manager.calls)} PyTgCalls آماده")
    print(f"👤 مالک: {OWNER_ID}")
    
    print("✅ ربات در Railway آماده است! از /start استفاده کنید.")

if __name__ == "__main__":
    print("=" * 50)
    print("ربات مدیریت اکانت‌های تلگرام - نسخه Railway")
    print("=" * 50)
    
    # بررسی کتابخانه‌ها
    try:
        import pytgcalls
    except ImportError:
        print("❌ کتابخانه pytgcalls نصب نیست.")
    
    try:
        import pyrogram
    except ImportError:
        print("❌ کتابخانه pyrogram نصب نیست.")
    
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
        print("🟢 ربات فعال و در حال اجرا...")
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n⏹ ربات متوقف شد")
    except Exception as e:
        print(f"❌ خطا: {e}")