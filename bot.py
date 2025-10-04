# bot.py — Telegram Stars premium + admin-only panel
import os, json, time, datetime, threading
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ====== КОНФИГ ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set (Render → Settings → Environment)")

# цена премиума в звёздах и длительность (можно менять из админки)
STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))  # 100⭐ по умолчанию
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))  # 30 дней

# --- список админов (только они видят/могут админку) ---
def _parse_admins():
    ids = set()
    if os.getenv("ADMIN_ID"):
        try: ids.add(int(os.getenv("ADMIN_ID")))
        except: pass
    if os.getenv("ADMIN_IDS"):
        for x in os.getenv("ADMIN_IDS").split(","):
            x = x.strip()
            if x.isdigit(): ids.add(int(x))
    if not ids:
        ids.add(123456789)  # <-- замени на свой ID или задай ADMIN_ID на Render
    return ids
ADMIN_IDS = _parse_admins()
def is_admin(user_id:int) -> bool:
    return user_id in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "users.json"   # локальная БД (Render хранит файл между рестартами)
# структура:
# {
#   "<uid>": {"joined":ts,"premium":bool,"premium_until":ts},
#   "__payments__":[{"uid":..., "stars":int, "ts":ts, "payload":str}]
# }

# ====== БД ======
def _load():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f: f.write("{}")
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db, uid:int):
    s = str(uid)
    if s not in db:
        db[s] = {"joined": int(time.time()), "premium": False, "premium_until": 0}
    return db[s]

def set_premium(uid:int, days:int):
    db = _load()
    u = get_user(db, uid)
    now = int(time.time())
    base = u["premium_until"] if u["premium_until"] > now else now
    u["premium_until"] = base + days * 86400
    u["premium"] = True
    db[str(uid)] = u
    _save(db)

def has_premium(uid:int) -> bool:
    db = _load(); u = db.get(str(uid))
    if not u: return False
    if u["premium"] and u["premium_until"] > int(time.time()):
        return True
    # авто-сброс просроченного
    if u["premium"] and u["premium_until"] <= int(time.time()):
        u["premium"] = False
        db[str(uid)] = u
        _save(db)
    return False

def log_payment(uid:int, stars:int, payload:str):
    db = _load()
    db.setdefault("__payments__", []).append({
        "uid": uid, "stars": int(stars), "ts": int(time.time()), "payload": payload
    })
    _save(db)

def get_current_price() -> int:
    # читаем «живую» цену из ENV (можно менять из админки без перезапуска)
    try: return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except: return STAR_PRICE_PREMIUM_DEFAULT

# ====== МЕНЮ ======
def main_menu(user_id:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("📅 Меню на неделю (премиум)")
    if user_id and is_admin(user_id):
        kb.row(KeyboardButton("👨‍💻 Админка"))
    return kb
# ====== ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ======
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); get_user(db, m.from_user.id); _save(db)
    print(f"/start from {m.from_user.id} @{m.from_user.username}")
    bot.send_message(
        m.chat.id,
        "Привет! 🤖\nЯ принимаю оплату в Telegram Stars (XTR).\n"
        "Премиум открывает доп. функции на 30 дней.\n\n👇 Главное меню:",
        reply_markup=main_menu(m.from_user.id)
    )

@bot.message_handler(func=lambda m: m.text == "📊 Проверить премиум")
def check_premium(m):
    if has_premium(m.from_user.id):
        db = _load(); u = db.get(str(m.from_user.id), {})
        exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "⭐ Купить премиум")
def buy_premium(m):
    price = get_current_price()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_premium_stars"))
    bot.send_message(m.chat.id,
        f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\n"
        f"Цена: {price} ⭐",
        reply_markup=kb
    )

# пример платной фичи (пока заглушка — покажет, что премиум активен)
@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю (премиум)"])
def week_menu_feature(m):
    if not has_premium(m.from_user.id):
        bot.reply_to(m, "🔒 Эта функция доступна только с премиумом. Нажми «⭐ Купить премиум».",
                     reply_markup=main_menu(m.from_user.id))
        return
    bot.reply_to(m, "🧠 Здесь будет генерация меню на неделю (доступ открыт).",
                 reply_markup=main_menu(m.from_user.id))

# ====== АДМИНКА (видна только тебе) ======
@bot.message_handler(func=lambda m: m.text in ("👨‍💻 Админка", "/admin"))
def admin_panel(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Доступ запрещён.", reply_markup=main_menu(m.from_user.id))
        return
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
        InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_premiums")
    )
    kb.row(
        InlineKeyboardButton("➕ Выдать премиум (ID)", callback_data="adm_grant"),
        InlineKeyboardButton("➖ Снять премиум (ID)", callback_data="adm_revoke")
    )
    kb.row(
        InlineKeyboardButton("💰 Доход (локальный лог)", callback_data="adm_income"),
        InlineKeyboardButton("💵 Изм. цену (звёзды)", callback_data="adm_price")
    )
    bot.send_message(m.chat.id, "🔧 Админ-панель", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "⛔ Нет доступа.")
        return
    db = _load()
    if c.data == "adm_users":
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db.keys() if k!='__payments__'])}</b>")
    elif c.data == "adm_premiums":
        now = int(time.time())
        active = sum(1 for u in db.values() if isinstance(u, dict) and u.get("premium") and u.get("premium_until",0) > now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data == "adm_income":
        pays = db.get("__payments__", [])
        total = sum(p["stars"] for p in pays)
        cnt = len(pays)
        bot.send_message(c.message.chat.id, f"💰 Локально зафиксировано: <b>{total} ⭐</b> ({cnt} оплат)")
    elif c.data == "adm_grant":
        bot.send_message(c.message.chat.id, "Отправь: `<user_id> [дни]` (без скобок).", parse_mode=None)
        bot.register_next_step_handler(c.message, admin_grant_step)
    elif c.data == "adm_revoke":
        bot.send_message(c.message.chat.id, "Отправь: `<user_id>` для снятия премиума.", parse_mode=None)
        bot.register_next_step_handler(c.message, admin_revoke_step)
    elif c.data == "adm_price":
        bot.send_message(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число (например 150):", parse_mode=None)
        bot.register_next_step_handler(c.message, admin_price_step)

def admin_grant_step(m):
    if not is_admin(m.from_user.id): return
    try:
        parts = m.text.strip().split()
        uid = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else PREMIUM_DAYS
        set_premium(uid, days)
        bot.reply_to(m, f"✅ Выдан премиум пользователю <code>{uid}</code> на {days} дн.",
                     reply_markup=main_menu(m.from_user.id))
        try: bot.send_message(uid, f"✅ Вам выдан премиум на {days} дней администратором.")
        except: pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))

def admin_revoke_step(m):
    if not is_admin(m.from_user.id): return
    try:
        uid = int(m.text.strip())
        db = _load(); u = db.get(str(uid))
        if not u: raise ValueError("Пользователь не найден")
        u["premium"] = False; u["premium_until"] = 0
        db[str(uid)] = u; _save(db)
        bot.reply_to(m, f"✅ Снят премиум у <code>{uid}</code>.", reply_markup=main_menu(m.from_user.id))
        try: bot.send_message(uid, "❌ Ваш премиум был снят администратором.")
        except: pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))

def admin_price_step(m):
    if not is_admin(m.from_user.id): return
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)  # обновим «на лету»
        bot.reply_to(m, f"✅ Новая цена установлена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
# ====== ОПЛАТА TELEGRAM STARS (XTR) ======
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]  # amount = кол-во звёзд
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",      # Stars не требуют токена
        currency="XTR",         # очень важно: XTR = Telegram Stars
        prices=prices,
        is_flexible=False
    )
    bot.answer_callback_query(c.id)

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    try:
        bot.answer_pre_checkout_query(q.id, ok=True)
    except Exception as e:
        print("pre_checkout error:", e)

@bot.message_handler(content_types=['successful_payment'])
def on_paid(m):
    try:
        sp = m.successful_payment
        payload = sp.invoice_payload or ""
        total = getattr(sp, "total_amount", None)  # в Stars часто равно числу звёзд
        if payload.startswith("premium_stars:"):
            set_premium(m.from_user.id, PREMIUM_DAYS)
            if total: log_payment(m.from_user.id, total, payload)
            db = _load(); u = db.get(str(m.from_user.id), {})
            exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
            bot.send_message(
                m.from_user.id,
                f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.",
                reply_markup=main_menu(m.from_user.id)
            )
        else:
            if total: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.from_user.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ====== АВТО-ПЕРЕЗАПУСК РАЗ В СУТКИ (на случай зависаний) ======
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)  # Render перезапустит процесс

threading.Thread(target=auto_restart, daemon=True).start()

# ====== ЗАПУСК ======
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
