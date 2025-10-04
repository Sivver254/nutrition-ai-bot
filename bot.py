# -*- coding: utf-8 -*-
# bot.py — Calories/KBJU bot with Stars-premium + admin, trial and web ping

import os, json, time, datetime, threading
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ====== КОНФИГ ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))  # ⭐
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))

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
        ids.add(123456789)  # замени или задай ADMIN_ID
    return ids
ADMIN_IDS = _parse_admins()
def is_admin(uid: int) -> bool: return uid in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ---- Анти-409 + чистый старт ----
try:
    bot.delete_webhook(drop_pending_updates=True)
except Exception as e:
    print("delete_webhook error:", e)

# блокировка второго процесса в контейнере
try:
    import fcntl
    _lockfile = open("bot.lock", "w")
    fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
except Exception:
    print("Another instance is already running. Exit.")
    import sys; sys.exit(0)

DATA_FILE = "users.json"
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
        db[s] = {"joined": int(time.time()), "premium": False, "premium_until": 0, "trial_started": 0}
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
        u["premium"] = False; _save(db)
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

# ====== Состояния ======
USER_FLOW = {}   # { uid: {"step": "...", ...} }

def set_step(uid, step): USER_FLOW[uid] = {"step": step}
def get_step(uid): return USER_FLOW.get(uid, {}).get("step")
def reset_flow(uid): USER_FLOW.pop(uid, None)

# ====== Общие клавиатуры ======
def main_menu(uid:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("👨‍🍳 Рецепты от ИИ"))
    kb.row(KeyboardButton("📅 Меню на неделю"))
    if uid and is_admin(uid):
        kb.row(KeyboardButton("👨‍💻 Админка"))
    return kb

SERVICE_BUTTONS = {
    "🧾 КБЖУ по списку", "📸 КБЖУ по фото", "📅 Меню на неделю",
    "👨‍🍳 Рецепты от ИИ", "⬅️ Назад", "🏠 В меню",
    "⭐ Купить премиум", "📊 Проверить премиум", "👨‍💻 Админка"
}

# ====== Триал 24 часа для бесплатных фич ======
TRIAL_HOURS = 24
def trial_active(uid:int)->bool:
    db = _load(); u = get_user(db, uid)
    started = u.get("trial_started", 0)
    if not started:
        u["trial_started"] = int(time.time()); _save(db)
        return True
    return (int(time.time()) - started) <= TRIAL_HOURS*3600
    # ====== Старт/помощь ======
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); get_user(db, m.from_user.id); _save(db)
    bot.send_message(
        m.chat.id,
        "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
        "• «📸 КБЖУ по фото» — пришли фото блюда\n"
        "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
        "Также могу подобрать <b>меню на 7 дней</b> под твои параметры — «📅 Меню на неделю».\n"
        "А ещё — «👨‍🍳 Рецепты от ИИ» (в т.ч. «на калории»).\n\n"
        "Премиум открывает доп. функции на 30 дней.",
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
        f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐",
        reply_markup=kb
    )

# ====== Рецепты от ИИ ======
def recipes_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🍳 Рецепт по запросу"))
    kb.row(KeyboardButton("🔥 Рецепт на калории"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

@bot.message_handler(func=lambda m: m.text=="👨‍🍳 Рецепты от ИИ")
def recipes_root(m):
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Выбери режим:", reply_markup=recipes_menu())

@bot.message_handler(func=lambda m: m.text=="🍳 Рецепт по запросу")
def recipe_free_text(m):
    set_step(m.from_user.id, "recipe_free")
    bot.send_message(m.chat.id, "Что хочешь приготовить? Опиши кратко (ингредиенты, кухня и т.п.).")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="recipe_free", content_types=['text'])
def recipe_free_go(m):
    q = m.text.strip()
    # здесь можешь дергать свой ИИ — я даю заглушку
    text = f"Идея рецепта по запросу «{q}»:\n- Шаг 1 ...\n- Шаг 2 ...\nКБЖУ ориентировочно: 520 ккал, Б/Ж/У 28/22/46"
    bot.send_message(m.chat.id, text, reply_markup=recipes_menu())
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: m.text=="🔥 Рецепт на калории")
def recipe_kcal_start(m):
    set_step(m.from_user.id, "recipe_kcal")
    bot.send_message(m.chat.id, "Сколько калорий нужно? (например: 600)")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="recipe_kcal", content_types=['text'])
def recipe_kcal_go(m):
    try:
        kcal = int(m.text.strip())
        # Заглушка — подставь свой генератор
        text = (f"Пример рецепта на ~{kcal} ккал:\n"
                f"- Омлет с овощами и тостом\n- Йогурт без сахара\n\n"
                f"КБЖУ ~{kcal} ккал, Б/Ж/У ≈ 35/20/45")
        bot.send_message(m.chat.id, text, reply_markup=recipes_menu())
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка: {e}", reply_markup=recipes_menu())
    finally:
        reset_flow(m.from_user.id)

# ====== Назад ======
@bot.message_handler(func=lambda m: m.text in ("⬅️ Назад", "🏠 В меню"))
def go_back(m):
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))

# ====== Админка ======
@bot.message_handler(func=lambda m: m.text in ("👨‍💻 Админка", "/admin"))
def admin_panel(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Доступ запрещён.", reply_markup=main_menu(m.from_user.id))
        return
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
           InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_premiums"))
    kb.row(InlineKeyboardButton("➕ Выдать премиум (ID)", callback_data="adm_grant"),
           InlineKeyboardButton("➖ Снять премиум (ID)", callback_data="adm_revoke"))
    kb.row(InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"),
           InlineKeyboardButton("💵 Изм. цену (звёзды)", callback_data="adm_price"))
    bot.send_message(m.chat.id, "🛠 Админ-панель", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "⛔ Нет доступа."); return
    db = _load()
    if c.data == "adm_users":
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db.keys() if k!='__payments__'])}</b>")
    elif c.data == "adm_premiums":
        now = int(time.time())
        active = sum(1 for u in db.values() if isinstance(u, dict) and u.get("premium") and u.get("premium_until",0) > now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data == "adm_income":
        pays = db.get("__payments__", [])
        total = sum(p.get("stars", 0) for p in pays)
        cnt = len(pays)
        bot.send_message(c.message.chat.id, f"💰 Локально зафиксировано: <b>{total} ⭐</b> ({cnt} оплат)")
    elif c.data == "adm_grant":
        set_step(c.from_user.id, "adm_grant")
        bot.send_message(c.message.chat.id, "Отправь: `<user_id> [дни]` (без скобок).", parse_mode=None)
    elif c.data == "adm_revoke":
        set_step(c.from_user.id, "adm_revoke")
        bot.send_message(c.message.chat.id, "Отправь: `<user_id>` для снятия премиума.", parse_mode=None)
    elif c.data == "adm_price":
        set_step(c.from_user.id, "adm_price")
        bot.send_message(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число (например 150):", parse_mode=None)

@bot.message_handler(func=lambda m: get_step(m.from_user.id) in ("adm_grant","adm_revoke","adm_price"), content_types=['text'])
def admin_next_steps(m):
    step = get_step(m.from_user.id)
    try:
        if step == "adm_grant":
            parts = m.text.strip().split()
            uid = int(parts[0]); days = int(parts[1]) if len(parts) > 1 else PREMIUM_DAYS
            set_premium(uid, days)
            bot.reply_to(m, f"✅ Выдан премиум пользователю <code>{uid}</code> на {days} дн.",
                         reply_markup=main_menu(m.from_user.id))
            try: bot.send_message(uid, f"✅ Вам выдан премиум на {days} дней администратором.")
            except: pass
        elif step == "adm_revoke":
            uid = int(m.text.strip())
            db = _load(); u = db.get(str(uid))
            if not u: raise ValueError("Пользователь не найден")
            u["premium"] = False; u["premium_until"] = 0
            db[str(uid)] = u; _save(db)
            bot.reply_to(m, f"✅ Снят премиум у <code>{uid}</code>.", reply_markup=main_menu(m.from_user.id))
            try: bot.send_message(uid, "❌ Ваш премиум был снят администратором.")
            except: pass
        elif step == "adm_price":
            new_price = int(m.text.strip())
            os.environ["STAR_PRICE_PREMIUM"] = str(new_price)
            bot.reply_to(m, f"✅ Новая цена установлена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
    finally:
        reset_flow(m.from_user.id)
        # ====== КБЖУ по списку ======
def parse_items(text:str):
    """
    Формат: 'Курица 150 г; Рис 180 г; Салат 120 г'
    Возвращает список словарей [{"name":..., "gram":...}, ...]
    """
    items = []
    for part in text.split(";"):
        part = part.strip()
        if not part: continue
        # пытаемся выделить граммы в конце
        gram = None
        for token in (" г", " гр", " грамм", " граммов"):
            if part.lower().endswith(token):
                # взять последнее число
                nums = [s for s in part.split() if s.isdigit()]
                if nums: gram = int(nums[-1])
                name = part[: -len(token)].strip()
                break
        if gram is None:
            # без единиц: последнее число — граммы
            tokens = part.split()
            if tokens and tokens[-1].isdigit():
                gram = int(tokens[-1]); name = " ".join(tokens[:-1])
            else:
                # не продукт — вероятно, служебная кнопка
                raise ValueError(f"Не понял позицию: «{part}»")
        items.append({"name": name, "gram": gram})
    return items

def kbju_stub(items):
    # Заглушка — тут твоя логика/БД/ИИ
    # вернем приблизительные макросы
    kcal = sum(int(it["gram"]*1.2) for it in items)
    p = sum(int(it["gram"]*0.15/10) for it in items)
    f = sum(int(it["gram"]*0.08/10) for it in items)
    c = sum(int(it["gram"]*0.2/10) for it in items)
    return kcal, p, f, c

@bot.message_handler(func=lambda m: m.text=="🧾 КБЖУ по списку")
def kbju_list_start(m):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "kbju_list")
    bot.send_message(m.chat.id, "Пришли список в формате: «Продукт 120 г; ...». Пример:\n"
                                "Кур. грудка 150 г; Рис 180 г; Салат 120 г")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="kbju_list", content_types=['text'])
def kbju_list_calc(m):
    try:
        items = parse_items(m.text)
        kcal, p, f, c = kbju_stub(items)
        lines = [f"• {it['name']} — {it['gram']} г" for it in items]
        ans = "Ваш список:\n" + "\n".join(lines) + f"\n\nИтого: {kcal} ккал — Б/Ж/У {p}/{f}/{c}"
        bot.send_message(m.chat.id, ans, reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка: {e}\nПопробуй ещё раз.", reply_markup=main_menu(m.from_user.id))
    finally:
        reset_flow(m.from_user.id)

# ====== КБЖУ по фото ======
@bot.message_handler(func=lambda m: m.text=="📸 КБЖУ по фото")
def kbju_photo_hint(m):
    bot.send_message(m.chat.id, "Пришли фото блюда крупным планом. В первый раз доступ открыт на 24 часа (пробный).")

@bot.message_handler(content_types=['photo'])
def kbju_photo(m):
    uid = m.from_user.id
    if not (has_premium(uid) or trial_active(uid)):
        bot.reply_to(m, "🔒 Анализ по фото доступен с премиумом.\nНажми «⭐ Купить премиум».",
                     reply_markup=main_menu(uid))
        return
    # Здесь вставь свою нейросеть/распознавание — я возвращаю заглушку
    bot.reply_to(m, "🧠 Распознал блюда на фото и оценил КБЖУ (пример): ~520 ккал, Б/Ж/У 32/18/50",
                 reply_markup=main_menu(uid))

# ====== Меню на неделю (анкета) ======
def questionnaire_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(KeyboardButton("Мужчина"), KeyboardButton("Женщина"))
    kb.row(KeyboardButton("Цель: похудение"), KeyboardButton("Цель: поддержание"), KeyboardButton("Цель: набор"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

@bot.message_handler(func=lambda m: m.text=="📅 Меню на неделю")
def week_menu_start(m):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "menu_gender")
    bot.send_message(m.chat.id, "Начнём анкету. Укажи пол:", reply_markup=questionnaire_kb())

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="menu_gender")
def week_menu_gender(m):
    g = m.text.strip().lower()
    if g not in ("мужчина","женщина"):
        bot.send_message(m.chat.id, "Выбери кнопку «Мужчина» или «Женщина».", reply_markup=questionnaire_kb()); return
    USER_FLOW[m.from_user.id]["gender"] = g
    set_step(m.from_user.id, "menu_ht")
    bot.send_message(m.chat.id, "Рост (см):")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="menu_ht")
def week_menu_ht(m):
    try:
        h = int(m.text.strip()); USER_FLOW[m.from_user.id]["height"] = h
        set_step(m.from_user.id, "menu_wt"); bot.send_message(m.chat.id, "Вес (кг):")
    except: bot.send_message(m.chat.id, "Введи число в сантиметрах.")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="menu_wt")
def week_menu_wt(m):
    try:
        w = int(m.text.strip()); USER_FLOW[m.from_user.id]["weight"] = w
        set_step(m.from_user.id, "menu_goal"); bot.send_message(m.chat.id, "Выбери цель (кнопка выше).")
    except: bot.send_message(m.chat.id, "Введи число в кг.")

@bot.message_handler(func=lambda m: get_step(m.from_user.id)=="menu_goal")
def week_menu_goal(m):
    goal = m.text.strip().lower()
    if goal not in ("цель: похудение","цель: поддержание","цель: набор"):
        bot.send_message(m.chat.id, "Выбери цель кнопкой.", reply_markup=questionnaire_kb()); return
    USER_FLOW[m.from_user.id]["goal"] = goal

    # после анкеты — проверяем премиум
    if not has_premium(m.from_user.id):
        price = get_current_price()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(f"Купить премиум за {price} ⭐", callback_data="buy_premium_stars"))
        bot.send_message(m.chat.id,
            "Анкета принята ✅\nСоздание недельного меню — только с премиумом.",
            reply_markup=kb)
    else:
        bot.send_message(m.chat.id, "Генерирую меню на неделю под твои параметры… (заглушка)",
                         reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)
    # ====== ОПЛАТА TELEGRAM STARS (XTR) ======
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",        # Stars не требуют токена
        currency="XTR",           # важно: XTR
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
        total = getattr(sp, "total_amount", None)
        if payload.startswith("premium_stars:"):
            set_premium(m.from_user.id, PREMIUM_DAYS)
            if total is not None: log_payment(m.from_user.id, total, payload)
            db = _load(); u = db.get(str(m.from_user.id), {})
            exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
            bot.send_message(m.from_user.id, f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.",
                             reply_markup=main_menu(m.from_user.id))
        else:
            if total is not None: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.from_user.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ====== Мини-веб для Render (порт-биндинг/пинг) ======
try:
    import flask, threading as _th
    app = flask.Flask(__name__)
    @app.route('/')
    def index(): return "Bot is running!"
    def run_web():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)
    _th.Thread(target=run_web, daemon=True).start()
except Exception:
    pass  # на worker не нужен

# ====== Авто-перезапуск раз в сутки ======
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)
threading.Thread(target=auto_restart, daemon=True).start()

# ====== ЗАПУСК ======
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90, long_polling_timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
