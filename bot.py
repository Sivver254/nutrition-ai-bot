# ============ bot.py (Часть 1/3) ============
import os, json, time, datetime, threading
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ---------- КОНФИГ ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set (Render → Settings → Environment)")

STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))  # 100⭐
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))  # 30 дней

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
        ids.add(123456789)  # ← поменяй на свой ID или задай ADMIN_ID
    return ids

ADMIN_IDS = _parse_admins()
def is_admin(uid:int) -> bool:
    return uid in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "users.json"

# ---------- Хранилище пользовательских шагов (анкеты/админка/и т.п.) ----------
USER_FLOW = {}
def reset_flow(uid:int):
    USER_FLOW.pop(uid, None)

# ---------- БД ----------
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
    try: return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except: return STAR_PRICE_PREMIUM_DEFAULT

# ---------- Клавиатуры ----------
def main_menu(user_id:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("📅 Меню на неделю"))   # без слова «премиум», ограничим после анкеты
    if user_id and is_admin(user_id):
        kb.row(KeyboardButton("👨‍💻 Админка"))
    return kb
    # ============ bot.py (Часть 2/3) ============

# ---------- Пользовательские команды ----------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); get_user(db, m.from_user.id); _save(db)
    bot.send_message(
        m.chat.id,
        "Привет! 🤖 Я помогу оценить КБЖУ из списка/фото и сгенерирую меню на неделю.\n"
        "Премиум открывает все функции на 30 дней.\n\n👇 Главное меню:",
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
    bot.send_message(
        m.chat.id,
        f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐",
        reply_markup=kb
    )

# Меню на неделю — сначала анкета, а после анкеты проверим премиум
@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def week_menu_entry(m):
    USER_FLOW[m.from_user.id] = {"step": "anketa_goal"}
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"), KeyboardButton("Набор массы"))
    kb.row(KeyboardButton("⬅️ Назад"))
    bot.reply_to(m, "Выбери цель:", reply_markup=kb)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "anketa_goal")
def anketa_goal(m):
    if m.text == "⬅️ Назад":
        reset_flow(m.from_user.id)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))
        return
    USER_FLOW[m.from_user.id] = {"step": "anketa_params", "goal": m.text}
    bot.reply_to(m, "Пришли рост (см) и вес (кг) через пробел, например: <b>178 74</b>")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "anketa_params")
def anketa_params(m):
    try:
        h, w = m.text.replace(",", ".").split()
        h = int(float(h)); w = float(w)
        USER_FLOW[m.from_user.id]["height"] = h
        USER_FLOW[m.from_user.id]["weight"] = w
    except:
        bot.reply_to(m, "Не понял. Пример: <b>178 74</b>")
        return
    # тут проверим премиум
    if not has_premium(m.from_user.id):
        reset_flow(m.from_user.id)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(f"Купить премиум за {get_current_price()} ⭐", callback_data="buy_premium_stars"))
        bot.reply_to(m, "🔒 Меню доступно только с премиумом. Оформи ниже:", reply_markup=kb)
        return
    # Заглушка генерации меню
    g = USER_FLOW[m.from_user.id]["goal"]
    h = USER_FLOW[m.from_user.id]["height"]
    w = USER_FLOW[m.from_user.id]["weight"]
    reset_flow(m.from_user.id)
    bot.reply_to(m,
        f"🧠 Генерирую меню на неделю…\nЦель: <b>{g}</b>\nРост: {h} см, вес: {w} кг\n\n"
        "🍽 Здесь будет расписание на 7 дней.",
        reply_markup=main_menu(m.from_user.id)
    )

# ---------- Оплата Telegram Stars ----------
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]
    bot.answer_callback_query(c.id)  # гасим “часик”
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",      # Stars не требуют токена
        currency="XTR",
        prices=prices,
        is_flexible=False
    )

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def on_paid(m):
    sp = m.successful_payment
    payload = sp.invoice_payload or ""
    total = getattr(sp, "total_amount", None)
    if payload.startswith("premium_stars:"):
        set_premium(m.from_user.id, PREMIUM_DAYS)
        if total: log_payment(m.from_user.id, total, payload)
        db = _load(); u = db.get(str(m.from_user.id), {})
        exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
        bot.send_message(m.from_user.id, f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.",
                         reply_markup=main_menu(m.from_user.id))
    else:
        if total: log_payment(m.from_user.id, total, payload)
        bot.send_message(m.from_user.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))

# ---------- АДМИНКА ----------
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
        InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"),
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
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db.keys() if k!='__payments__'])}</b>")

    elif c.data == "adm_premiums":
        bot.answer_callback_query(c.id)
        now = int(time.time())
        active = sum(1 for u in db.values()
                     if isinstance(u, dict) and u.get("premium") and u.get("premium_until",0) > now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")

    elif c.data == "adm_income":
        bot.answer_callback_query(c.id)
        pays = db.get("__payments__", [])
        total = sum(p["stars"] for p in pays)
        cnt = len(pays)
        bot.send_message(c.message.chat.id, f"💰 Локально зафиксировано: <b>{total} ⭐</b> ({cnt} оплат)")

    elif c.data == "adm_grant":
        USER_FLOW[c.from_user.id] = {"step": "adm_grant"}
        bot.answer_callback_query(c.id)
        msg = bot.send_message(
            c.message.chat.id,
            "Отправь: <code>&lt;user_id&gt; [дни]</code>\nПример: <code>123456789 30</code>\n"
            "Чтобы отменить — пришли <b>Отмена</b>.",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, admin_grant_step)

    elif c.data == "adm_revoke":
        USER_FLOW[c.from_user.id] = {"step": "adm_revoke"}
        bot.answer_callback_query(c.id)
        msg = bot.send_message(c.message.chat.id,
                               "Отправь: <code>&lt;user_id&gt;</code> для снятия премиума.",
                               parse_mode="HTML")
        bot.register_next_step_handler(msg, admin_revoke_step)

    elif c.data == "adm_price":
        USER_FLOW[c.from_user.id] = {"step": "adm_price"}
        bot.answer_callback_query(c.id)
        msg = bot.send_message(c.message.chat.id,
                               f"Текущая цена: {get_current_price()} ⭐\n"
                               "Отправь новое число (например <b>150</b>)",
                               parse_mode="HTML")
        bot.register_next_step_handler(msg, admin_price_step)

def admin_grant_step(m):
    if not is_admin(m.from_user.id): return
    if m.text.strip().lower() in ("отмена", "cancel", "назад"):
        reset_flow(m.from_user.id)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))
        return
    try:
        parts = m.text.replace(",", " ").split()
        uid = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else PREMIUM_DAYS
        set_premium(uid, days)
        bot.reply_to(m, f"✅ Выдан премиум <code>{uid}</code> на {days} дн.",
                     reply_markup=main_menu(m.from_user.id))
        try: bot.send_message(uid, f"✅ Вам выдан премиум на {days} дней администратором.")
        except: pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}\nФормат: <code>123456789 30</code>",
                     reply_markup=main_menu(m.from_user.id), parse_mode="HTML")
    finally:
        reset_flow(m.from_user.id)

def admin_revoke_step(m):
    if not is_admin(m.from_user.id): return
    if m.text.strip().lower() in ("отмена", "cancel", "назад"):
        reset_flow(m.from_user.id)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))
        return
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
    finally:
        reset_flow(m.from_user.id)

def admin_price_step(m):
    if not is_admin(m.from_user.id): return
    if m.text.strip().lower() in ("отмена", "cancel", "назад"):
        reset_flow(m.from_user.id)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))
        return
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)  # «на лету»
        bot.reply_to(m, f"✅ Новая цена установлена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
    finally:
        reset_flow(m.from_user.id)
        # ============ bot.py (Часть 3/3) ============

# ---------- Мини-веб для Render (порт-биндинг) ----------
try:
    import flask
    app = flask.Flask(__name__)

    @app.route('/')
    def index():
        return "Bot is running!"

    def run_web():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    threading.Thread(target=run_web, daemon=True).start()
except Exception:
    pass  # если Flask не установлен (на worker не нужен)

# ---------- Авто-перезапуск раз в сутки ----------
def auto_restart():
    while True:
        time.sleep(24 * 3600)
        os._exit(0)  # Render перезапустит процесс

threading.Thread(target=auto_restart, daemon=True).start()

# ---------- Запуск бота ----------
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
        
