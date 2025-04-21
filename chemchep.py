# -*- coding: utf-8 -*-

# === Import Thư viện ===
import telebot
import json
import logging
import os
import random
import time
import threading # Cần cho /thongbao, xóa tin nhắn delay và schedule
import sqlite3
import requests # Cần cho các API (/thoitiet, /phim, /rutgon, /fl, /flauto)
import qrcode   # Cần cho lệnh /qr
from io import BytesIO # Cần cho lệnh /qr
from datetime import datetime, timedelta, date # Cần cho /time, /plan, /diemdanh
from pathlib import Path
from threading import Lock
import html # Dùng để escape HTML entities
from functools import wraps # Cần thiết cho decorator
import schedule # <<< THÊM MỚI - Cần cho /flauto

# === Cấu hình ===
# --- Bắt buộc thay đổi ---
BOT_TOKEN = "7352828711:AAEM-kWD-A8PXrjpYKLbHAn-MRVXKMzzmK0"             # !!! THAY TOKEN BOT CỦA BẠN !!!
ADMIN_ID = 5992662564                          # !!! THAY ID TELEGRAM ADMIN CỦA BẠN !!!
ADMIN_USERNAME = "mnhutdznecon"          # !!! THAY USERNAME ADMIN (không có @) !!!
WEATHER_API_KEY = "a40c3955762a3e2ccbd83c25ece1cf5c" # !!! THAY API KEY THỜI TIẾT !!!
TMDB_API_KEY = "2a551c919f8c5fe445096179fc184ac3"            # !!! THAY API KEY CỦA TMDb !!!
TIKTOK_FL_API_BASE_URL = "https://apitangfltiktok.soundcast.me/telefl.php" # <<< API URL TĂNG FL TIKTOK >>>

# --- ID NHÓM ĐƯỢC PHÉP HOẠT ĐỘNG ---
ALLOWED_GROUP_ID = -1001931537243 # <<< ID NHÓM CỐ ĐỊNH >>>

# --- Đường dẫn file ---
BASE_DIR = Path(__file__).parent
DATA_FILE_PATH = BASE_DIR / "taixiu_data_telebot.json" # File dữ liệu game (JSON)
DB_FILE_PATH = BASE_DIR / "user_vip_data.db"          # File database VIP (SQLite)
QR_CODE_IMAGE_PATH = BASE_DIR / "vietqr_payment.png"  # File ảnh QR cho /muavip (Cần tạo sẵn)

# --- Thông tin VIP & Ngân hàng ---
VIP_PRICE = "50K"
VIP_DURATION_DAYS = 30
BANK_NAME = "MB Bank"
ACCOUNT_NUMBER = "17363999999999" # Thay STK thật nếu cần
ACCOUNT_NAME = "BUI MINH NHUT"    # Thay tên TK thật nếu cần
MAX_VIP_DURATION_DAYS = 18250 # ~50 năm

# --- Cấu hình Game ---
HOUSE_EDGE_PERCENT = 5 # Tỷ lệ lợi thế nhà cái (%) cho Tài Xỉu
JACKPOT_AMOUNT = 100000000
JACKPOT_CHANCE_ONE_IN = 5000 # Tỷ lệ 1/5000 trúng Jackpot mỗi lần chơi Tài Xỉu
DELETE_DELAY = 15 # Giây
CHECKIN_REWARD = 50000   # <<< THAY ĐỔI - Giảm phần thưởng điểm danh
PLAY_COOLDOWN = 2 # Giây chờ giữa các lần chơi Tài Xỉu
BAUCUA_COOLDOWN = 2 # Giây chờ giữa các lần chơi Bầu Cua
TOP_N = 10 # Số lượng người hiển thị trong /top
BAUCUA_ITEMS = ["bầu", "cua", "tôm", "cá", "gà", "nai"]
BAUCUA_ICONS = {"bầu": "🍐", "cua": "🦀", "tôm": "🦐", "cá": "🐟", "gà": "🐓", "nai": "🦌"}

# --- Cấu hình Lệnh Mới ---
FLAUTO_COST = 100000      # <<< THÊM MỚI - Chi phí cho /flauto
FLAUTO_INTERVAL_MINUTES = 16 # <<< THÊM MỚI - Khoảng thời gian tự động fl (phút)

# === Logging ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Quản lý dữ liệu & Trạng thái ===
data_lock = Lock()
start_time = datetime.now()
last_command_time = {}
allowed_vip_users = set()
maintenance_mode = False
MAINTENANCE_MESSAGE = "🛠️ Bot đang bảo trì để nâng cấp. Vui lòng thử lại sau ít phút! ⏳"
auto_follow_tasks = {} # <<< THÊM MỚI - Lưu trữ các tác vụ /flauto đang chạy {user_id: {'tiktok_username': '...', 'job': schedule_job}}
scheduler_lock = Lock() # <<< THÊM MỚI - Khóa để quản lý tác vụ schedule an toàn

# === Decorator Kiểm Tra Nhóm ===
def kiem_tra_nhom_cho_phep(func):
    @wraps(func)
    def wrapper(message: telebot.types.Message, *args, **kwargs):
        if message.chat.id == ALLOWED_GROUP_ID:
            return func(message, *args, **kwargs)
        else:
            # Cho phép admin dùng lệnh ở bất cứ đâu (trong PM chẳng hạn)
            if message.from_user.id == ADMIN_ID:
                 logger.info(f"Admin {ADMIN_ID} dùng lệnh '{message.text}' ngoài nhóm cho phép (ID: {message.chat.id}).")
                 return func(message, *args, **kwargs)
            else:
                logger.info(f"Lệnh '{message.text}' bị bỏ qua từ chat ID {message.chat.id} (không được phép).")
                return
    return wrapper

# === Các hàm tiện ích ===
def format_xu(amount: int | float) -> str:
    try:
        if isinstance(amount, float) and amount.is_integer(): amount = int(amount)
        if isinstance(amount, float): amount = round(amount)
        return f"{amount:,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return str(amount)

def get_user_info_from_message(message: telebot.types.Message) -> tuple[int, str]:
    user = message.from_user
    user_id = user.id
    user_name = user.username or f"{user.first_name} {user.last_name or ''}".strip() or f"User_{user_id}"
    safe_user_name = html.escape(user_name)
    return user_id, safe_user_name

def get_user_profile_info(user_id: int) -> str:
    try:
        chat = bot.get_chat(user_id)
        uid = chat.id
        fname = html.escape(chat.first_name or "")
        lname = html.escape(chat.last_name or "")
        full_name = f"{fname} {lname}".strip()
        uname = chat.username
        safe_bio = "📝 Không thể lấy hoặc không có."
        try:
             maybe_bio = getattr(chat, 'bio', None)
             if maybe_bio:
                 safe_bio = f"📝 {html.escape(maybe_bio)}"
        except Exception: pass

        mention_link = f"<a href='tg://user?id={uid}'>{full_name or 'Ẩn Danh'}</a>"
        info_lines = [
            "👤✨ <b>Thông tin người dùng</b> ✨👤",
            "-----------------------------",
            f"🆔 ID: <code>{uid}</code>",
            f"📝 Tên: {mention_link}",
            f"🔗 Username: @{uname}" if uname else "🔗 Username: 👻 Không có",
            f"📜 Bio: {safe_bio}"
        ]
        return "\n".join(info_lines)
    except telebot.apihelper.ApiTelegramException as e:
        error_msg = str(e).lower()
        logger.warning(f"Lỗi API khi lấy thông tin user {user_id}: {e}")
        if "chat not found" in error_msg or "user not found" in error_msg:
            return f"❌ Không tìm thấy người dùng với ID <code>{user_id}</code>."
        elif "bot can't initiate conversation" in error_msg:
             return f"❌ Tôi không thể bắt đầu trò chuyện với người dùng ID <code>{user_id}</code>."
        else:
            return f"❌ Lỗi API Telegram: {html.escape(str(e))}"
    except Exception as e:
        logger.error(f"Lỗi không xác định khi lấy thông tin user {user_id}: {e}", exc_info=True)
        return f"❌ Lỗi không mong muốn khi lấy thông tin ID <code>{user_id}</code>."

# === Database Setup (SQLite cho VIP Users) ===
def initialize_vip_database():
    try:
        conn = sqlite3.connect(DB_FILE_PATH, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS vip_users (
                            user_id INTEGER PRIMARY KEY,
                            expiration_time TEXT NOT NULL
                          )''')
        conn.commit(); conn.close()
        logger.info(f"💾 Đã khởi tạo/kết nối database VIP: {DB_FILE_PATH}")
    except Exception as e: logger.error(f"🆘 Lỗi khởi tạo database VIP: {e}", exc_info=True)

def load_vip_users_from_db():
    global allowed_vip_users
    try:
        conn = sqlite3.connect(DB_FILE_PATH, check_same_thread=False); conn.row_factory = sqlite3.Row
        cursor = conn.cursor(); cursor.execute('SELECT user_id, expiration_time FROM vip_users'); rows = cursor.fetchall(); conn.close()
        current_time = datetime.now(); valid_vips = set(); expired_vips_to_delete = []
        for row in rows:
            user_id = row['user_id']; exp_time_str = row['expiration_time']
            try:
                exp_time = datetime.fromisoformat(exp_time_str)
                if exp_time > current_time: valid_vips.add(user_id)
                else: expired_vips_to_delete.append(user_id)
            except (ValueError, TypeError): logger.warning(f"DB VIP: Format time lỗi user {user_id}: {exp_time_str}")
        allowed_vip_users = valid_vips; logger.info(f"✅ Đã load {len(allowed_vip_users)} VIP users hợp lệ.")
        if expired_vips_to_delete:
            logger.info(f"🗑️ Đang xóa {len(expired_vips_to_delete)} VIP users hết hạn...")
            conn_del = sqlite3.connect(DB_FILE_PATH, check_same_thread=False); cursor_del = conn_del.cursor()
            cursor_del.executemany("DELETE FROM vip_users WHERE user_id = ?", [(uid,) for uid in expired_vips_to_delete])
            conn_del.commit(); conn_del.close(); logger.info(f"✅ Đã xóa {len(expired_vips_to_delete)} VIP users hết hạn.")
    except Exception as e: logger.error(f"🆘 Lỗi load VIP users: {e}", exc_info=True); allowed_vip_users = set()

def save_vip_user_to_db(user_id: int, duration_days: int) -> tuple[bool, datetime | str]:
    if not (0 < duration_days <= MAX_VIP_DURATION_DAYS): return False, f"⚠️ Số ngày VIP phải từ 1 đến {MAX_VIP_DURATION_DAYS}."
    try:
        current_expiration = get_vip_expiration_time_from_db(user_id); start_date = datetime.now()
        if current_expiration and current_expiration > start_date: start_date = current_expiration
        expiration_time = start_date + timedelta(days=duration_days)
        conn = sqlite3.connect(DB_FILE_PATH, check_same_thread=False); cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO vip_users (user_id, expiration_time) VALUES (?, ?)', (user_id, expiration_time.isoformat()))
        conn.commit(); conn.close(); logger.info(f"💾 Lưu/Update VIP user {user_id}, hết hạn {expiration_time.isoformat()}")
        load_vip_users_from_db(); return True, expiration_time
    except OverflowError: logger.error(f"🆘 Lỗi tràn số khi tính ngày hết hạn VIP cho user {user_id}, {duration_days} ngày."); return False, "🆘 Lỗi tràn số (thời gian quá xa)."
    except Exception as e: logger.error(f"🆘 Lỗi lưu VIP user {user_id}: {e}", exc_info=True); return False, f"🆘 Lỗi DB: {e}"

def delete_vip_user_from_db(target_user_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_FILE_PATH, check_same_thread=False); cursor = conn.cursor()
        cursor.execute("DELETE FROM vip_users WHERE user_id = ?", (target_user_id,)); conn.commit()
        deleted_rows = cursor.rowcount; conn.close()
        if deleted_rows > 0: logger.info(f"🗑️ Đã xóa VIP user {target_user_id}."); allowed_vip_users.discard(target_user_id); return True
        return False
    except Exception as e: logger.error(f"🆘 Lỗi xóa VIP user {target_user_id}: {e}", exc_info=True); return False

def get_vip_expiration_time_from_db(user_id: int) -> datetime | None:
    try:
        conn = sqlite3.connect(DB_FILE_PATH, check_same_thread=False); cursor = conn.cursor()
        cursor.execute("SELECT expiration_time FROM vip_users WHERE user_id = ?", (user_id,)); result = cursor.fetchone(); conn.close()
        if result:
            try: return datetime.fromisoformat(result[0])
            except (ValueError, TypeError): logger.warning(f"DB VIP: Format time lỗi khi đọc user {user_id}: {result[0]}"); return None
        return None
    except Exception as e: logger.error(f"🆘 Lỗi query hạn VIP user {user_id}: {e}", exc_info=True); return None

# === Các hàm load/save/get data game (JSON) ===
def load_game_data_sync() -> dict:
    with data_lock:
        try:
            if DATA_FILE_PATH.exists() and DATA_FILE_PATH.stat().st_size > 0:
                with open(DATA_FILE_PATH, "r", encoding="utf-8") as f: return json.load(f)
            logger.warning(f"⚠️ File data game {DATA_FILE_PATH} trống hoặc không tồn tại. Tạo mới."); return {}
        except json.JSONDecodeError: logger.error(f"🆘 Lỗi giải mã JSON trong file {DATA_FILE_PATH}. Trả về dữ liệu trống.", exc_info=True); return {}
        except Exception as e: logger.error(f"🆘 Lỗi đọc file {DATA_FILE_PATH}: {e}. Trả về dữ liệu trống.", exc_info=True); return {}

def save_game_data_sync(data: dict):
    with data_lock:
        temp_file_path = DATA_FILE_PATH.with_suffix(".json.tmp")
        try:
            with open(temp_file_path, "w", encoding="utf-8") as f: json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(temp_file_path, DATA_FILE_PATH)
        except Exception as e:
            logger.error(f"🆘 Lỗi nghiêm trọng khi lưu game data vào {DATA_FILE_PATH}: {e}", exc_info=True)
            if temp_file_path.exists():
                try: temp_file_path.unlink()
                except OSError as rm_err: logger.error(f"🆘 Không thể xóa file tạm {temp_file_path} sau lỗi lưu: {rm_err}")

def get_player_data(user_id: int, user_name: str, data: dict) -> dict:
    uid = str(user_id)
    safe_user_name = user_name
    player_info = data.get(uid)

    if player_info is None:
        player_info = {
            "name": safe_user_name, "xu": 100000, "plays": 0, "last_checkin_date": None
        }
        data[uid] = player_info
        logger.info(f"✨ Tạo người chơi mới: ID={uid}, Tên='{safe_user_name}', Xu={player_info['xu']}")
    else:
        # Luôn cập nhật tên nếu có thay đổi
        if player_info.get("name") != safe_user_name:
            logger.info(f"🔄 Cập nhật tên người chơi {uid}: '{player_info.get('name', 'N/A')}' -> '{safe_user_name}'")
            player_info["name"] = safe_user_name
        # Đảm bảo các trường cơ bản tồn tại
        player_info.setdefault("xu", 0)
        player_info.setdefault("plays", 0)
        player_info.setdefault("last_checkin_date", None)

    return player_info

# === Logic Game ===
def roll_dice_sync() -> tuple[list[int], int, str]:
    dice = [random.randint(1, 6) for _ in range(3)]; total = sum(dice)
    result = "tài" if 11 <= total <= 18 else "xỉu"; return dice, total, result

def roll_baucua_sync() -> list[str]:
    return random.choices(BAUCUA_ITEMS, k=3)

# === Khởi tạo Bot ===
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
logger.info("🚀 TeleBot instance đã được tạo.")

# === Hàm xóa tin nhắn sau delay ===
def delete_message_after_delay(chat_id: int, message_id: int, delay: int):
    def delete_task():
        try:
            time.sleep(delay)
            bot.delete_message(chat_id=chat_id, message_id=message_id)
        except telebot.apihelper.ApiTelegramException as e:
            if "message to delete not found" in str(e).lower() or "message identifier is not specified" in str(e).lower():
                pass
            else:
                logger.warning(f"⚠️ Lỗi API khi xóa tin nhắn {message_id} trong chat {chat_id}: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Lỗi không xác định khi xóa tin nhắn {message_id} trong chat {chat_id}: {e}")

    if delay > 0:
        thread = threading.Thread(target=delete_task, daemon=True)
        thread.start()

# === Middleware kiểm tra bảo trì ===
@bot.message_handler(func=lambda message: maintenance_mode and message.from_user.id != ADMIN_ID)
def handle_maintenance(message: telebot.types.Message):
    try:
        # Chỉ trả lời nếu tin nhắn đến từ nhóm được phép hoặc từ admin (dù admin bỏ qua check này)
        if message.chat.id == ALLOWED_GROUP_ID or message.from_user.id == ADMIN_ID:
            if message.text and message.text.startswith('/'): # Chỉ trả lời lệnh
                bot.reply_to(message, MAINTENANCE_MESSAGE)
    except Exception as e:
        logger.error(f"🆘 Lỗi gửi tin nhắn bảo trì cho user {message.from_user.id}: {e}")

# === Hàm chạy API tự động (/flauto) ===
def _run_auto_follow(user_id: int, tiktok_username: str):
    """Hàm được schedule gọi để thực hiện tăng follow tự động."""
    api_url = f"{TIKTOK_FL_API_BASE_URL}?user={tiktok_username}&userid={user_id}&tokenbot={BOT_TOKEN}"
    logger.info(f"🤖 [AutoFL] Đang chạy tác vụ cho User {user_id}, TikTok '{tiktok_username}'...")
    try:
        response = requests.get(api_url, timeout=25)
        response.raise_for_status()
        logger.info(f"✅ [AutoFL] Gọi API thành công cho User {user_id}, TikTok '{tiktok_username}'. Response: {response.text[:100]}...")
        # Gửi thông báo thành công (tùy chọn, có thể tắt nếu gây spam)
        # try:
        #     bot.send_message(user_id, f"✨ Tác vụ tự động tăng follow cho @{html.escape(tiktok_username)} vừa được thực hiện.", parse_mode='HTML')
        # except Exception as send_err:
        #     logger.warning(f"⚠️ [AutoFL] Không thể gửi thông báo thành công cho user {user_id}: {send_err}")
    except requests.exceptions.Timeout:
        logger.error(f"⏳ [AutoFL] Timeout khi gọi API cho User {user_id}, TikTok '{tiktok_username}'")
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else "N/A"
        logger.error(f"🆘 [AutoFL] Lỗi kết nối/API (Code: {status_code}) cho User {user_id}, TikTok '{tiktok_username}': {e}")
        # Thông báo lỗi cho người dùng (tùy chọn)
        # try:
        #     bot.send_message(user_id, f"❌ Lỗi khi tự động tăng follow cho @{html.escape(tiktok_username)}. API gặp sự cố (Code: {status_code}). Tác vụ vẫn tiếp tục theo lịch.", parse_mode='HTML')
        # except Exception as send_err:
        #      logger.warning(f"⚠️ [AutoFL] Không thể gửi thông báo lỗi cho user {user_id}: {send_err}")
    except Exception as e:
        logger.error(f"🆘 [AutoFL] Lỗi không mong muốn khi chạy tác vụ cho User {user_id}, TikTok '{tiktok_username}': {e}", exc_info=True)

# === Hàm chạy Scheduler trong Thread riêng ===
def _scheduler_loop():
    """Vòng lặp chạy kiểm tra và thực thi các tác vụ đã được lên lịch."""
    logger.info("⏰ Bắt đầu vòng lặp scheduler...")
    while True:
        try:
            with scheduler_lock: # Đảm bảo an toàn khi truy cập schedule
                 schedule.run_pending()
        except Exception as e:
            logger.error(f"🆘 Lỗi trong vòng lặp scheduler: {e}", exc_info=True)
            # Ngủ một chút để tránh vòng lặp lỗi quá nhanh
            time.sleep(5)
        time.sleep(1) # Kiểm tra mỗi giây

# === Các lệnh ADMIN ===
@bot.message_handler(commands=['add'])
@kiem_tra_nhom_cho_phep
def add_vip_command(message: telebot.types.Message):
    user_id, _ = get_user_info_from_message(message)
    if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền sử dụng lệnh này!")
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return bot.reply_to(message, f"❌ Sai cú pháp! Dùng: <code>/add &lt;user_id&gt; [số_ngày]</code>\n(Mặc định là {VIP_DURATION_DAYS} ngày nếu không nhập)")
    try:
        target_user_id = int(args[1])
        duration_days = VIP_DURATION_DAYS
        if len(args) >= 3:
            try:
                duration_days = int(args[2])
                if not (0 < duration_days <= MAX_VIP_DURATION_DAYS):
                    return bot.reply_to(message, f"⚠️ Số ngày VIP phải là một số dương và không quá {MAX_VIP_DURATION_DAYS} ngày.")
            except ValueError:
                return bot.reply_to(message, "⚠️ Số ngày VIP phải là một số nguyên hợp lệ.")
        success, result_data = save_vip_user_to_db(target_user_id, duration_days)
        if success and isinstance(result_data, datetime):
            exp_str = result_data.strftime('%Y-%m-%d %H:%M:%S')
            reply_msg = f"✅✨ Đã cấp/gia hạn VIP thành công <b>{duration_days}</b> ngày cho ID <code>{target_user_id}</code>.\n⏳ Ngày hết hạn mới: <b>{exp_str}</b>."
            bot.reply_to(message, reply_msg)
            try:
                # Cố gắng lấy tên người dùng được add VIP để thông báo
                target_info = get_user_profile_info(target_user_id) # Lấy cả info để có tên
                target_mention = f"ID <code>{target_user_id}</code>"
                try:
                    target_chat = bot.get_chat(target_user_id)
                    target_mention = f"<a href='tg://user?id={target_user_id}'>{html.escape(target_chat.first_name)}</a> (ID: <code>{target_user_id}</code>)"
                except Exception: pass # Không lấy được tên thì dùng ID

                bot.send_message(target_user_id, f"🎉 Chúc mừng! Bạn đã được Admin cấp/gia hạn <b>{duration_days}</b> ngày VIP.\n🗓️ VIP của bạn sẽ hết hạn vào lúc: {exp_str}")
                logger.info(f"👑 Admin {user_id} đã cấp {duration_days} ngày VIP cho user {target_mention}")
            except Exception as e:
                logger.warning(f"⚠️ Không thể gửi tin nhắn thông báo cấp VIP cho user {target_user_id}: {e}")
                bot.reply_to(message, f"ℹ️ Đã cấp VIP thành công nhưng không thể gửi thông báo cho ID <code>{target_user_id}</code> (có thể do họ đã chặn bot hoặc lỗi khác).")
        else:
            bot.reply_to(message, f"❌ Lỗi khi thêm VIP cho ID <code>{target_user_id}</code>: {result_data}")
            logger.error(f"🆘 Admin {user_id} gặp lỗi khi thêm VIP cho {target_user_id}: {result_data}")
    except ValueError:
        bot.reply_to(message, "❌ User ID không hợp lệ. Vui lòng nhập ID dạng số.")
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong lệnh /add: {e}", exc_info=True)
        bot.reply_to(message, "❌ Đã xảy ra lỗi không mong muốn trong quá trình xử lý.")

@bot.message_handler(commands=['xoavip'])
@kiem_tra_nhom_cho_phep
def xoavip_command(message: telebot.types.Message):
    user_id, _ = get_user_info_from_message(message)
    if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền sử dụng lệnh này!")
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return bot.reply_to(message, "❌ Sai cú pháp! Dùng: <code>/xoavip &lt;user_id&gt;</code>")
    try:
        target_user_id = int(args[1])
        deleted = delete_vip_user_from_db(target_user_id)
        if deleted:
            bot.reply_to(message, f"✅🗑️ Đã xóa thành công trạng thái VIP của người dùng ID <code>{target_user_id}</code>.")
            logger.info(f"🗑️ Admin {user_id} đã xóa VIP của user {target_user_id}")
            try:
                bot.send_message(target_user_id, "ℹ️ Trạng thái VIP của bạn đã bị quản trị viên thu hồi.")
            except Exception as e:
                logger.warning(f"⚠️ Không thể gửi tin nhắn thông báo thu hồi VIP cho user {target_user_id}: {e}")
        else:
            bot.reply_to(message, f"ℹ️ Không tìm thấy người dùng VIP với ID <code>{target_user_id}</code> hoặc đã có lỗi xảy ra khi xóa.")
            logger.warning(f"⚠️ Admin {user_id} xóa VIP user {target_user_id} thất bại (không tìm thấy hoặc lỗi DB).")
    except ValueError:
        bot.reply_to(message, "❌ User ID không hợp lệ. Vui lòng nhập ID dạng số.")
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong lệnh /xoavip: {e}", exc_info=True)
        bot.reply_to(message, "❌ Đã xảy ra lỗi không mong muốn trong quá trình xử lý.")

@bot.message_handler(commands=['thongbao'])
@kiem_tra_nhom_cho_phep
def thongbao_command(message: telebot.types.Message):
     user_id, _ = get_user_info_from_message(message)
     if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền!")
     args = message.text.split(maxsplit=1)
     if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, "❌ Vui lòng nhập nội dung thông báo: <code>/thongbao [Nội dung cần gửi]</code>")
     broadcast_message = f"📢 <b>Thông Báo Từ Admin:</b>\n\n{args[1].strip()}"
     game_data = load_game_data_sync()
     user_ids_str = list(game_data.keys())
     if not user_ids_str:
        return bot.reply_to(message, "ℹ️ Không có người dùng nào trong dữ liệu để gửi thông báo.")
     total_users = len(user_ids_str)
     sent_count = 0; failed_count = 0; blocked_count = 0
     logger.info(f"📢 Admin {ADMIN_ID} bắt đầu gửi thông báo đến {total_users} người dùng...")
     confirm_msg = None
     try: confirm_msg = bot.reply_to(message, f"⏳ Chuẩn bị gửi thông báo đến <b>{total_users}</b> người dùng... Vui lòng chờ!")
     except Exception as e: logger.error(f"🆘 Lỗi gửi tin nhắn xác nhận /thongbao: {e}"); return

     def broadcast_thread_func(confirm_msg_obj):
        nonlocal sent_count, failed_count, blocked_count
        for user_id_str in user_ids_str:
            try:
                user_id_int = int(user_id_str)
                bot.send_message(user_id_int, broadcast_message)
                sent_count += 1
                time.sleep(0.1) # Delay nhỏ để tránh rate limit
            except ValueError:
                logger.warning(f"⚠️ Bỏ qua ID không hợp lệ trong /thongbao: {user_id_str}")
                failed_count += 1
            except telebot.apihelper.ApiTelegramException as e:
                error_str = str(e).lower()
                if "forbidden: bot was blocked by the user" in error_str or "chat not found" in error_str or "user is deactivated" in error_str:
                    blocked_count += 1
                    # Tùy chọn: Xóa user đã chặn/deactivate khỏi DB?
                    # delete_vip_user_from_db(user_id_int)
                    # with data_lock:
                    #     game_data_local = load_game_data_sync()
                    #     if user_id_str in game_data_local:
                    #         del game_data_local[user_id_str]
                    #         save_game_data_sync(game_data_local)
                    # logger.info(f"🚫 User {user_id_str} bị chặn/không tồn tại, đã xóa khỏi dữ liệu (nếu có).")
                else:
                    logger.warning(f"⚠️ Lỗi API khi gửi thông báo đến {user_id_str}: {e}")
                    failed_count += 1
                time.sleep(0.5) # Delay lớn hơn nếu gặp lỗi API
            except Exception as e:
                logger.error(f"🆘 Lỗi không xác định khi gửi đến {user_id_str}: {e}", exc_info=True)
                failed_count += 1
                time.sleep(0.5)

        logger.info(f"📢 Thông báo hoàn tất: Thành công={sent_count}, Lỗi={failed_count}, Bị chặn/Không tìm thấy={blocked_count}")
        result_text = (f"✅ <b>Thông báo hoàn tất!</b>\n--------------------------\n✔️ Gửi thành công: <b>{sent_count}</b>\n"
                       f"❌ Gửi thất bại: <b>{failed_count}</b>\n"
                       f"🚫 Bị chặn/Không tìm thấy: <b>{blocked_count}</b>")
        try:
            if confirm_msg_obj:
                 bot.edit_message_text(result_text, chat_id=confirm_msg_obj.chat.id, message_id=confirm_msg_obj.message_id)
            else: # Nếu tin nhắn xác nhận ban đầu bị lỗi
                 bot.send_message(ADMIN_ID, result_text)
        except Exception as edit_e:
            logger.error(f"🆘 Lỗi không thể sửa/gửi tin nhắn kết quả thông báo: {edit_e}")
            bot.send_message(ADMIN_ID, result_text) # Gửi tin nhắn mới cho admin

     broadcast_thread = threading.Thread(target=broadcast_thread_func, args=(confirm_msg,), daemon=True)
     broadcast_thread.start()

@bot.message_handler(commands=['baotri'])
@kiem_tra_nhom_cho_phep
def baotri_command(message: telebot.types.Message):
    global maintenance_mode
    user_id, _ = get_user_info_from_message(message)
    if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền!")
    maintenance_mode = True; logger.info(f"🛠️ Admin {ADMIN_ID} đã BẬT chế độ bảo trì.")
    bot.reply_to(message, "✅🛠️ Đã bật chế độ bảo trì. Chỉ Admin mới có thể dùng lệnh.")

@bot.message_handler(commands=['hoantat'])
@kiem_tra_nhom_cho_phep
def hoantat_command(message: telebot.types.Message):
    global maintenance_mode
    user_id, _ = get_user_info_from_message(message)
    if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền!")
    maintenance_mode = False; logger.info(f"✅ Admin {ADMIN_ID} đã TẮT chế độ bảo trì.")
    bot.reply_to(message, "✅👍 Đã tắt chế độ bảo trì. Bot hoạt động bình thường.")

@bot.message_handler(commands=['cong'])
@kiem_tra_nhom_cho_phep
def cong_command(message: telebot.types.Message):
    user_id, _ = get_user_info_from_message(message)
    if user_id != ADMIN_ID: return bot.reply_to(message, "⛔ Bạn không có quyền!")
    args = message.text.split(); target_user_id = None; amount = None
    if len(args) == 3:
        try:
            target_user_id = int(args[1])
            amount_str = args[2].replace('.', '').replace(',', '') # Xử lý dấu chấm/phẩy
            amount = int(amount_str)
            if amount <= 0:
                return bot.reply_to(message, "❌ Số xu cộng phải là số dương.")
        except ValueError:
            return bot.reply_to(message, "❌ Sai cú pháp hoặc số không hợp lệ.\nDùng: <code>/cong [user_id] [số_xu]</code>")
    else:
        return bot.reply_to(message, "❌ Sai cú pháp! Dùng: <code>/cong [user_id] [số_xu]</code>")

    game_data = load_game_data_sync()
    # Cố gắng lấy tên thật thay vì tên tạm thời
    target_name_temp = "User_" + str(target_user_id)
    try:
        target_chat = bot.get_chat(target_user_id)
        target_name_temp = target_chat.username or f"{target_chat.first_name} {target_chat.last_name or ''}".strip() or f"User_{target_user_id}"
    except Exception:
        pass # Không lấy được tên thì dùng tên tạm

    target_player = get_player_data(target_user_id, target_name_temp, game_data)
    target_player["xu"] += amount
    save_game_data_sync(game_data)
    logger.info(f"💸 Admin {user_id} đã cộng {format_xu(amount)} xu cho {target_player['name']}(ID:{target_user_id}). Số dư mới: {format_xu(target_player['xu'])}")
    bot.reply_to(message, f"✅ Đã cộng thành công <b>{format_xu(amount)}</b> xu cho {html.escape(target_player['name'])} (ID: <code>{target_user_id}</code>).\n💰 Số dư mới của họ: <b>{format_xu(target_player['xu'])}</b> xu.")

@bot.message_handler(commands=['truxu'])
@kiem_tra_nhom_cho_phep
def truxu_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    if user_id != ADMIN_ID:
        return bot.reply_to(message, "⛔ Bạn không có quyền sử dụng lệnh này!")

    logger.warning(f"🚨 Admin {user_id} ({user_name}) đang thực hiện lệnh /truxu!")
    msg_confirm = bot.reply_to(message, "⏳ Đang xử lý trừ hết xu của tất cả người dùng... Vui lòng chờ.")

    try:
        game_data = load_game_data_sync()
        count = 0
        user_ids_affected = []

        for uid_str, player_info in game_data.items():
            # Kiểm tra xem có phải là dict hợp lệ và có key 'xu' không
            if isinstance(player_info, dict) and "xu" in player_info:
                 # Trừ cả admin nếu muốn, nếu không thì thêm: and int(uid_str) != ADMIN_ID
                if player_info["xu"] != 0:
                    player_info["xu"] = 0
                    count += 1
                    user_ids_affected.append(uid_str)

        save_game_data_sync(game_data)
        logger.warning(f"🚨 Admin {user_id} đã trừ hết xu của {count} người dùng.")
        bot.edit_message_text(f"✅ Đã trừ hết xu của <b>{count}</b> người dùng về 0 thành công!",
                              chat_id=msg_confirm.chat.id, message_id=msg_confirm.message_id)

        # Tùy chọn: Gửi thông báo cho những người bị ảnh hưởng (có thể gây spam)
        # for affected_id_str in user_ids_affected:
        #     try:
        #         bot.send_message(int(affected_id_str), "ℹ️ Tài khoản xu của bạn đã được Admin đặt lại về 0.")
        #         time.sleep(0.1)
        #     except Exception:
        #         pass # Bỏ qua nếu không gửi được

    except Exception as e:
        logger.error(f"🆘 Lỗi nghiêm trọng khi thực hiện /truxu: {e}", exc_info=True)
        try:
             bot.edit_message_text(f"❌ Đã xảy ra lỗi khi thực hiện lệnh /truxu: {html.escape(str(e))}",
                                  chat_id=msg_confirm.chat.id, message_id=msg_confirm.message_id)
        except Exception: # Nếu sửa cũng lỗi thì gửi mới
            bot.reply_to(message, f"❌ Đã xảy ra lỗi khi thực hiện lệnh /truxu: {html.escape(str(e))}")

# === Các lệnh Người dùng ===
@bot.message_handler(commands=['start', 'help'])
@kiem_tra_nhom_cho_phep
def start_help_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    game_data = load_game_data_sync()
    player_data = get_player_data(user_id, user_name, game_data)
    save_game_data_sync(game_data) # Lưu lại nếu người dùng mới được tạo

    is_admin = user_id == ADMIN_ID
    is_vip = user_id in allowed_vip_users
    vip_status_line = ""
    if is_vip:
        exp_time = get_vip_expiration_time_from_db(user_id)
        vip_status_line = f"💎 Bạn là thành viên <b>VIP</b>"
        if exp_time:
            vip_status_line += f" (Hết hạn: {exp_time.strftime('%d/%m/%Y %H:%M')})\n"
        else:
            vip_status_line += " (Không rõ hạn)\n"

    # Cập nhật help text với lệnh mới
    help_text = f"""
👋 Chào {user_name}! Số dư của bạn: 💰 <b>{format_xu(player_data['xu'])}</b> xu.
{vip_status_line}
📖✨ <b>Lệnh Người Dùng Thường</b> ✨📖
┣─ /help - ❓ Xem hướng dẫn này
┣─ /muavip - 💎 Hướng dẫn mua/gia hạn VIP
┣─ /plan - 📅 Kiểm tra thời hạn VIP
┣─ /diemdanh - 🎁 Nhận <b>{format_xu(CHECKIN_REWARD)}</b> xu miễn phí mỗi ngày
┣─ /check - 💰 Xem số dư xu
┣─ /play <code>[tài|xỉu] [số_xu|all]</code> - 🎲 Chơi Tài Xỉu
┣─ /baucua <code>[vật] [số_xu|all|10k|1m]</code> - 🦀 Chơi Bầu Cua
┣─ /top - 🏆 Xem Top Đại Gia
┣─ /time - ⏱️ Xem thời gian hoạt động của Bot
┣─ /info <code>[reply/ID]</code> - 👤 Xem thông tin user Telegram
┣─ /qr <code>[nội dung]</code> - █ Tạo mã QR
┣─ /rutgon <code>[link]</code> - 🔗 Rút gọn link URL
┣─ /thoitiet <code>[địa điểm]</code> - 🌦️ Xem thời tiết
┣─ /phim <code>[tên phim]</code> - 🎬 Tìm thông tin phim
┣─ /fl <code>[Username TikTok]</code> - ✨ Tăng follow TikTok (Thử nghiệm)
┣─ /flauto <code>[Username TikTok]</code> - 🤖 Tự động FL ({FLAUTO_INTERVAL_MINUTES}p, tốn {format_xu(FLAUTO_COST)} xu)
┣─ /stopflauto - 🚫 Dừng tự động FL
┗─ /admin - 🧑‍💼 Liên hệ Admin
"""
    if is_vip:
        vip_commands_text = "\n💎👑 <b>Lệnh Đặc Quyền VIP</b> 👑💎\n(Hiện chưa có lệnh VIP nào khác)\n"
        help_text += vip_commands_text
    if is_admin:
        admin_commands_text = f"""
🔒🔑 <b>Lệnh Quản Trị Viên</b> 🔑🔒
┣─ /add <code>[id] [ngày]</code> - ✅ Thêm/Gia hạn VIP
┣─ /xoavip <code>[id]</code> - ❌ Xóa VIP
┣─ /cong <code>[id] [xu]</code> - ➕ Cộng xu
┣─ /truxu - ➖ Trừ hết xu của mọi người về 0
┣─ /thongbao <code>[nội dung]</code> - 📢 Gửi thông báo chung
┣─ /baotri - 🛠️ Bật chế độ bảo trì
┗─ /hoantat - ✅ Tắt chế độ bảo trì
"""
        help_text += admin_commands_text

    help_text += f"\nChúc {user_name} sử dụng bot vui vẻ! 🎉"
    try:
        bot.reply_to(message, help_text, disable_web_page_preview=True)
    except telebot.apihelper.ApiTelegramException as e:
        if "can't parse entities" in str(e):
            logger.error(f"🆘 Vẫn lỗi parse HTML trong /help, gửi dạng text thường: {e}")
            plain_text_help = telebot.util.extract_tags(help_text)
            try:
                bot.reply_to(message, plain_text_help, disable_web_page_preview=True)
            except Exception as plain_e:
                logger.error(f"🆘 Lỗi gửi cả text thường của /help: {plain_e}")
                bot.reply_to(message, "😥 Lỗi hiển thị trợ giúp. Vui lòng thử lại sau.")
        else:
            logger.error(f"🆘 Lỗi API khi gửi /help: {e}")
            bot.reply_to(message, "😥 Đã có lỗi xảy ra khi hiển thị trợ giúp.")
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn khi gửi /help: {e}", exc_info=True)
        bot.reply_to(message, "😥 Đã có lỗi xảy ra khi hiển thị trợ giúp.")

@bot.message_handler(commands=['top'])
@kiem_tra_nhom_cho_phep
def top_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message); logger.info(f"🏆 User {user_id} ({user_name}) yêu cầu xem /top.")
    game_data = load_game_data_sync();
    if not game_data: return bot.reply_to(message, "ℹ️ Hiện tại chưa có dữ liệu người chơi nào để xếp hạng.")
    player_list = []
    for uid_str, p_data in game_data.items():
        if isinstance(p_data, dict) and "xu" in p_data and "name" in p_data:
            # Chuyển xu sang số để sắp xếp đúng
            player_xu = p_data.get("xu", 0)
            if not isinstance(player_xu, (int, float)): player_xu = 0 # Đảm bảo là số
            player_list.append({"id": uid_str, "name": p_data["name"], "xu": player_xu})
        else: logger.warning(f"⚠️ Dữ liệu người chơi không hợp lệ trong /top cho ID {uid_str}: {p_data}")
    if not player_list: return bot.reply_to(message, "ℹ️ Không tìm thấy người chơi hợp lệ nào trong dữ liệu.")
    sorted_players = sorted(player_list, key=lambda p: p["xu"], reverse=True); top_players = sorted_players[:TOP_N]
    reply_lines = [f"🏆✨ <b>BẢNG XẾP HẠNG TOP {len(top_players)} ĐẠI GIA</b> ✨🏆", "---------------------------------"]; ranks_emojis = ["🥇", "🥈", "🥉"]
    for rank, player in enumerate(top_players, 1):
        rank_icon = ranks_emojis[rank-1] if rank <= len(ranks_emojis) else "🏅"
        safe_name = html.escape(player["name"]) # Escape tên người dùng
        formatted_xu = format_xu(player["xu"])
        reply_lines.append(f"{rank_icon} {rank}. {safe_name} - 💰 <b>{formatted_xu}</b> xu")
    reply_text = "\n".join(reply_lines); bot.reply_to(message, reply_text)

@bot.message_handler(commands=['info'])
@kiem_tra_nhom_cho_phep
def info_command(message: telebot.types.Message):
    user_id_to_check = None; args = message.text.split(); requesting_user_id, requesting_user_name = get_user_info_from_message(message)
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
        user_id_to_check = target_user.id
        logger.info(f"ℹ️ User {requesting_user_id} ({requesting_user_name}) yêu cầu /info của user {target_user.id} (qua reply).")
    elif len(args) > 1:
        try:
            user_id_to_check = int(args[1])
            logger.info(f"ℹ️ User {requesting_user_id} ({requesting_user_name}) yêu cầu /info cho ID: {user_id_to_check}.")
        except ValueError:
            return bot.reply_to(message, "❌ ID người dùng không hợp lệ. Nhập ID dạng số hoặc reply tin nhắn.")
    else:
        user_id_to_check = message.from_user.id
        logger.info(f"ℹ️ User {requesting_user_id} ({requesting_user_name}) yêu cầu /info của chính mình.")

    if user_id_to_check:
        info_text = get_user_profile_info(user_id_to_check)
        bot.reply_to(message, info_text, disable_web_page_preview=True)
    else:
        bot.reply_to(message, "❌ Không thể xác định người dùng cần xem thông tin.")

@bot.message_handler(commands=['muavip'])
@kiem_tra_nhom_cho_phep
def muavip_telebot_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    transfer_content = f"NAP VIP {user_id}"
    caption_text = f"""
💎✨ <b>Đăng Ký / Gia Hạn VIP</b> ✨💎
---------------------------------
👤 Người dùng: <b>{user_name}</b> (ID: <code>{user_id}</code>)
✨ Quyền lợi VIP: (Hiện tại chủ yếu để thể hiện, các quyền lợi khác có thể được thêm sau)
💰 Phí dịch vụ: <b>{VIP_PRICE} / {VIP_DURATION_DAYS} ngày</b>
---------------------------------
💳 <b>Thông Tin Thanh Toán:</b>
🏦 Ngân hàng: <b>{BANK_NAME}</b>
🔢 Số tài khoản: <code>{ACCOUNT_NUMBER}</code>
✍️ Tên chủ tài khoản: <b>{ACCOUNT_NAME}</b>
💬 Nội dung CK: <code>{transfer_content}</code> (<b>‼️ QUAN TRỌNG ‼️</b>)
---------------------------------
⚠️ <b>Lưu ý quan trọng:</b>
1️⃣ Chuyển khoản chính xác số tiền và nội dung.
2️⃣ Sau khi CK thành công, <b>chụp lại biên lai</b> giao dịch.
3️⃣ Nhấn nút 'Liên Hệ Admin' và gửi biên lai kèm ID <code>{user_id}</code> của bạn để Admin kích hoạt VIP.
❓ Thắc mắc? Nhấn nút 'Liên Hệ Admin'.
"""
    markup = telebot.types.InlineKeyboardMarkup()
    btn_contact = telebot.types.InlineKeyboardButton(text="👉 Liên Hệ Admin Xác Nhận 👈", url=f"https://t.me/{ADMIN_USERNAME}")
    markup.add(btn_contact)
    try:
        if not QR_CODE_IMAGE_PATH.exists():
            logger.error(f"🆘 Lỗi /muavip: Không tìm thấy ảnh QR tại {QR_CODE_IMAGE_PATH}")
            return bot.reply_to(message, f"❌ Lỗi hệ thống: Không tìm thấy mã QR thanh toán. Vui lòng chuyển khoản thủ công theo thông tin trên và liên hệ Admin (@{ADMIN_USERNAME}) để xác nhận.", reply_markup=markup)

        with open(QR_CODE_IMAGE_PATH, 'rb') as qr_photo:
             bot.send_photo(message.chat.id, photo=qr_photo, caption=caption_text, reply_markup=markup, reply_to_message_id=message.message_id)
        logger.info(f"💎 User {user_id} ({user_name}) đã yêu cầu xem thông tin /muavip.")
    except FileNotFoundError:
        logger.error(f"🆘 Lỗi FileNotFoundError /muavip: Không tìm thấy {QR_CODE_IMAGE_PATH}")
        bot.reply_to(message, f"❌ Lỗi hệ thống: Không tìm thấy file QR. Vui lòng chuyển khoản thủ công theo thông tin trên và liên hệ Admin (@{ADMIN_USERNAME}) để xác nhận.", reply_markup=markup)
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong /muavip: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Đã xảy ra lỗi khi gửi thông tin mua VIP. Vui lòng thử lại hoặc liên hệ Admin (@{ADMIN_USERNAME}).", reply_markup=markup)

@bot.message_handler(commands=['plan'])
@kiem_tra_nhom_cho_phep
def plan_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    expiration_time = get_vip_expiration_time_from_db(user_id)
    now = datetime.now()
    if expiration_time and expiration_time > now:
        remaining_time = expiration_time - now
        days = remaining_time.days
        seconds = remaining_time.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        parts = []
        if days > 0: parts.append(f"<b>{days}</b> ngày")
        if hours > 0: parts.append(f"<b>{hours}</b> giờ")
        if minutes > 0: parts.append(f"<b>{minutes}</b> phút")
        if not parts and seconds > 0 : parts.append(f"<b>{seconds}</b> giây") # Chỉ hiển thị giây nếu không có ngày/giờ/phút
        time_str = ", ".join(parts) if parts else "sắp hết hạn"
        exp_str_formatted = expiration_time.strftime('%H:%M:%S ngày %d/%m/%Y')
        reply_text = (f"👑 {user_name}, bạn đang là thành viên <b>VIP</b>.\n"
                      f"🗓️ Thời gian còn lại: ~{time_str}\n"
                      f"⏳ Hết hạn vào lúc: {exp_str_formatted}")
        bot.reply_to(message, reply_text)
        logger.info(f"ℹ️ User {user_id} ({user_name}) kiểm tra /plan: VIP còn hạn đến {exp_str_formatted}")
    elif expiration_time and expiration_time <= now:
        exp_str_formatted = expiration_time.strftime('%d/%m/%Y')
        reply_text = f"😥 {user_name}, gói VIP của bạn đã hết hạn vào ngày {exp_str_formatted}. Hãy dùng <code>/muavip</code> để gia hạn nhé!"
        bot.reply_to(message, reply_text)
        logger.info(f"ℹ️ User {user_id} ({user_name}) kiểm tra /plan: VIP đã hết hạn {exp_str_formatted}.")
    else:
        reply_text = f"ℹ️ {user_name}, bạn chưa phải là VIP. Dùng <code>/muavip</code> để xem hướng dẫn đăng ký nha."
        bot.reply_to(message, reply_text)
        logger.info(f"ℹ️ User {user_id} ({user_name}) kiểm tra /plan: Chưa phải VIP.")

@bot.message_handler(commands=['check'])
@kiem_tra_nhom_cho_phep
def check_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    game_data = load_game_data_sync()
    player_data = get_player_data(user_id, user_name, game_data) # Không cần save lại vì chỉ đọc
    bot.reply_to(message, f"💰 {user_name}, số dư của bạn là: <b>{format_xu(player_data['xu'])}</b> xu.")

@bot.message_handler(commands=['diemdanh'])
@kiem_tra_nhom_cho_phep
def diemdanh_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    today_str = date.today().isoformat()
    game_data = load_game_data_sync()
    player_data = get_player_data(user_id, user_name, game_data)

    if player_data.get("last_checkin_date") == today_str:
        return bot.reply_to(message, f"🗓️ {user_name}, bạn đã điểm danh hôm nay rồi. Mai lại ghé nhé! 😉")

    # Phần thưởng đã được lấy từ CHECKIN_REWARD đã thay đổi
    player_data["xu"] += CHECKIN_REWARD
    player_data["last_checkin_date"] = today_str
    save_game_data_sync(game_data)

    logger.info(f"🎁 User {user_id} ({user_name}) thực hiện /diemdanh (+{CHECKIN_REWARD}). Ngày: {today_str}")
    bot.reply_to(message, f"✅ Điểm danh ngày {date.today().strftime('%d/%m/%Y')} thành công!\n🎁 Bạn nhận được <b>{format_xu(CHECKIN_REWARD)}</b> xu.\n💰 Số dư mới: <b>{format_xu(player_data['xu'])}</b> xu. Tuyệt vời! ✨")

@bot.message_handler(commands=['time'])
@kiem_tra_nhom_cho_phep
def time_command(message: telebot.types.Message):
    now = datetime.now()
    uptime_delta = now - start_time
    total_seconds = int(uptime_delta.total_seconds())
    days, seconds_remaining = divmod(total_seconds, 86400)
    hours, seconds_remaining = divmod(seconds_remaining, 3600)
    minutes, seconds = divmod(seconds_remaining, 60)
    uptime_parts = []
    if days > 0: uptime_parts.append(f"{days} ngày")
    if hours > 0: uptime_parts.append(f"{hours} giờ")
    if minutes > 0: uptime_parts.append(f"{minutes} phút")
    if seconds > 0 or not uptime_parts: uptime_parts.append(f"{seconds} giây")
    uptime_str = ", ".join(uptime_parts)
    bot.reply_to(message, f"⏱️ Bot đã hoạt động được: <b>{uptime_str}</b>.")
    logger.info(f"ℹ️ User {message.from_user.id} ({get_user_info_from_message(message)[1]}) yêu cầu /time.")

@bot.message_handler(commands=['play'])
@kiem_tra_nhom_cho_phep
def play_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split()[1:]
    if len(args) != 2:
        return bot.reply_to(message, "❌ Sai cú pháp! Ví dụ:\n<code>/play tài 10000</code>\n<code>/play xỉu all</code>")

    choice = args[0].lower()
    bet_input = args[1].lower()

    if choice not in ["tài", "xỉu"]:
        return bot.reply_to(message, "❌ Lựa chọn không hợp lệ. Chọn <b>tài</b> hoặc <b>xỉu</b> nha!")

    command_name = 'play'
    current_time = time.time()
    user_last_cmd_times = last_command_time.setdefault(user_id, {})
    last_play_time = user_last_cmd_times.get(command_name, 0)

    # Kiểm tra cooldown
    if current_time - last_play_time < PLAY_COOLDOWN:
        wait_time = round(PLAY_COOLDOWN - (current_time - last_play_time), 1)
        msg_wait = bot.reply_to(message, f"⏳ Chơi chậm lại chút nào! Vui lòng chờ <b>{wait_time} giây</b> nữa nha.")
        # Xóa tin nhắn chờ và tin nhắn lệnh gốc sau khi hết cooldown
        delete_message_after_delay(message.chat.id, msg_wait.message_id, wait_time + 1)
        delete_message_after_delay(message.chat.id, message.message_id, wait_time + 1)
        return

    game_data = load_game_data_sync()
    player_data = get_player_data(user_id, user_name, game_data)
    current_xu = player_data.get("xu", 0)
    bet_amount = 0

    if bet_input == "all":
        if current_xu <= 0:
            return bot.reply_to(message, f"😥 Hết xu rồi! Hãy /diemdanh để nhận thêm nhé!")
        bet_amount = current_xu
    else:
        try:
            bet_amount_str = bet_input.replace('.', '').replace(',', '') # Xử lý dấu chấm/phẩy
            bet_amount = int(bet_amount_str)
            if bet_amount <= 0:
                return bot.reply_to(message, "⚠️ Số tiền cược phải lớn hơn 0.")
        except ValueError:
            return bot.reply_to(message, "⚠️ Tiền cược không hợp lệ. Nhập số hoặc 'all'.")

    if bet_amount > current_xu:
        return bot.reply_to(message, f"😥 Không đủ <b>{format_xu(bet_amount)}</b> xu để cược. Bạn có: <b>{format_xu(current_xu)}</b> xu thôi.")

    logger.info(f"🎲 User {user_id} ({user_name}) /play: Cược {format_xu(bet_amount)} xu vào '{choice}'.")

    # Trừ tiền trước khi quay
    player_data["xu"] -= bet_amount
    player_data["plays"] = player_data.get("plays", 0) + 1
    user_last_cmd_times[command_name] = current_time # Cập nhật thời gian chơi cuối

    # Quay số
    dice, total, result = roll_dice_sync()
    dice_str = ' + '.join(map(str, dice))
    is_win = (choice == result)
    net_gain = 0
    jackpot_hit = False
    jackpot_win_amount = 0

    if is_win:
        # Thắng: Trả lại tiền cược + tiền thắng (đã trừ house edge)
        win_amount = round(bet_amount * (1 - (HOUSE_EDGE_PERCENT / 100.0)))
        net_gain = win_amount # Số tiền thực nhận thêm (không tính tiền cược gốc)
        player_data["xu"] += bet_amount + net_gain # Hoàn tiền cược + tiền thắng

        # Kiểm tra Jackpot
        if random.randint(1, JACKPOT_CHANCE_ONE_IN) == 1:
            jackpot_hit = True
            jackpot_win_amount = JACKPOT_AMOUNT
            player_data["xu"] += jackpot_win_amount
            logger.info(f"💥 JACKPOT! User {user_id} ({user_name}) trúng {format_xu(jackpot_win_amount)} xu!")
    else:
        # Thua: Đã trừ tiền cược ở trên rồi
        net_gain = -bet_amount

    save_game_data_sync(game_data)

    result_icon = "🎯" if is_win else "💥"
    result_text_bold = f"<b>Thắng Lớn</b> 🎉" if is_win else f"<b>Thua Rồi</b> 😥"
    msg = (f"🎲 <b>Kết Quả Tài Xỉu</b> 🎲\n--------------------------\n"
           f"👤 Người chơi: {user_name}\n"
           f"👇 Bạn chọn: <b>{choice.capitalize()}</b>\n"
           f"🎲 Kết quả: {dice_str} = {total} (<b>{result.capitalize()}</b>)\n"
           f"--------------------------\n"
           f"{result_icon} Bạn đã {result_text_bold}!\n")

    if is_win:
        msg += f"✨ Thắng: <b>+{format_xu(net_gain)}</b> xu\n"
    if jackpot_hit:
        msg += f"<b>💎??💎 NỔ HŨ JACKPOT!!! +{format_xu(jackpot_win_amount)} xu 💎💎💎</b>\n"
    if not is_win: # Chỉ hiển thị mất tiền nếu thua
        msg += f"💸 Mất: <b>{format_xu(abs(net_gain))}</b> xu\n"

    msg += f"💰 Số dư mới: <b>{format_xu(player_data['xu'])}</b> xu."
    bot.reply_to(message, msg)
    logger.info(f"Game Result /play: User:{user_id}, Dice:{dice}, Total:{total}, Result:{result}, Choice:{choice}, Bet:{bet_amount}, Win:{is_win}, Net:{net_gain}, Jackpot:{jackpot_hit}, NewBalance:{player_data['xu']}")

@bot.message_handler(commands=['baucua'])
@kiem_tra_nhom_cho_phep
def baucua_telebot_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split()[1:]
    valid_items_str = ", ".join([f"{BAUCUA_ICONS.get(item, '')}<code>{item}</code>" for item in BAUCUA_ITEMS])
    if len(args) != 2:
        return bot.reply_to(message, f"❌ Sai cú pháp! Ví dụ:\n<code>/baucua cua 10000</code>\n<code>/baucua bầu all</code>\n<code>/baucua tôm 10k</code>\nCác vật phẩm: {valid_items_str}")

    choice = args[0].lower()
    bet_input = args[1].lower()

    if choice not in BAUCUA_ITEMS:
        valid_items_str_code = ", ".join([f"<code>{item}</code>" for item in BAUCUA_ITEMS])
        return bot.reply_to(message, f"❌ Vật phẩm '<code>{html.escape(choice)}</code>' không hợp lệ!\nChọn: {valid_items_str_code}")

    command_name = 'baucua'
    current_time = time.time()
    user_last_cmd_times = last_command_time.setdefault(user_id, {})
    last_baucua_time = user_last_cmd_times.get(command_name, 0)

    if current_time - last_baucua_time < BAUCUA_COOLDOWN:
        wait_time = round(BAUCUA_COOLDOWN - (current_time - last_baucua_time), 1)
        msg_wait = bot.reply_to(message, f"⏳ Từ từ nào! Chờ <b>{wait_time} giây</b> nữa mới chơi tiếp được.")
        delete_message_after_delay(message.chat.id, msg_wait.message_id, wait_time + 1)
        delete_message_after_delay(message.chat.id, message.message_id, wait_time + 1)
        return

    game_data = load_game_data_sync()
    player_data = get_player_data(user_id, user_name, game_data)
    current_xu = player_data.get("xu", 0)
    bet_amount = 0
    multiplier = 1

    if bet_input != 'all':
        bet_str_num = bet_input
        if bet_input.endswith('k'):
            multiplier = 1000
            bet_str_num = bet_input[:-1]
        elif bet_input.endswith('m'):
            multiplier = 1000000
            bet_str_num = bet_input[:-1]
        try:
            bet_amount_str = bet_str_num.replace('.', '').replace(',', '') # Xử lý dấu chấm/phẩy
            bet_amount = int(bet_amount_str) * multiplier
            if bet_amount <= 0:
                return bot.reply_to(message, "⚠️ Số tiền cược phải lớn hơn 0.")
        except ValueError:
            return bot.reply_to(message, "⚠️ Tiền cược không hợp lệ. Nhập số, 'all', hoặc dạng 10k, 1m.")
    elif bet_input == 'all':
         if current_xu <= 0:
             return bot.reply_to(message, f"😥 Hết xu rồi! Hãy /diemdanh để nhận thêm nhé!")
         bet_amount = current_xu
    else: # Trường hợp nhập linh tinh khác 'all' và số
        return bot.reply_to(message, "⚠️ Tiền cược không hợp lệ.")

    if bet_amount > current_xu:
        return bot.reply_to(message, f"😥 Không đủ <b>{format_xu(bet_amount)}</b> xu. Bạn chỉ có: <b>{format_xu(current_xu)}</b> xu.")

    logger.info(f"🦀 User {user_id} ({user_name}) /baucua: Cược {format_xu(bet_amount)} xu vào '{choice}'.")

    # Trừ tiền trước
    player_data["xu"] -= bet_amount
    user_last_cmd_times[command_name] = current_time # Cập nhật thời gian

    # Quay bầu cua
    results = roll_baucua_sync()
    results_icons = [BAUCUA_ICONS.get(item, item) for item in results]
    results_str_icons = " ".join(results_icons)
    results_str_text = ', '.join(results)
    match_count = results.count(choice)
    net_gain = 0

    if match_count > 0:
        # Thắng: Hoàn tiền cược + tiền thắng (tiền cược * số lần xuất hiện)
        win_multiplier = match_count
        net_gain = bet_amount * win_multiplier # Tiền thắng thêm
        player_data["xu"] += bet_amount + net_gain # Hoàn cược + tiền thắng
    else:
        # Thua: Đã trừ tiền ở trên
        net_gain = -bet_amount

    save_game_data_sync(game_data)

    result_icon = "🎯" if match_count > 0 else "💥"
    result_text_bold = f"<b>Thắng</b> 🎉" if match_count > 0 else f"<b>Thua</b> 😥"
    choice_icon = BAUCUA_ICONS.get(choice, choice)

    msg = (f"🦀 <b>Kết Quả Bầu Cua</b> 🦐\n--------------------------\n"
           f"👤 Người chơi: {user_name}\n"
           f"👇 Bạn chọn: {choice_icon} (<code>{choice}</code>)\n"
           f"🎲 Kết quả: {results_str_icons} ({results_str_text})\n"
           f"--------------------------\n"
           f"{result_icon} Bạn đã {result_text_bold}!\n")

    if match_count > 0:
        msg += f"✨ Thắng: <b>+{format_xu(net_gain)}</b> xu (xuất hiện {match_count} lần)\n"
    else:
        msg += f"💸 Mất: <b>{format_xu(abs(bet_amount))}</b> xu\n"

    msg += f"💰 Số dư mới: <b>{format_xu(player_data['xu'])}</b> xu."
    bot.reply_to(message, msg)
    logger.info(f"Game Result /baucua: User:{user_id}, Results:{results}, Choice:{choice}, Bet:{bet_amount}, Matches:{match_count}, Net:{net_gain}, NewBalance:{player_data['xu']}")

@bot.message_handler(commands=['qr'])
@kiem_tra_nhom_cho_phep
def qr_command(message: telebot.types.Message):
     user_id, user_name = get_user_info_from_message(message)
     text_to_encode = message.text.split(maxsplit=1)
     if len(text_to_encode) < 2 or not text_to_encode[1].strip():
         return bot.reply_to(message, "❌ Vui lòng nhập nội dung cần tạo mã QR.\nVí dụ: <code>/qr Nội dung cần mã hóa</code>")

     content = text_to_encode[1].strip()
     logger.info(f"█ User {user_id} ({user_name}) yêu cầu tạo QR: '{content[:50]}...'")
     try:
        qr = qrcode.QRCode( version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4 )
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        # Lưu vào buffer bộ nhớ thay vì file tạm
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0) # Đưa con trỏ về đầu buffer

        safe_caption_content = html.escape(content)
        max_caption_len = 200 # Giới hạn caption để tránh lỗi Telegram
        if len(safe_caption_content) > max_caption_len:
            safe_caption_content = safe_caption_content[:max_caption_len] + "..."

        bot.send_photo(message.chat.id, photo=img_byte_arr, caption=f"✨ Đây là mã QR của bạn cho:\n<code>{safe_caption_content}</code>", reply_to_message_id=message.message_id)
        logger.info(f"✅ Đã tạo và gửi QR thành công cho user {user_id}.")
     except Exception as e:
        logger.error(f"🆘 Lỗi khi tạo hoặc gửi mã QR cho user {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Đã xảy ra lỗi khi tạo mã QR: {html.escape(str(e))}")

@bot.message_handler(commands=['rutgon'])
@kiem_tra_nhom_cho_phep
def rutgon_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, "❌ Vui lòng cung cấp link URL muốn rút gọn.\nVí dụ: <code>/rutgon https://example.com/long/link</code>")

    url_to_shorten = args[1].strip()
    # Kiểm tra URL cơ bản
    if not url_to_shorten.lower().startswith(('http://', 'https://')):
        return bot.reply_to(message, "❌ Link không hợp lệ. Phải bắt đầu bằng <code>http://</code> hoặc <code>https://</code>.")

    logger.info(f"🔗 User {user_id} ({user_name}) yêu cầu rút gọn link: {url_to_shorten}")
    api_url = "https://cleanuri.com/api/v1/shorten"
    payload = {'url': url_to_shorten}
    waiting_msg = bot.reply_to(message, "⏳ Đang rút gọn link, chờ chút...")

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status() # Ném lỗi nếu status code là 4xx hoặc 5xx
        result = response.json()

        if "error" in result:
            error_msg = result["error"]
            logger.error(f"❌ Lỗi từ API cleanuri khi rút gọn '{url_to_shorten}': {error_msg}")
            bot.edit_message_text(f"❌ Lỗi từ dịch vụ rút gọn: {html.escape(error_msg)}", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
            return

        short_url = result.get("result_url")
        if short_url:
            reply_text = (f"🔗 Link gốc: {html.escape(url_to_shorten)}\n"
                          f"✨ Link rút gọn: {short_url}")
            bot.edit_message_text(reply_text, chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id, disable_web_page_preview=True)
            logger.info(f"✅ Đã rút gọn link '{url_to_shorten}' thành '{short_url}' cho user {user_id}")
        else:
            logger.error(f"❌ API cleanuri không trả về 'result_url' cho '{url_to_shorten}'. Phản hồi: {result}")
            bot.edit_message_text("❌ Lỗi không xác định từ dịch vụ rút gọn (không có result_url).", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)

    except requests.exceptions.Timeout:
        logger.error(f"⏳ Timeout khi gọi API cleanuri cho link: {url_to_shorten}")
        bot.edit_message_text("⏳ Yêu cầu rút gọn link bị quá thời gian. Thử lại sau.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except requests.exceptions.RequestException as e:
        logger.error(f"🆘 Lỗi kết nối API cleanuri: {e}", exc_info=True)
        error_detail = f" (Code: {e.response.status_code})" if e.response is not None else ""
        bot.edit_message_text(f"❌ Lỗi kết nối dịch vụ rút gọn link{error_detail}.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except json.JSONDecodeError:
        logger.error(f"🆘 Lỗi giải mã JSON từ API cleanuri: {url_to_shorten}")
        bot.edit_message_text("❌ Lỗi xử lý phản hồi từ dịch vụ rút gọn (JSON không hợp lệ).", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong /rutgon: {e}", exc_info=True)
        try:
            bot.edit_message_text("❌ Đã xảy ra lỗi không mong muốn.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        except Exception: # Nếu sửa tin nhắn cũng lỗi
             bot.reply_to(message, "❌ Đã xảy ra lỗi không mong muốn.")

@bot.message_handler(commands=['thoitiet'])
@kiem_tra_nhom_cho_phep
def weather_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split(maxsplit=1)

    if not WEATHER_API_KEY or WEATHER_API_KEY == "YOUR_OPENWEATHERMAP_API_KEY":
        logger.warning(f"⚠️ User {user_id} dùng /thoitiet nhưng API key chưa cấu hình.")
        return bot.reply_to(message, "⚠️ Tính năng thời tiết chưa được cấu hình. Liên hệ Admin.")

    if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, "❌ Vui lòng nhập tên thành phố/địa điểm.\nVí dụ: <code>/thoitiet Hà Nội</code>")

    location = args[1].strip()
    logger.info(f"🌦️ User {user_id} ({user_name}) yêu cầu thời tiết tại: '{location}'")
    base_url = "http://api.openweathermap.org/data/2.5/weather?"
    # units=metric để lấy độ C, lang=vi để lấy mô tả tiếng Việt
    complete_url = base_url + "appid=" + WEATHER_API_KEY + "&q=" + location + "&units=metric&lang=vi"
    waiting_msg = bot.reply_to(message, f"⏳ Đang lấy thông tin thời tiết cho <code>{html.escape(location)}</code>...")

    try:
        response = requests.get(complete_url, timeout=10)
        response.raise_for_status()
        weather_data = response.json()

        # Kiểm tra mã phản hồi từ API (ví dụ: 404 Not Found)
        if weather_data.get("cod") != 200:
            error_message = weather_data.get("message", "Lỗi không xác định từ API")
            logger.error(f"❌ Lỗi từ API OpenWeatherMap (mã {weather_data.get('cod')}) cho '{location}': {error_message}")
            reply_error = f"❌ Lỗi từ dịch vụ thời tiết: {html.escape(error_message)}"
            if "city not found" in error_message.lower():
                 reply_error = f"❌ Không tìm thấy địa điểm '<code>{html.escape(location)}</code>'."
            bot.edit_message_text(reply_error, chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
            return

        main = weather_data.get("main", {})
        weather_desc_list = weather_data.get("weather", [{}])
        weather_desc = weather_desc_list[0] if weather_desc_list else {}
        wind = weather_data.get("wind", {})
        sys_info = weather_data.get("sys", {})

        city_name = weather_data.get("name", location)
        country = sys_info.get("country", "")
        temp = main.get("temp", "N/A")
        feels_like = main.get("feels_like", "N/A")
        humidity = main.get("humidity", "N/A")
        description = weather_desc.get("description", "Không rõ").capitalize()
        icon_code = weather_desc.get("icon")
        wind_speed = wind.get("speed", "N/A") # m/s

        # Mapping icon codes to emojis (có thể mở rộng thêm)
        weather_icons = {
            "01d": "☀️", "01n": "🌙", "02d": "🌤️", "02n": "☁️",
            "03d": "☁️", "03n": "☁️", "04d": "🌥️", "04n": "☁️",
            "09d": "🌧️", "09n": "🌧️", "10d": "🌦️", "10n": "🌧️",
            "11d": "⛈️", "11n": "⛈️", "13d": "❄️", "13n": "❄️",
            "50d": "🌫️", "50n": "🌫️"
        }
        icon_emoji = weather_icons.get(icon_code, "❓") # Emoji mặc định nếu không có icon

        reply_text = (
            f"{icon_emoji} <b>Thời tiết tại {html.escape(city_name)}, {country}</b>\n"
            f"---------------------------------\n"
            f"🌡️ Nhiệt độ: <b>{temp}°C</b> (Cảm giác như: {feels_like}°C)\n"
            f"💧 Độ ẩm: <b>{humidity}%</b>\n"
            f"🌬️ Gió: <b>{wind_speed} m/s</b>\n"
            f"📝 Mô tả: <b>{html.escape(description)}</b>"
        )
        bot.edit_message_text(reply_text, chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        logger.info(f"✅ Đã gửi thời tiết '{location}' tới user {user_id}")

    except requests.exceptions.Timeout:
        logger.error(f"⏳ Timeout khi gọi API OpenWeatherMap cho: {location}")
        bot.edit_message_text("⏳ Yêu cầu thời tiết bị quá thời gian. Thử lại sau.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except requests.exceptions.RequestException as req_err:
        logger.error(f"🆘 Lỗi kết nối API OpenWeatherMap: {req_err}", exc_info=True)
        error_detail = f" (Code: {req_err.response.status_code})" if req_err.response is not None else ""
        bot.edit_message_text(f"❌ Lỗi kết nối dịch vụ thời tiết{error_detail}.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except json.JSONDecodeError:
        logger.error(f"🆘 Lỗi giải mã JSON từ API OpenWeatherMap: '{location}'")
        bot.edit_message_text("❌ Lỗi xử lý dữ liệu thời tiết (JSON không hợp lệ).", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except IndexError: # Trường hợp list 'weather' rỗng
        logger.error(f"🆘 IndexError khi xử lý dữ liệu thời tiết '{location}'.")
        bot.edit_message_text("❌ Lỗi dữ liệu thời tiết không đầy đủ từ API.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong /thoitiet '{location}': {e}", exc_info=True)
        try:
             bot.edit_message_text("❌ Đã xảy ra lỗi không mong muốn.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        except Exception:
             bot.reply_to(message, "❌ Đã xảy ra lỗi không mong muốn.")

@bot.message_handler(commands=['phim'])
@kiem_tra_nhom_cho_phep
def movie_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split(maxsplit=1)

    if not TMDB_API_KEY or TMDB_API_KEY == "YOUR_TMDB_API_KEY":
        logger.warning(f"⚠️ User {user_id} dùng /phim nhưng API key TMDb chưa cấu hình.")
        return bot.reply_to(message, "⚠️ Tính năng tìm phim chưa được cấu hình. Liên hệ Admin.")

    if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, "❌ Vui lòng nhập tên phim bạn muốn tìm.\nVí dụ: <code>/phim Inception</code>")

    query = args[1].strip()
    logger.info(f"🎬 User {user_id} ({user_name}) tìm kiếm phim: '{query}'")
    waiting_msg = bot.reply_to(message, f"⏳ Đang tìm phim '<code>{html.escape(query)}</code>'...")

    search_url = f"https://api.themoviedb.org/3/search/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "language": "vi-VN", # Ưu tiên tiếng Việt
        "include_adult": False
    }

    try:
        # Bước 1: Tìm kiếm phim
        response_search = requests.get(search_url, params=params, timeout=15)
        response_search.raise_for_status()
        search_results = response_search.json()

        # Nếu không có kết quả tiếng Việt, thử tiếng Anh
        if not search_results.get("results"):
            logger.info(f"Không tìm thấy '{query}' (vi), thử tiếng Anh.")
            params["language"] = "en-US"
            response_search = requests.get(search_url, params=params, timeout=15)
            response_search.raise_for_status()
            search_results = response_search.json()

            if not search_results.get("results"):
                bot.edit_message_text(f"❌ Không tìm thấy phim nào khớp với '<code>{html.escape(query)}</code>'.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
                return

        # Lấy phim đầu tiên trong kết quả
        movie = search_results["results"][0]
        movie_id = movie.get("id")

        if not movie_id:
            logger.error(f"❌ Kết quả tìm phim '{query}' không chứa ID. Data: {movie}")
            bot.edit_message_text(f"❌ Lỗi dữ liệu khi tìm phim '<code>{html.escape(query)}</code>'.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
            return

        # Bước 2: Lấy chi tiết phim bằng ID (ưu tiên tiếng Việt)
        details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
        details_params = {
            "api_key": TMDB_API_KEY,
            "language": "vi-VN",
            "append_to_response": "credits" # Lấy thông tin đạo diễn, diễn viên
        }
        details = None
        try:
            details_response_vn = requests.get(details_url, params=details_params, timeout=15)
            if details_response_vn.status_code == 200:
                 details_vn = details_response_vn.json()
                 # Kiểm tra xem có tiêu đề tiếng Việt không, nếu có thì dùng
                 if details_vn.get("title"):
                     details = details_vn
                     logger.info(f"✅ Lấy chi tiết phim '{query}' (ID: {movie_id}) tiếng Việt.")
        except requests.exceptions.RequestException as detail_err_vn:
             logger.warning(f"⚠️ Lỗi khi lấy chi tiết phim TV ID {movie_id}: {detail_err_vn}")

        # Nếu không lấy được tiếng Việt hoặc không có tiêu đề TV, thử tiếng Anh
        if not details:
            logger.info(f"Không có chi tiết TV hoặc lỗi cho ID {movie_id}, thử tiếng Anh.")
            details_params["language"] = "en-US"
            details_response_en = requests.get(details_url, params=details_params, timeout=15)
            details_response_en.raise_for_status() # Ném lỗi nếu tiếng Anh cũng lỗi
            details = details_response_en.json()
            logger.info(f"✅ Lấy chi tiết phim '{query}' (ID: {movie_id}) tiếng Anh.")

        # Trích xuất thông tin từ 'details'
        title = details.get("title", "N/A")
        original_title = details.get("original_title", "")
        tagline = details.get("tagline", "")
        overview = details.get("overview", "Không có mô tả.")
        release_date_str = details.get("release_date", "N/A") # YYYY-MM-DD
        runtime = details.get("runtime") # Phút
        genres_list = details.get("genres", [])
        genres = ", ".join([g["name"] for g in genres_list]) if genres_list else "N/A"
        rating = details.get("vote_average", 0)
        vote_count = details.get("vote_count", 0)
        poster_path = details.get("poster_path") # Chỉ là phần cuối URL
        homepage = details.get("homepage")

        # Lấy đạo diễn và diễn viên từ 'credits'
        director = "N/A"
        actors_list = []
        crew = details.get("credits", {}).get("crew", [])
        cast = details.get("credits", {}).get("cast", [])

        for member in crew:
            if member.get("job") == "Director":
                director = member.get("name", "N/A")
                break
        if cast:
            actors_list = [a.get("name", "") for a in cast[:5] if a.get("name")] # Lấy tối đa 5 diễn viên
        actors = ", ".join(actors_list) if actors_list else "N/A"

        # Định dạng lại thông tin
        runtime_str = "N/A"
        if isinstance(runtime, int) and runtime > 0:
            hours, minutes = divmod(runtime, 60)
            runtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        rating_str = "Chưa đánh giá"
        if vote_count > 0 and isinstance(rating, (float, int)) and rating > 0:
            rating_str = f"{rating:.1f}/10 ({vote_count:,} lượt)"

        release_date_formatted = release_date_str
        try:
            if release_date_str and release_date_str != "N/A":
                release_dt = datetime.strptime(release_date_str, '%Y-%m-%d')
                release_date_formatted = release_dt.strftime('%d/%m/%Y')
        except ValueError:
            pass # Giữ nguyên định dạng gốc nếu không parse được

        # Escape HTML cho an toàn
        safe_title = html.escape(title)
        safe_original_title = f"<i>({html.escape(original_title)})</i>" if original_title and original_title != title else ""
        safe_tagline = f"<i>“{html.escape(tagline)}”</i>" if tagline else ""
        safe_genres = html.escape(genres)
        safe_director = html.escape(director)
        safe_actors = html.escape(actors)
        safe_overview = html.escape(overview or 'Chưa có mô tả.')
        max_overview_length = 350 # Giới hạn độ dài mô tả
        if len(safe_overview) > max_overview_length:
            safe_overview = safe_overview[:max_overview_length] + "..."

        caption = (
            f"🎬 <b>{safe_title}</b> {safe_original_title}\n{safe_tagline}\n"
            f"---------------------------------\n"
            f"⭐️ Đánh giá: <b>{rating_str}</b>\n"
            f"🗓️ Phát hành: {release_date_formatted}\n"
            f"⏱️ Thời lượng: {runtime_str}\n"
            f"🎭 Thể loại: {safe_genres}\n"
            f"🎬 Đạo diễn: {safe_director}\n"
            f"👥 Diễn viên: {safe_actors}\n"
            f"---------------------------------\n"
            f"📝 <b>Nội dung:</b>\n{safe_overview}"
        )
        if homepage:
            caption += f"\n\n🔗 Trang chủ: {homepage}"

        # Xóa tin nhắn chờ
        try:
            bot.delete_message(chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        except Exception:
            pass # Bỏ qua nếu không xóa được

        # Gửi kết quả kèm poster nếu có
        if poster_path:
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
            try:
                # Giới hạn caption cho ảnh (Telegram giới hạn 1024 ký tự)
                max_caption_length = 1024
                if len(caption) > max_caption_length:
                    caption = caption[:max_caption_length-25] + "...\n(Nội dung bị cắt bớt)"

                bot.send_photo( message.chat.id, photo=poster_url, caption=caption, reply_to_message_id=message.message_id)
                logger.info(f"✅ Gửi phim '{title}' kèm poster cho user {user_id}")
            except Exception as img_err:
                logger.warning(f"⚠️ Lỗi gửi ảnh poster phim '{title}': {img_err}. Gửi dạng văn bản.")
                # Gửi lại dưới dạng text nếu gửi ảnh lỗi
                bot.reply_to(message, caption, disable_web_page_preview=True)
        else:
            # Gửi text nếu không có poster
            bot.reply_to(message, caption, disable_web_page_preview=True)
            logger.info(f"✅ Gửi phim '{title}' (không poster) cho user {user_id}")

    except requests.exceptions.Timeout:
        logger.error(f"⏳ Timeout khi gọi API TMDb cho phim: {query}")
        bot.edit_message_text("⏳ Yêu cầu tìm phim bị quá thời gian.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except requests.exceptions.RequestException as req_err:
        logger.error(f"🆘 Lỗi kết nối API TMDb: {req_err}", exc_info=True)
        error_detail = f" (Code: {req_err.response.status_code})" if req_err.response is not None else ""
        bot.edit_message_text(f"❌ Lỗi kết nối dịch vụ tìm phim{error_detail}.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except json.JSONDecodeError:
        logger.error(f"🆘 Lỗi giải mã JSON từ API TMDb: '{query}'")
        bot.edit_message_text("❌ Lỗi xử lý dữ liệu phim (JSON không hợp lệ).", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
    except IndexError: # Nếu search_results["results"] rỗng sau cả 2 lần thử
        logger.warning(f"⚠️ IndexError khi xử lý kết quả tìm phim '{query}'.")
        # Tin nhắn này đã được xử lý ở phần kiểm tra results rỗng
        pass
    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong /phim '{query}': {e}", exc_info=True)
        try:
            bot.edit_message_text("❌ Đã xảy ra lỗi không mong muốn.", chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        except Exception:
             bot.reply_to(message, "❌ Đã xảy ra lỗi không mong muốn.")

@bot.message_handler(commands=['fl'])
@kiem_tra_nhom_cho_phep
def follow_tiktok_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, "❌ Sai cú pháp! Vui lòng nhập username TikTok.\nVí dụ: <code>/fl tiktokusername</code>")

    tiktok_username = args[1].strip().replace('@', '') # Xóa @ nếu người dùng nhập vào
    if not tiktok_username:
        return bot.reply_to(message, "❌ Username TikTok không được để trống.")

    # Tạo URL API động
    api_url = f"{TIKTOK_FL_API_BASE_URL}?user={tiktok_username}&userid={user_id}&tokenbot={BOT_TOKEN}"

    logger.info(f"📲 User {user_id} ({user_name}) yêu cầu /fl cho TikTok: '{tiktok_username}'")
    waiting_msg = bot.reply_to(message, f"⏳ Đang gửi yêu cầu tăng follow cho <code>{html.escape(tiktok_username)}</code>... Phép thuật đang diễn ra ✨")

    api_success = False
    api_response_text = "Không có phản hồi cụ thể."
    error_message_detail = None

    try:
        response = requests.get(api_url, timeout=25) # Tăng timeout lên 25s
        response.raise_for_status() # Kiểm tra lỗi HTTP (4xx, 5xx)
        api_success = True
        api_response_text = response.text # Lưu lại text phản hồi để debug
        logger.info(f"✅ API Response /fl cho User {user_id}, TikTok '{tiktok_username}': {api_response_text}")

        # Thông báo thành công chung chung vì không biết API trả về gì cụ thể
        success_reply = f"""✨ <b>Yêu cầu Tăng Follow TikTok</b> ✨\n➖➖➖➖➖➖➖➖➖➖➖\n💬 Trạng thái: <code>Yêu cầu đã được gửi đi!</code>\n✅ Kết quả: <b>Thành công</b> (Theo phản hồi từ API)\n👤 Tên Telegram: {user_name}\n🆔 ID Telegram: <code>{user_id}</code>\n🔗 TikTok User: <code>@{html.escape(tiktok_username)}</code>\n➖➖➖➖➖➖➖➖➖➖➖\nℹ️ <i>Lưu ý: Thời gian follow tăng có thể thay đổi tùy thuộc vào hệ thống.</i>"""
        bot.edit_message_text(success_reply, chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)

    except requests.exceptions.Timeout:
        logger.error(f"⏳ Timeout khi gọi API TikTok FL cho user {user_id}, TikTok '{tiktok_username}'")
        error_message_detail = "Yêu cầu tới API bị quá thời gian chờ."
    except requests.exceptions.RequestException as e:
        status_code = "N/A"
        if e.response is not None:
            status_code = e.response.status_code
            api_response_text = e.response.text # Lưu lại text lỗi từ API
            logger.error(f"🆘 Lỗi kết nối/API TikTok FL (Code: {status_code}) cho user {user_id}, TikTok '{tiktok_username}': {e}. Response: {api_response_text[:500]}")
            # Cố gắng phân tích lỗi JSON nếu có
            try:
                error_json = e.response.json()
                if 'message' in error_json: error_message_detail = html.escape(error_json['message'])
                elif 'error' in error_json: error_message_detail = html.escape(error_json['error'])
                else: error_message_detail = f"Lỗi HTTP {status_code} từ API."
            except json.JSONDecodeError:
                 error_message_detail = f"Lỗi HTTP {status_code} từ API (Phản hồi không phải JSON)."
        else:
            logger.error(f"🆘 Lỗi kết nối API TikTok FL (Không có phản hồi) cho user {user_id}, TikTok '{tiktok_username}': {e}", exc_info=True)
            error_message_detail = f"Lỗi kết nối mạng hoặc không nhận được phản hồi từ API."

    except Exception as e:
        logger.error(f"🆘 Lỗi không mong muốn trong /fl cho user {user_id}, TikTok '{tiktok_username}': {e}", exc_info=True)
        error_message_detail = "Lỗi không xác định trong quá trình xử lý."

    # Xử lý nếu API không thành công
    if not api_success:
        failure_reply = f"""❌ <b>Yêu cầu Tăng Follow TikTok Thất Bại</b> ❌\n➖➖➖➖➖➖➖➖➖➖➖\n💬 Thông báo: <code>{error_message_detail or 'Không rõ nguyên nhân.'}</code>\n📉 Trạng thái: <b>Thất bại</b>\n👤 Tên Telegram: {user_name}\n🆔 ID Telegram: <code>{user_id}</code>\n🔗 TikTok User: <code>@{html.escape(tiktok_username)}</code>\n➖➖➖➖➖➖➖➖➖➖➖\nℹ️ <i>Vui lòng thử lại sau hoặc liên hệ Admin nếu lỗi tiếp diễn.</i>"""
        try:
            bot.edit_message_text(failure_reply, chat_id=waiting_msg.chat.id, message_id=waiting_msg.message_id)
        except Exception as edit_err:
            logger.error(f"🆘 Không thể sửa tin nhắn báo lỗi /fl: {edit_err}")
            # Gửi tin nhắn mới nếu không sửa được
            bot.send_message(waiting_msg.chat.id, failure_reply, reply_to_message_id=message.message_id)

    # Log lại kết quả cuối cùng và phản hồi thô (nếu có) để debug
    logger.info(f"Kết quả /fl: Success={api_success}, User={user_id}, TikTok='{tiktok_username}'. Raw Response (nếu có): {api_response_text}")

@bot.message_handler(commands=['flauto'])
@kiem_tra_nhom_cho_phep
def flauto_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)
    args = message.text.split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        return bot.reply_to(message, f"❌ Sai cú pháp! Vui lòng nhập username TikTok.\nVí dụ: <code>/flauto tiktokusername</code>\nChi phí: <b>{format_xu(FLAUTO_COST)} xu</b>.\nChạy mỗi: <b>{FLAUTO_INTERVAL_MINUTES} phút</b>.")

    tiktok_username = args[1].strip().replace('@', '')
    if not tiktok_username:
        return bot.reply_to(message, "❌ Username TikTok không được để trống.")

    with scheduler_lock: # Khóa để kiểm tra và thêm tác vụ an toàn
        if user_id in auto_follow_tasks:
            active_task = auto_follow_tasks[user_id]
            next_run_time = "Không xác định"
            try:
                # Cố gắng lấy thời gian chạy tiếp theo
                if hasattr(active_task['job'], 'next_run'):
                    next_run_dt = active_task['job'].next_run
                    if next_run_dt:
                         next_run_time = next_run_dt.strftime('%H:%M:%S %d/%m/%Y')
            except Exception as time_err:
                logger.warning(f"Không thể lấy next_run cho job của user {user_id}: {time_err}")

            return bot.reply_to(message, f"⚠️ Bạn đã có một tác vụ tự động tăng follow đang chạy cho <b>@{html.escape(active_task['tiktok_username'])}</b>.\n(Dự kiến chạy lần tới: {next_run_time})\nDùng lệnh <code>/stopflauto</code> để hủy trước khi tạo cái mới.")

        # Kiểm tra tiền trước khi lock data
        temp_game_data = load_game_data_sync()
        temp_player_data = get_player_data(user_id, user_name, temp_game_data) # Chỉ đọc, chưa sửa
        if temp_player_data['xu'] < FLAUTO_COST:
            return bot.reply_to(message, f"😥 Bạn không đủ <b>{format_xu(FLAUTO_COST)}</b> xu để kích hoạt.\nBạn đang có: <b>{format_xu(temp_player_data['xu'])}</b> xu.")
        del temp_game_data # Giải phóng bộ nhớ tạm

        # Nếu đủ tiền, thực hiện trừ tiền và lên lịch
        game_data = load_game_data_sync()
        player_data = get_player_data(user_id, user_name, game_data)
        player_data['xu'] -= FLAUTO_COST
        save_game_data_sync(game_data)
        logger.info(f"💸 User {user_id} ({user_name}) đã chi {FLAUTO_COST} xu để kích hoạt /flauto cho @{tiktok_username}.")

        # Lên lịch tác vụ
        job = schedule.every(FLAUTO_INTERVAL_MINUTES).minutes.do(_run_auto_follow, user_id=user_id, tiktok_username=tiktok_username)
        auto_follow_tasks[user_id] = {'tiktok_username': tiktok_username, 'job': job}

        bot.reply_to(message, f"✅ Đã kích hoạt tự động tăng follow cho <b>@{html.escape(tiktok_username)}</b> mỗi <b>{FLAUTO_INTERVAL_MINUTES} phút</b>.\n💰 Đã trừ <b>{format_xu(FLAUTO_COST)}</b> xu. Số dư mới: <b>{format_xu(player_data['xu'])}</b> xu.\nDùng <code>/stopflauto</code> để hủy.")
        logger.info(f"⏰ [AutoFL] Đã lên lịch tác vụ cho User {user_id}, TikTok '{tiktok_username}', Interval: {FLAUTO_INTERVAL_MINUTES} phút.")

@bot.message_handler(commands=['stopflauto'])
@kiem_tra_nhom_cho_phep
def stop_flauto_command(message: telebot.types.Message):
    user_id, user_name = get_user_info_from_message(message)

    with scheduler_lock: # Khóa để kiểm tra và hủy tác vụ an toàn
        if user_id not in auto_follow_tasks:
            return bot.reply_to(message, "ℹ️ Bạn không có tác vụ tự động tăng follow nào đang chạy.")

        try:
            task_info = auto_follow_tasks[user_id]
            tiktok_username = task_info['tiktok_username']
            job_to_cancel = task_info['job']

            schedule.cancel_job(job_to_cancel)
            del auto_follow_tasks[user_id]

            logger.info(f"🛑 [AutoFL] User {user_id} ({user_name}) đã hủy tác vụ tự động cho @{tiktok_username}.")
            bot.reply_to(message, f"✅ Đã hủy tác vụ tự động tăng follow cho <b>@{html.escape(tiktok_username)}</b> thành công.")

        except Exception as e:
            logger.error(f"🆘 Lỗi khi hủy tác vụ /stopflauto cho user {user_id}: {e}", exc_info=True)
            bot.reply_to(message, "❌ Đã xảy ra lỗi khi cố gắng hủy tác vụ của bạn.")

@bot.message_handler(commands=['admin'])
@kiem_tra_nhom_cho_phep
def admin_contact_command(message: telebot.types.Message):
     user_id, user_name = get_user_info_from_message(message)
     bot.reply_to(message, f"🧑‍💼 Cần hỗ trợ? Liên hệ quản trị viên ngay: @{ADMIN_USERNAME} ✨")
     logger.info(f"ℹ️ User {user_id} ({user_name}) yêu cầu thông tin liên hệ admin.")

# === Khởi chạy Bot ===
def main():
    logger.info("--- 🚀 Bot đang khởi tạo 🚀 ---")
    initialize_vip_database()
    load_vip_users_from_db()
    _ = load_game_data_sync() # Load data game ban đầu để đảm bảo file tồn tại/hợp lệ

    # <<< THÊM MỚI - Khởi chạy luồng cho scheduler >>>
    scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Luồng Scheduler đã được khởi chạy.")
    # <<< KẾT THÚC PHẦN THÊM MỚI >>>

    logger.info(f"🔒 Bot sẽ chỉ hoạt động trong nhóm ID: {ALLOWED_GROUP_ID}")
    logger.info(f"🔑 Bot Token: ...{BOT_TOKEN[-6:]}")
    logger.info(f"👑 Admin ID: {ADMIN_ID} | Admin Username: @{ADMIN_USERNAME}")
    logger.info(f"💰 Điểm danh: {format_xu(CHECKIN_REWARD)} xu | AutoFL Cost: {format_xu(FLAUTO_COST)} xu")
    logger.info(f"💾 Game Data File: {DATA_FILE_PATH}")
    logger.info(f"💎 VIP DB File: {DB_FILE_PATH}")
    logger.info(f"💳 VIP QR Code Image: {QR_CODE_IMAGE_PATH}")
    logger.info(f"✨ TikTok FL API: {TIKTOK_FL_API_BASE_URL}")
    logger.info(f"⏰ Bot bắt đầu chạy lúc: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("--- 🎉 Bot đã sẵn sàng tung hoành! 🎉 ---")
    try:
        bot.infinity_polling(logger_level=logging.INFO, skip_pending=True) # Đổi level log polling thành INFO
    except Exception as e:
        logger.critical(f"‼️🆘 LỖI NGHIÊM TRỌNG KHIẾN BOT DỪNG HOẠT ĐỘNG: {e}", exc_info=True)
    finally:
        logger.info("--- Bot đang dừng... Hẹn gặp lại! 👋 ---")
        # <<< THÊM MỚI - Dọn dẹp schedule khi dừng bot >>>
        schedule.clear()
        logger.info("🧹 Đã xóa tất cả các tác vụ schedule đang chờ.")
        # <<< KẾT THÚC PHẦN THÊM MỚI >>>
        logger.info("--- Bot đã dừng hoàn toàn ---")

if __name__ == '__main__':
    # Nhắc nhở cài đặt thư viện cần thiết
    try:
        import schedule
    except ImportError:
        print("Lỗi: Thư viện 'schedule' chưa được cài đặt.")
        print("Vui lòng chạy: pip install schedule")
        exit() # Thoát nếu chưa cài

    main()
