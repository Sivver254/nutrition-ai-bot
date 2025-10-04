# bot.py — Calories AI (webhook, AI vision, weekly plan, recipes)
import os, json, time, datetime, threading, math, re
from uuid import uuid4

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ====== CONFIG ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

# Premium price/duration (env-override)
STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))  # ⭐
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")
EXTERNAL_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")  # nutrition-ai-bot.onrender.com
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # опционально

# Admins
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
        ids.add(123456789)  # поменяй либо задай ADMIN_ID/ADMIN_IDS
    return ids

ADMIN_IDS = _parse_admins()
def is_admin(uid:int)->bool: return uid in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "users.json"      # локальная БД
WELCOME_FILE = "welcome.txt"  # текст приветствия

# ====== БД ======
def _load():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f: f.write("{}")
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db, uid:int):
    s = str(uid)
    if s not in db:
        db[s] = {
            "joined": int(time.time()),
            "premium": False,
            "premium_until": 0,
            "trial_started": 0,
            "profile": { "sex": None, "height": None, "weight": None, "goal": None },
            "last_features": {},
        }
    return db[s]

def set_premium(uid:int, days:int):
    db = _load()
    u = get_user(db, uid)
    now = int(time.time())
    base = u["premium_until"] if u["premium_until"] > now else now
    u["premium_until"] = base + days*86400
    u["premium"] = True
    db[str(uid)] = u
    _save(db)

def get_current_price() -> int:
    try: return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except: return STAR_PRICE_PREMIUM_DEFAULT

def has_premium(uid:int) -> bool:
    # админам — всегда True
    if is_admin(uid): return True
    db = _load(); u = db.get(str(uid))
    if not u: return False
    if u["premium"] and u["premium_until"] > int(time.time()):
        return True
    # авто-сброс
    if u["premium"] and u["premium_until"] <= int(time.time()):
        u["premium"] = False
        db[str(uid)] = u; _save(db)
    return False

def start_trial_if_needed(uid:int):
    db = _load(); u = get_user(db, uid)
    if u["trial_started"] == 0:
        u["trial_started"] = int(time.time())
        db[str(uid)] = u; _save(db)

def trial_active(uid:int) -> bool:
    if is_admin(uid): return True
    db = _load(); u = get_user(db, uid)
    ts = u.get("trial_started", 0)
    return ts != 0 and (int(time.time()) - ts) < 24*3600

def log_payment(uid:int, stars:int, payload:str):
    db = _load()
    db.setdefault("__payments__", []).append({
        "uid": uid, "stars": int(stars), "ts": int(time.time()), "payload": payload
    })
    _save(db)

# ====== WELCOME TEXT ======
def get_welcome_text() -> str:
    default = (
        "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
        "• «📸 КБЖУ по фото» — пришли фото блюда\n"
        "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
        "Также подберу <b>меню на 7 дней</b> под твои параметры — «📅 Меню на неделю».\n"
        "«👨‍🍳 Рецепты от ИИ» — бесплатно.\n\n"
        "Премиум открывает доп. функции на 30 дней."
    )
    if not os.path.exists(WELCOME_FILE):
        with open(WELCOME_FILE, "w", encoding="utf-8") as f: f.write(default)
        return default
    try:
        with open(WELCOME_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
            return t or default
    except:
        return default

def set_welcome_text(new_text:str):
    with open(WELCOME_FILE, "w", encoding="utf-8") as f:
        f.write(new_text.strip())

# ====== OpenAI helpers (мягкие заглушки, если нет ключа) ======
def ai_available() -> bool:
    return bool(OPENAI_API_KEY)

def ai_summarize(prompt:str, system:str="Ты нутрициолог. Пиши коротко и по делу.") -> str:
    if not ai_available():
        # мягкая заглушка
        return "⚠️ ИИ временно недоступен. Попробуй позже."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        chat = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system", "content":system},
                {"role":"user", "content":prompt}
            ],
            temperature=0.4,
            max_tokens=600
        )
        return chat.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ ИИ ошибка: {e}"

def ai_vision_estimate_kbju(image_url:str, hint_text:str="") -> dict:
    """
    Возвращает дикт: {"kcal": int, "p":int, "f":int, "c":int, "title": "Блюдо", "items":[...]}
    """
    if not ai_available():
        # простая заглушка
        return {"kcal": 520, "p": 32, "f": 18, "c": 50, "title": "Блюдо (пример)", "items": ["пример ингредиентов"]}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        msg = [
            {"role":"system","content":"Ты опытный нутрициолог. По фото оцени название блюда, приблизительный состав и КБЖУ."},
            {"role":"user","content":[
                {"type":"input_text","text": "Проанализируй фото. Дай название, список ингредиентов и приблизительное КБЖУ." + (f"\nПодсказка: {hint_text}" if hint_text else "")},
                {"type":"input_image","image_url": image_url}
            ]}
        ]
        out = client.chat.completions.create(model="gpt-4o-mini", messages=msg, temperature=0.2, max_tokens=500)
        text = out.choices[0].message.content
        # простейший разбор цифр
        kcal = re.search(r'(\d{2,4})\s*к?к?ал', text.lower())
        p = re.search(r'б.*?(\d{1,3})', text.lower())
        f = re.search(r'ж.*?(\d{1,3})', text.lower())
        c = re.search(r'у.*?(\d{1,3})', text.lower())
        title = re.search(r'название\s*[:\-]\s*(.+)', text.lower())
        return {
            "kcal": int(kcal.group(1)) if kcal else 500,
            "p": int(p.group(1)) if p else 30,
            "f": int(f.group(1)) if f else 20,
            "c": int(c.group(1)) if c else 50,
            "title": (title.group(1).strip().title() if title else "Блюдо"),
            "items": re.findall(r'•\s*(.+)', text)
        }
    except Exception as e:
        return {"kcal": 520, "p": 32, "f": 18, "c": 50, "title":"Блюдо", "items":[f"Ошибка ИИ: {e}"]}
        # ====== Клавиатуры ======
def main_menu(uid:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("📅 Меню на неделю"), KeyboardButton("👨‍🍳 Рецепты от ИИ"))
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    if uid and is_admin(uid):
        kb.row(KeyboardButton("🛠 Админ-панель"))
    return kb

def back_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def sex_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Мужчина"), KeyboardButton("Женщина"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def goal_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"), KeyboardButton("Набор массы"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

# ====== Пользовательские состояния ======
USER_FLOW = {}  # {uid: {"step": "...", "tmp": {...}}}

def reset_flow(uid:int):
    USER_FLOW.pop(uid, None)

# ====== Старт ======
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); u = get_user(db, m.from_user.id); _save(db)
    bot.send_message(m.chat.id, get_welcome_text(), reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text=="📊 Проверить премиум")
def check_premium(m):
    if has_premium(m.from_user.id):
        db = _load(); u = db.get(str(m.from_user.id), {})
        exp = u.get("premium_until", 0)
        exp_str = datetime.datetime.fromtimestamp(exp).strftime("%d.%m.%Y") if exp>0 else "∞"
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp_str}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text=="⭐ Купить премиум")
def buy_premium(m):
    price = get_current_price()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_premium_stars"))
    bot.send_message(m.chat.id, f"Премиум на {PREMIUM_DAYS} дней.\nЦена: {price} ⭐", reply_markup=kb)

# ====== Анкета для плана ======
@bot.message_handler(func=lambda m: m.text=="📅 Меню на неделю")
def week_menu_start(m):
    uid = m.from_user.id
    db = _load(); u = get_user(db, uid)
    USER_FLOW[uid] = {"step":"ask_sex", "tmp":{}}
    bot.send_message(m.chat.id, "Кому составляем план? Выберите пол:", reply_markup=sex_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="ask_sex")
def week_menu_sex(m):
    if m.text == "⬅️ Назад":
        reset_flow(m.from_user.id); bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id)); return
    if m.text not in ("Мужчина","Женщина"):
        bot.reply_to(m, "Выберите кнопку «Мужчина» или «Женщина».", reply_markup=sex_kb()); return
    USER_FLOW[m.from_user.id]["tmp"]["sex"] = "male" if m.text=="Мужчина" else "female"
    USER_FLOW[m.from_user.id]["step"] = "ask_height"
    bot.send_message(m.chat.id, "Введите рост (см):", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="ask_height")
def week_menu_height(m):
    if m.text == "⬅️ Назад":
        USER_FLOW[m.from_user.id]["step"]="ask_sex"; bot.send_message(m.chat.id,"Кому план? Пол:", reply_markup=sex_kb()); return
    try:
        h = int(re.sub(r"[^\d]", "", m.text))
        if h < 100 or h > 240: raise ValueError
        USER_FLOW[m.from_user.id]["tmp"]["height"] = h
        USER_FLOW[m.from_user.id]["step"] = "ask_weight"
        bot.send_message(m.chat.id, "Введите вес (кг):", reply_markup=back_kb())
    except:
        bot.reply_to(m, "Напишите целое число, например 178.", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="ask_weight")
def week_menu_weight(m):
    if m.text == "⬅️ Назад":
        USER_FLOW[m.from_user.id]["step"]="ask_height"; bot.send_message(m.chat.id,"Введите рост (см):", reply_markup=back_kb()); return
    try:
        w = int(re.sub(r"[^\d]", "", m.text))
        if w < 30 or w > 300: raise ValueError
        USER_FLOW[m.from_user.id]["tmp"]["weight"] = w
        USER_FLOW[m.from_user.id]["step"] = "ask_goal"
        bot.send_message(m.chat.id, "Выберите цель:", reply_markup=goal_kb())
    except:
        bot.reply_to(m, "Напишите целое число, например 72.", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="ask_goal")
def week_menu_goal(m):
    if m.text == "⬅️ Назад":
        USER_FLOW[m.from_user.id]["step"]="ask_weight"; bot.send_message(m.chat.id,"Введите вес (кг):", reply_markup=back_kb()); return
    if m.text not in ("Похудение","Поддержание веса","Набор массы"):
        bot.reply_to(m, "Выберите кнопку цели.", reply_markup=goal_kb()); return
    USER_FLOW[m.from_user.id]["tmp"]["goal"] = m.text
    USER_FLOW[m.from_user.id]["step"] = "build_week_plan"

    # сохраним профиль
    db = _load(); u = get_user(db, m.from_user.id)
    u["profile"].update(USER_FLOW[m.from_user.id]["tmp"])
    db[str(m.from_user.id)] = u; _save(db)

    # запуск генерации
    bot.send_chat_action(m.chat.id, "typing")
    msg = bot.send_message(m.chat.id, "🧠 Создаю план под вас! Это может занять 5–10 секунд…", reply_markup=back_kb())
    plan = build_week_menu_ai(m.from_user.id)
    bot.edit_message_text(plan, m.chat.id, msg.message_id, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

def build_week_menu_ai(uid:int) -> str:
    db = _load(); u = get_user(db, uid)
    sex = "мужчина" if u["profile"].get("sex")=="male" else "женщина"
    h = u["profile"].get("height") or 170
    w = u["profile"].get("weight") or 70
    goal = u["profile"].get("goal") or "Поддержание веса"

    prompt = (
        f"Составь подробный план питания на 7 дней, по дням недели.\n"
        f"Профиль: пол {sex}, рост {h} см, вес {w} кг, цель: {goal}.\n"
        f"На каждый день: завтрак/обед/ужин/перекусы, приблизительные граммовки и КБЖУ каждого блюда. "
        f"Суммарную суточную калорийность выводи строкой «Итого за день: N ккал». "
        f"Пиши кратко, но структурировано. Без медицинских заявлений."
    )
    ans = ai_summarize(prompt, system="Ты нутрициолог. Пиши структурировано, по дням.")
    return ans
    # ====== КБЖУ по списку ======
@bot.message_handler(func=lambda m: m.text=="🧾 КБЖУ по списку")
def kbju_list_start(m):
    uid = m.from_user.id
    start_trial_if_needed(uid)
    bot.send_message(
        m.chat.id,
        "Пробный доступ активен ✅\nПришли список в формате: «Продукт 120 г; ...». Пример:\n"
        "Кур. грудка 150 г; Рис 180 г; Салат 120 г",
        reply_markup=back_kb()
    )
    USER_FLOW[uid] = {"step":"kbju_list"}

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="kbju_list")
def kbju_list_calc(m):
    if m.text == "⬅️ Назад":
        reset_flow(m.from_user.id); bot.send_message(m.chat.id,"Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id)); return
    uid = m.from_user.id
    if not (trial_active(uid) or has_premium(uid)):
        price = get_current_price()
        bot.reply_to(m, f"🔒 Функция доступна с премиумом. Купи премиум за {price} ⭐.", reply_markup=main_menu(uid))
        reset_flow(uid); return

    items = [x.strip() for x in re.split(r"[;,]\s*", m.text) if x.strip()]
    if not items:
        bot.reply_to(m, "Не понял список 🤔 Пришли так: «Кур. грудка 150 г; Рис 180 г; Салат 120 г».", reply_markup=back_kb()); return

    # ИИ-подсчёт КБЖУ по списку (семантический)
    prompt = (
        "Посчитай КБЖУ для списка продуктов с граммовкой. Верни итог и краткую таблицу.\n" +
        "\n".join(items)
    )
    ans = ai_summarize(prompt, system="Ты нутрициолог. Учитывай типичные пищевые ценности. Пиши кратко.")
    bot.send_message(m.chat.id, ans, reply_markup=main_menu(uid))
    reset_flow(uid)

# ====== КБЖУ по фото ======
@bot.message_handler(func=lambda m: m.text=="📸 КБЖУ по фото")
def kbju_photo_hint(m):
    uid = m.from_user.id
    start_trial_if_needed(uid)
    USER_FLOW[uid] = {"step":"wait_photo_hint"}
    bot.send_message(m.chat.id, "Пришли фото блюда. Можно добавить подпись с ингредиентами.", reply_markup=back_kb())

@bot.message_handler(content_types=['photo'])
def kbju_photo_handle(m):
    uid = m.from_user.id
    state = USER_FLOW.get(uid,{}).get("step")
    if state not in ("wait_photo_hint", None):  # разбираем только в этой ветке
        return
    if not (trial_active(uid) or has_premium(uid)):
        price = get_current_price()
        bot.reply_to(m, f"🔒 Для лучшего распознавания нужен премиум. Купи за {price} ⭐.", reply_markup=main_menu(uid))
        reset_flow(uid); return

    # URL фото
    file_id = m.photo[-1].file_id
    info = bot.get_file(file_id)
    img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"

    tmp_msg = bot.send_message(m.chat.id, "🧠 Начинаю анализировать изображение на КБЖУ…")
    result = ai_vision_estimate_kbju(img_url, hint_text=m.caption or "")
    title = result.get("title","Блюдо")
    items = result.get("items", [])
    ans = (
        f"<b>{title}</b>\n"
        + (("Ингредиенты: " + ", ".join(items) + "\n") if items else "")
        + f"Примерно: ~{result['kcal']} ккал, Б/Ж/У {result['p']}/{result['f']}/{result['c']}"
    )
    bot.edit_message_text(ans, m.chat.id, tmp_msg.message_id, reply_markup=main_menu(uid))
    reset_flow(uid)

# ====== Рецепты от ИИ ======
@bot.message_handler(func=lambda m: m.text=="👨‍🍳 Рецепты от ИИ")
def recipes_menu(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("Рецепт по калориям"), KeyboardButton("Рецепт по запросу"))
    kb.row(KeyboardButton("⬅️ Назад"))
    bot.send_message(m.chat.id, "Выбери режим:", reply_markup=kb)
    USER_FLOW[m.from_user.id] = {"step":"recipes_menu"}

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipes_menu")
def recipes_choice(m):
    if m.text == "⬅️ Назад":
        reset_flow(m.from_user.id); bot.send_message(m.chat.id, "Окей, меню.", reply_markup=main_menu(m.from_user.id)); return
    if m.text == "Рецепт по калориям":
        USER_FLOW[m.from_user.id] = {"step":"recipe_cal"}
        bot.send_message(m.chat.id, "Сколько ккал нужно? Например: 600", reply_markup=back_kb())
    elif m.text == "Рецепт по запросу":
        USER_FLOW[m.from_user.id] = {"step":"recipe_free"}
        bot.send_message(m.chat.id, "Что приготовить? Например: блины без сахара", reply_markup=back_kb())
    else:
        bot.reply_to(m, "Выбери режим рецептов.", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipe_cal")
def recipe_by_cal(m):
    if m.text=="⬅️ Назад":
        recipes_menu(m); return
    try:
        cal = int(re.sub(r"[^\d]","", m.text))
        tmp = bot.send_message(m.chat.id, "👨‍🍳 Создаю рецепт…")
        prompt = (
            f"Придумай простой рецепт примерно на {cal} ккал. "
            "Дай список ингредиентов с граммовкой, шаги приготовления и оценку КБЖУ."
        )
        ans = ai_summarize(prompt, system="Ты кулинар и нутрициолог. Пиши структурировано.")
        bot.edit_message_text(ans, m.chat.id, tmp.message_id, reply_markup=main_menu(m.from_user.id))
        reset_flow(m.from_user.id)
    except:
        bot.reply_to(m, "Напиши число, например 600.", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipe_free")
def recipe_free(m):
    if m.text=="⬅️ Назад":
        recipes_menu(m); return
    tmp = bot.send_message(m.chat.id, "👨‍🍳 Создаю рецепт…")
    prompt = (
        f"Сгенерируй рецепт по запросу: «{m.text}». "
        "Дай ингредиенты с граммовкой, шаги, время приготовления и КБЖУ на порцию."
    )
    ans = ai_summarize(prompt, system="Ты кулинар и нутрициолог. Пиши структурировано.")
    bot.edit_message_text(ans, m.chat.id, tmp.message_id, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)
    # ====== Оплата Stars ======
@bot.callback_query_handler(func=lambda c: c.data=="buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",  # Stars
        currency="XTR",
        prices=prices,
        is_flexible=False
    )
    bot.answer_callback_query(c.id)

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    try: bot.answer_pre_checkout_query(q.id, ok=True)
    except Exception as e: print("pre_checkout error:", e)

@bot.message_handler(content_types=['successful_payment'])
def on_paid(m):
    try:
        sp = m.successful_payment
        payload = sp.invoice_payload or ""
        total = getattr(sp, "total_amount", None)
        if payload.startswith("premium_stars:"):
            set_premium(m.from_user.id, PREMIUM_DAYS)
            if total: log_payment(m.from_user.id, total, payload)
            db = _load(); u = db.get(str(m.from_user.id), {})
            exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
            bot.send_message(m.from_user.id, f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
        else:
            if total: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.from_user.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ====== Админка ======
@bot.message_handler(func=lambda m: m.text in ("🛠 Админ-панель", "/admin"))
def admin_panel(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Нет доступа.", reply_markup=main_menu(m.from_user.id)); return
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
        InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_premiums")
    )
    kb.row(
        InlineKeyboardButton("📣 Рассылка", callback_data="adm_broadcast"),
        InlineKeyboardButton("📝 Изм. приветствие", callback_data="adm_welcome")
    )
    kb.row(InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"))
    bot.send_message(m.chat.id, "🔧 Админ-панель", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "⛔ Нет доступа."); return
    db = _load()
    if c.data == "adm_users":
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db.keys() if k!='__payments__'])}</b>")
    elif c.data == "adm_premiums":
        now = int(time.time())
        active = 0
        for k,v in db.items():
            if k=="__payments__": continue
            if isinstance(v, dict) and (is_admin(int(k)) or (v.get('premium') and v.get('premium_until',0)>now)):
                active+=1
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data == "adm_income":
        pays = db.get("__payments__", [])
        total = sum(p["stars"] for p in pays)
        cnt = len(pays)
        bot.send_message(c.message.chat.id, f"💰 Локально зафиксировано: <b>{total} ⭐</b> ({cnt} оплат)")
    elif c.data == "adm_broadcast":
        bot.send_message(c.message.chat.id, "Отправь текст рассылки (разошлю всем).", parse_mode=None)
        bot.register_next_step_handler(c.message, admin_broadcast_step)
    elif c.data == "adm_welcome":
        bot.send_message(c.message.chat.id, "Пришли новый текст приветствия. Текущий:\n\n" + get_welcome_text(), parse_mode=None)
        bot.register_next_step_handler(c.message, admin_welcome_step)

def admin_broadcast_step(m):
    if not is_admin(m.from_user.id): return
    db = _load()
    text = m.text
    ok, fail = 0, 0
    for k in list(db.keys()):
        if k=="__payments__": continue
        try:
            bot.send_message(int(k), text)
            ok += 1
        except:
            fail += 1
    bot.reply_to(m, f"📣 Отправлено: {ok}, ошибок: {fail}", reply_markup=main_menu(m.from_user.id))

def admin_welcome_step(m):
    if not is_admin(m.from_user.id): return
    set_welcome_text(m.text)
    bot.reply_to(m, "✅ Приветствие обновлено.", reply_markup=main_menu(m.from_user.id))
    # ====== Webhook server (Flask) ======
def run_web():
    try:
        from flask import Flask, request, abort
        app = Flask(__name__)

        @app.get("/")
        def index(): return "Bot is running", 200

        @app.post(f"/tg/{WEBHOOK_SECRET}")
        def webhook():
            if request.headers.get('content-type') != 'application/json':
                return abort(403)
            # (опционально) проверка кастомного хедера:
            # if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET: abort(403)
            update = telebot.types.Update.de_json(request.get_data(as_text=True))
            bot.process_new_updates([update])
            return "ok", 200

        # Снимем старый вебхук и поставим новый
        try:
            bot.remove_webhook()  # без drop_pending_updates (в старых версиях нет аргумента)
        except Exception as e:
            print("remove_webhook warn:", e)

        if not EXTERNAL_HOST:
            print("⚠️ RENDER_EXTERNAL_HOSTNAME не задан — вебхук не будет установлен.")
        else:
            url = f"https://{EXTERNAL_HOST}/tg/{WEBHOOK_SECRET}"
            bot.set_webhook(url=url)  # можно добавить secret_token=WEBHOOK_SECRET в новых версиях API

        port = int(os.getenv("PORT", "10000"))
        app.run(host="0.0.0.0", port=port)
    except Exception as e:
        print("❌ Webhook setup failed:", e)

# ====== Авто-перезапуск раз в сутки (подстраховка) ======
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)

# ====== Run ======
if __name__ == "__main__":
    threading.Thread(target=auto_restart, daemon=True).start()
    print("✅ Bot started (webhook)")
    run_web()
