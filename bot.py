# bot.py (часть 1/4) — конфиг, база, хелперы
import os, json, time, math, threading, datetime
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ===== Конфиг =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # нужен для ИИ и фото
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))

ADMIN_IDS = set()
if os.getenv("ADMIN_ID"):
    try: ADMIN_IDS.add(int(os.getenv("ADMIN_ID")))
    except: pass
if os.getenv("ADMIN_IDS"):
    for x in os.getenv("ADMIN_IDS").split(","):
        x=x.strip()
        if x.isdigit(): ADMIN_IDS.add(int(x))
if not ADMIN_IDS:
    ADMIN_IDS.add(123456789)  # подстраховка

def is_admin(uid:int)->bool: return uid in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ===== "БД" =====
DATA_FILE = "users.json"
def _load():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE,"w",encoding="utf-8") as f: f.write("{}")
    try:
        return json.load(open(DATA_FILE,"r",encoding="utf-8"))
    except: return {}
def _save(db):
    json.dump(db, open(DATA_FILE,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

def get_user(db, uid:int):
    s=str(uid)
    if s not in db:
        db[s] = {
            "joined": int(time.time()),
            "premium": False, "premium_until":0,
            "trial_until": 0,                    # пробный 24 часа для бесплатных КБЖУ
            "profile": {}                        # анкета
        }
    return db[s]

def set_premium(uid:int, days:int):
    db=_load(); u=get_user(db, uid)
    now=int(time.time()); base=u["premium_until"] if u["premium_until"]>now else now
    u["premium_until"]=base+days*86400; u["premium"]=True
    db[str(uid)]=u; _save(db)

def has_premium(uid:int)->bool:
    if is_admin(uid): return True
    db=_load(); u=db.get(str(uid))
    if not u: return False
    if u["premium"] and u["premium_until"]>int(time.time()): return True
    if u["premium"] and u["premium_until"]<=int(time.time()):
        u["premium"]=False; db[str(uid)]=u; _save(db)
    return False

def mark_trial(uid:int, hours=24):
    db=_load(); u=get_user(db, uid)
    u["trial_until"]=int(time.time())+hours*3600
    db[str(uid)]=u; _save(db)

def has_trial(uid:int)->bool:
    if is_admin(uid): return True
    db=_load(); u=db.get(str(uid))
    return bool(u and u.get("trial_until",0)>int(time.time()))

def log_payment(uid:int, stars:int, payload:str):
    db=_load()
    db.setdefault("__payments__", []).append({
        "uid": uid, "stars": int(stars), "ts": int(time.time()), "payload": payload
    })
    _save(db)

def get_current_price()->int:
    try: return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except: return STAR_PRICE_PREMIUM_DEFAULT

# ===== UI =====
MAIN_BTNS = {
    "buy": "⭐ Купить премиум",
    "check": "📊 Проверить премиум",
    "photo": "📸 КБЖУ по фото",
    "list": "🧾 КБЖУ по списку",
    "menu": "📅 Меню на неделю",
    "recipes": "👨‍🍳 Рецепты от ИИ",
    "back": "⬅️ Назад",
    "admin": "👨‍💻 Админка"
}

def main_menu(uid:int=None):
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(MAIN_BTNS["buy"]), KeyboardButton(MAIN_BTNS["check"]))
    kb.row(KeyboardButton(MAIN_BTNS["photo"]), KeyboardButton(MAIN_BTNS["list"]))
    kb.row(KeyboardButton(MAIN_BTNS["menu"]))
    kb.row(KeyboardButton(MAIN_BTNS["recipes"]))
    if uid and is_admin(uid):
        kb.row(KeyboardButton(MAIN_BTNS["admin"]))
    return kb

def back_menu(): 
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(MAIN_BTNS["back"]))
    return kb

def send_typing(chat_id, kind="typing", seconds=2):
    # kind: typing, upload_photo, choose_sticker...
    try:
        for _ in range(seconds):
            bot.send_chat_action(chat_id, kind)
            time.sleep(1)
    except: pass

# временные статусы «Создаю…»
def temp_message(chat_id, text):
    try: 
        m = bot.send_message(chat_id, text)
        return (m.chat.id, m.message_id)
    except:
        return (None, None)
def delete_temp(msg_tuple):
    cid, mid = msg_tuple
    if cid and mid:
        try: bot.delete_message(cid, mid)
        except: pass
            # bot.py (часть 2/4)

WELCOME = (
    "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
    "• «📸 КБЖУ по фото» — пришли фото блюда\n"
    "• «🧾 КБЖУ по списку» — напиши продукты и граммовку\n\n"
    "Также сделаю меню на 7 дней под твои параметры — «📅 Меню на неделю».\n"
    "«👨‍🍳 Рецепты от ИИ» — бесплатно. Премиум открывает доп. функции на 30 дней."
)

@bot.message_handler(commands=["start"])
def cmd_start(m):
    db=_load(); get_user(db, m.from_user.id); _save(db)
    if is_admin(m.from_user.id):  # админам всегда премиум
        set_premium(m.from_user.id, 3650)
    bot.send_message(m.chat.id, WELCOME, reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["check"])
def check_premium(m):
    if has_premium(m.from_user.id):
        u=_load().get(str(m.from_user.id),{})
        exp=datetime.datetime.fromtimestamp(u.get("premium_until",0)).strftime("%d.%m.%Y")
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["buy"])
def buy_premium(m):
    price=get_current_price()
    kb=InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_stars"))
    bot.send_message(m.chat.id,
        f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐",
        reply_markup=kb
    )

# ===== Рецепты от ИИ (статус + ИИ) =====
def ai_call(prompt:str)->str:
    """
    Универсальный вызов чата OpenAI. Нужен OPENAI_API_KEY.
    Возвращает текст. Если ключа нет — фолбэк.
    """
    if not OPENAI_API_KEY:
        return "⚠️ ИИ недоступен (нет OPENAI_API_KEY)."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"Ты нутрициолог и шеф-повар. Пиши чётко и по делу."},
                      {"role":"user","content":prompt}]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ ИИ ошибка: {e}"

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["recipes"])
def recipes_menu(m):
    kb=InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Рецепт на 600 ккал", callback_data="rx_600"))
    kb.add(InlineKeyboardButton("Рецепт по запросу", callback_data="rx_custom"))
    bot.send_message(m.chat.id, "Выбери вариант:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("rx_600","rx_custom"))
def recipes_actions(c):
    if c.data=="rx_600":
        t = temp_message(c.message.chat.id, "🍳 Создаю рецепт…")
        send_typing(c.message.chat.id, "typing", 2)
        txt = ai_call("Сделай простой рецепт на ~600 ккал. Дай ингредиенты с граммовкой, шаги и К/Б/Ж/У в конце.")
        delete_temp(t)
        bot.send_message(c.message.chat.id, txt, reply_markup=main_menu(c.from_user.id))
    else:
        bot.answer_callback_query(c.id)
        msg = bot.send_message(c.message.chat.id, "Напиши, какой рецепт тебе нужен (вкус/продукты/калории)…", reply_markup=back_menu())
        bot.register_next_step_handler(msg, recipe_custom_step)

def recipe_custom_step(m):
    if m.text==MAIN_BTNS["back"]:
        bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id)); return
    t = temp_message(m.chat.id, "🍳 Создаю рецепт…")
    send_typing(m.chat.id, "typing", 3)
    txt = ai_call(f"Сделай рецепт по описанию: «{m.text}». Обязательно укажи порции, ингредиенты, шаги и К/Б/Ж/У.")
    delete_temp(t)
    bot.send_message(m.chat.id, txt, reply_markup=main_menu(m.from_user.id))

# ===== КБЖУ по списку =====
def parse_list_to_items(text:str):
    """
    Пытается понять строки типа:
    'Кур. грудка 150 г; Рис 180 г; Салат 120 г'
    Возвращает список словарей [{'name':..., 'gram':...}, ...]
    """
    raw = [p.strip() for p in text.replace("\n"," ").split(";") if p.strip()]
    items=[]
    for p in raw:
        # мягкий разбор, ИИ-поддержка
        grams = None
        name = p
        # выцепим число граммов
        import re
        g = re.search(r'(\d+)\s*(г|гр|грам|грамм|grams?)?', p, re.I)
        if g:
            grams = int(g.group(1))
            name = p[:g.start()].strip() or p[g.end():].strip()
        if not grams:
            # попросим ИИ догадаться
            guess = ai_call(f"В фразе «{p}» найди целое число граммов, верни только число. Если нет — 0.")
            try: grams = int(guess)
            except: grams = 0
        name = name.strip(" ,.-")
        if not name:
            name = ai_call(f"В фразе «{p}» выдели название продукта (кратко). Верни только название.")
        if grams<=0:
            return None, f"Не понял позицию: «{p}»"
        items.append({"name": name, "gram": grams})
    return items, None

def estimate_kbju_items(items):
    """
    Простейший расчёт через ИИ (можно заменить на твою таблицу).
    На вход: [{'name','gram'}]. Возвращает (кал,б,ж,у,детализация)
    """
    if not OPENAI_API_KEY:
        # фолбэк
        kcal = sum(max(int(i["gram"]*1.2), 0) for i in items)
        return kcal, round(kcal/10), round(kcal/20), round(kcal/6), "⚠️ Грубая оценка без ИИ."

    prompt = (
        "Оцени К/Б/Ж/У для списка продуктов с граммовкой. "
        "Дай точные числа и краткую сводку.\n\n"
        + "\n".join([f"- {it['name']} {it['gram']} г" for it in items]) +
        "\n\nФормат ответа:\n"
        "Итог: ХХХ ккал, Б/Ж/У: B/G/C\n"
        "По позициям: ... (кратко)\n"
    )
    txt = ai_call(prompt)
    # Поверим на слово ИИ и вернём текст. Для интерфейса понадобятся числа:
    import re
    kcal = re.search(r'(\d{2,5})\s*ккал', txt)
    b = re.search(r'Б[:\s]*([0-9]+)', txt, re.I)
    g = re.search(r'Ж[:\s]*([0-9]+)', txt, re.I)
    c = re.search(r'У[:\s]*([0-9]+)', txt, re.I)
    K = int(kcal.group(1)) if kcal else 0
    B = int(b.group(1)) if b else 0
    G = int(g.group(1)) if g else 0
    C = int(c.group(1)) if c else 0
    return K, B, G, C, txt

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["list"])
def kbju_list_prompt(m):
    if not (has_premium(m.from_user.id) or has_trial(m.from_user.id)):
        mark_trial(m.from_user.id, 24)
        bot.send_message(m.chat.id, "Пробный доступ активен ✅\nПришли список в формате: «Продукт 120 г; …».", reply_markup=back_menu())
    else:
        bot.send_message(m.chat.id, "Пришли список в формате: «Продукт 120 г; …».", reply_markup=back_menu())
    bot.register_next_step_handler(m, kbju_list_calc)

def kbju_list_calc(m):
    if m.text==MAIN_BTNS["back"]:
        bot.send_message(m.chat.id, "Ок, вернул в меню.", reply_markup=main_menu(m.from_user.id)); return
    items, err = parse_list_to_items(m.text)
    if err:
        bot.send_message(m.chat.id, f"⚠️ Ошибка: {err}\nПопробуй ещё раз.", reply_markup=back_menu())
        bot.register_next_step_handler(m, kbju_list_calc); return
    t=temp_message(m.chat.id, "🧮 Считаю КБЖУ…")
    send_typing(m.chat.id, "typing", 2)
    kcal,B,G,C,detail = estimate_kbju_items(items)
    delete_temp(t)
    lines = [f"<b>Итог:</b> ~{kcal} ккал, Б/Ж/У: {B}/{G}/{C}", "", "<b>Детали:</b>", detail]
    bot.send_message(m.chat.id, "\n".join(lines), reply_markup=main_menu(m.from_user.id))
    # bot.py (часть 3/4)

# ===== КБЖУ по фото (OpenAI Vision) =====
def ai_vision_kbju(photo_url:str)->str:
    """
    Отправляет URL фото в модель, просит назвать блюдо/ингредиенты и оценить КБЖУ.
    Вернёт готовый текст.
    """
    if not OPENAI_API_KEY:
        return "⚠️ ИИ-визион недоступен (нет OPENAI_API_KEY)."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = (
            "Определи блюдо по фото, перечисли 3–6 ключевых ингредиентов.\n"
            "Затем оцени суммарные К/Б/Ж/У порции. Выведи так:\n"
            "Название: ...\nИнгредиенты: ...\nИтог: ХХХ ккал; Б/Ж/У: B/G/C\n"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"Ты нутрициолог. Будь краток и точен."},
                {"role":"user","content":[
                    {"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url":photo_url}}
                ]}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Ошибка анализа фото: {e}"

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["photo"])
def kbju_photo_hint(m):
    bot.send_message(m.chat.id, "Пришли фото блюда одним сообщением. Начну анализ и пришлю КБЖУ.", reply_markup=back_menu())

@bot.message_handler(content_types=['photo'])
def on_photo(m):
    # статус
    s = temp_message(m.chat.id, "🧠 Начинаю анализировать изображение на КБЖУ…")
    try:
        send_typing(m.chat.id, "upload_photo", 3)
        # возьмём самый большой файл
        file_id = m.photo[-1].file_id
        fi = bot.get_file(file_id)
        # публичный URL у Telegram нет, но API отдаёт path -> формируем ссылку CDN
        # альтернатива: скачать и поднять на filesend — но Render без диска. 
        # Поэтому используем прямую ссылку Telegram Files:
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fi.file_path}"
        txt = ai_vision_kbju(photo_url)
        delete_temp(s)
        bot.send_message(m.chat.id, txt, reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        delete_temp(s)
        bot.send_message(m.chat.id, f"⚠️ Не удалось обработать фото: {e}", reply_markup=main_menu(m.from_user.id))

# ===== Меню на неделю =====
GOAL_BTNS = ["🏃 Похудение","⚖️ Поддержание","💪 Набор массы"]
SEX_BTNS  = ["👨 Мужчина","👩 Женщина"]

def ask_profile(m):
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(SEX_BTNS[0]), KeyboardButton(SEX_BTNS[1]))
    bot.send_message(m.chat.id, "Укажи пол:", reply_markup=kb)
    bot.register_next_step_handler(m, prof_sex)

def prof_sex(m):
    if m.text not in SEX_BTNS:
        bot.register_next_step_handler(m, prof_sex); return
    db=_load(); u=get_user(db, m.from_user.id); u["profile"]["sex"]= "male" if "Мужчина" in m.text else "female"; _save(db)
    msg = bot.send_message(m.chat.id, "Введи рост в см (например 178):", reply_markup=back_menu())
    bot.register_next_step_handler(msg, prof_height)

def prof_height(m):
    if m.text==MAIN_BTNS["back"]:
        bot.send_message(m.chat.id, "Отменено.", reply_markup=main_menu(m.from_user.id)); return
    try:
        h=int(m.text); assert 120<=h<=230
        db=_load(); u=get_user(db, m.from_user.id); u["profile"]["height"]=h; _save(db)
        msg = bot.send_message(m.chat.id, "Введи вес в кг (например 74):", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_weight)
    except:
        msg = bot.send_message(m.chat.id,"Нужно целое число от 120 до 230. Попробуй ещё:", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_height)

def prof_weight(m):
    if m.text==MAIN_BTNS["back"]:
        bot.send_message(m.chat.id, "Отменено.", reply_markup=main_menu(m.from_user.id)); return
    try:
        w=float(m.text); assert 30<=w<=300
        db=_load(); u=get_user(db, m.from_user.id); u["profile"]["weight"]=float(w); _save(db)
        kb=ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row(*[KeyboardButton(x) for x in GOAL_BTNS])
        bot.send_message(m.chat.id, "Выбери цель:", reply_markup=kb)
        bot.register_next_step_handler(m, prof_goal)
    except:
        msg = bot.send_message(m.chat.id,"Нужно число от 30 до 300. Попробуй ещё:", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_weight)

def prof_goal(m):
    if m.text not in GOAL_BTNS:
        bot.register_next_step_handler(m, prof_goal); return
    db=_load(); u=get_user(db, m.from_user.id)
    goal_map={"🏃 Похудение":"cut","⚖️ Поддержание":"maintain","💪 Набор массы":"bulk"}
    u["profile"]["goal"]=goal_map[m.text]; _save(db)
    build_week_menu(m)

def bmr_mifflin(sex, w, h, age=30):
    # грубо, возраста нет в анкете — по умолчанию 30
    s = 5 if sex=="male" else -161
    return 10*w + 6.25*h - 5*age + s

def build_week_menu(m):
    uid=m.from_user.id
    if not has_premium(uid):
        # анкету можно заполнить без премиума -> затем попросим оплату
        bot.send_message(m.chat.id, "🔒 Эта функция платная. Оформи премиум в меню.", reply_markup=main_menu(uid))
        return
    db=_load(); u=get_user(db, uid); p=u.get("profile",{})
    if not {"sex","height","weight","goal"}<=set(p.keys()):
        ask_profile(m); return
    # оценим калории
    kcal = round(bmr_mifflin(p["sex"], p["weight"], p["height"]) * (1.35 if p["goal"]!="bulk" else 1.55))
    if p["goal"]=="cut": kcal-=300
    if p["goal"]=="bulk": kcal+=300

    t=temp_message(m.chat.id, "🗓️ Создаю план под вас!")
    send_typing(m.chat.id, "typing", 4)
    prompt = (
        "Составь подробное меню на 7 дней (Пн..Вс) под параметры:\n"
        f"Пол: {('мужчина' if p['sex']=='male' else 'женщина')}, Рост: {p['height']} см, Вес: {p['weight']} кг, "
        f"Цель: {p['goal']}.\n"
        f"Дневная калорийность ~{kcal} ккал. На каждый день дай 4–5 приёмов пищи, названия блюд и граммовки. "
        "Пиши строго по дням недели: Пн:, Вт:, Ср:, Чт:, Пт:, Сб:, Вс:. В конце каждого дня — итого ккал и Б/Ж/У."
    )
    txt = ai_call(prompt)
    delete_temp(t)
    bot.send_message(m.chat.id, f"<b>Твой ориентир:</b> ~{kcal} ккал/день\n\n{txt}", reply_markup=main_menu(uid))

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["menu"])
def start_menu_wizard(m):
    ask_profile(m)

# ===== Админка (callback-based, чтобы всегда работало) =====
def admin_kb():
    kb=InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
           InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_prem"))
    kb.row(InlineKeyboardButton("📣 Сообщение всем", callback_data="adm_broadcast"),
           InlineKeyboardButton("✏️ Изм. приветствие", callback_data="adm_welcome"))
    kb.row(InlineKeyboardButton("💵 Изм. цену (звёзды)", callback_data="adm_price"))
    return kb

@bot.message_handler(func=lambda x: x.text==MAIN_BTNS["admin"])
def admin_entry(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Нет доступа.", reply_markup=main_menu(m.from_user.id)); return
    bot.send_message(m.chat.id, "🔧 Админ-панель", reply_markup=admin_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Нет доступа"); return
    db=_load()
    if c.data=="adm_users":
        total=len([k for k in db.keys() if k!="__payments__"])
        bot.send_message(c.message.chat.id, f"👥 Всего: <b>{total}</b>")
    elif c.data=="adm_prem":
        now=int(time.time())
        active=sum(1 for u in db.values() if isinstance(u,dict) and u.get("premium") and u.get("premium_until",0)>now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data=="adm_price":
        bot.send_message(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число:")
        bot.register_next_step_handler(c.message, admin_price_step)
    elif c.data=="adm_broadcast":
        bot.send_message(c.message.chat.id, "Напиши текст рассылки. Будь краток.")
        bot.register_next_step_handler(c.message, admin_broadcast_step)
    elif c.data=="adm_welcome":
        bot.send_message(c.message.chat.id, "Пришли новый текст приветствия (/start).")
        bot.register_next_step_handler(c.message, admin_welcome_step)

def admin_price_step(m):
    if not is_admin(m.from_user.id): return
    try:
        new=int(m.text.strip()); os.environ["STAR_PRICE_PREMIUM"]=str(new)
        bot.reply_to(m, f"✅ Новая цена: {new} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))

def admin_broadcast_step(m):
    if not is_admin(m.from_user.id): return
    txt=m.text.strip()
    db=_load()
    sent=0
    for k,u in db.items():
        if k=="__payments__": continue
        try:
            bot.send_message(int(k), f"📣 {txt}")
            sent+=1
            time.sleep(0.05)
        except: pass
    bot.reply_to(m, f"✅ Отправлено: {sent}")

def admin_welcome_step(m):
    if not is_admin(m.from_user.id): return
    global WELCOME
    WELCOME = m.text.strip()
    bot.reply_to(m, "✅ Приветствие обновлено.")
    # bot.py (часть 4/4)

# ===== Оплата Stars =====
@bot.callback_query_handler(func=lambda c: c.data=="buy_stars")
def buy_stars(c):
    price = get_current_price()
    prices = [LabeledPrice(label=f"Премиум {PREMIUM_DAYS} дней", amount=price)]
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Все функции на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",            # Stars не требуют токена
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
        sp=m.successful_payment
        payload = sp.invoice_payload or ""
        total = getattr(sp, "total_amount", None)
        if payload.startswith("premium_stars:"):
            set_premium(m.from_user.id, PREMIUM_DAYS)
            if total: log_payment(m.from_user.id, total, payload)
            u=_load().get(str(m.from_user.id),{})
            exp=datetime.datetime.fromtimestamp(u.get("premium_until",0)).strftime("%d.%m.%Y")
            bot.send_message(m.chat.id, f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
        else:
            if total: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.chat.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ===== Мини-веб для Render (и аптайма) =====
def run_web():
    try:
        from flask import Flask
        app = Flask(__name__)
        @app.get("/")
        def index(): return "Bot is running"
        port = int(os.getenv("PORT", "10000"))
        app.run(host="0.0.0.0", port=port)
    except Exception as e:
        print("web warn:", e)

# ===== Автоперезапуск раз в сутки =====
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)

# ===== Запуск =====
if __name__ == "__main__":
    # админам — премиум навсегда (на всякий случай при первом старте)
    for aid in list(ADMIN_IDS):
        try: set_premium(aid, 3650)
        except: pass

    # снимаем вебхук, поднимаем мини-веб и polling
    try:
        bot.remove_webhook(drop_pending_updates=True)
    except Exception as e:
        print("remove_webhook warn:", e)

    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=auto_restart, daemon=True).start()

    print("✅ Bot started")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=90, long_polling_timeout=30)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("polling error:", e)
            time.sleep(3)
