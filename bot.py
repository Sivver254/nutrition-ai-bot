# bot.py — Nutrition AI Bot (Telegram Stars + Trial + Recipes + Week Menu)

import os, json, time, datetime, threading, re
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ========= КОНФИГ =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set (Render → Settings → Environment)")

STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
TRIAL_HOURS = 24  # бесплатный доступ к КБЖУ (24 часа с первого использования)

# Админы
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
        ids.add(123456789)  # подставь свой ID или задай ADMIN_ID
    return ids
ADMIN_IDS = _parse_admins()
def is_admin(uid:int)->bool: return uid in ADMIN_IDS

# OpenAI (опционально)
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    oa_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    oa_client = None

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ========= ХРАНИЛКА (файл) =========
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
        db[s] = {
            "joined": int(time.time()),
            "premium": False,
            "premium_until": 0,
            "trial_started_at": 0,
            "pending_menu": None
        }
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
        db[str(uid)] = u; _save(db)
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

# ========= ТРИАЛ =========
def trial_status(uid:int):
    db = _load(); u = get_user(db, uid)
    ts = u.get("trial_started_at", 0)
    if not ts: return (False, None)
    alive = (int(time.time()) - ts) < TRIAL_HOURS * 3600
    return (alive, ts if alive else None)

def ensure_trial_or_premium(uid:int):
    if has_premium(uid): return ("premium", None)
    db = _load(); u = get_user(db, uid)
    alive, ts = trial_status(uid)
    if alive:
        return ("trial_active", ts + TRIAL_HOURS*3600)
    if not u.get("trial_started_at"):
        u["trial_started_at"] = int(time.time())
        db[str(uid)] = u; _save(db)
        return ("trial_started", u["trial_started_at"] + TRIAL_HOURS*3600)
    return ("denied", None)

# ========= Нутри-база + ИИ-парсер =========
NUTRI_BASE = {
    "куриная грудка": {"kcal":165,"p":31,"f":3.6,"c":0},
    "рис":            {"kcal":340,"p":7,"f":1,"c":76},
    "гречка":         {"kcal":330,"p":12.6,"f":3.3,"c":62.1},
    "овсянка":        {"kcal":370,"p":12.3,"f":6.1,"c":59.5},
    "яйцо":           {"kcal":157,"p":12.7,"f":11.5,"c":0.7},
    "творог":         {"kcal":156,"p":18,"f":9,"c":2.8},
    "банан":          {"kcal":89, "p":1.1,"f":0.3,"c":23},
    "яблоко":         {"kcal":52, "p":0.3,"f":0.2,"c":14},
}
def norm_name(s:str)->str: return re.sub(r"\s+"," ",s.strip().lower())

def _sanitize_items(items):
    """Приводим ответ к виду [{'name': str, 'grams': int}, ...]."""
    out = []
    if not isinstance(items, (list, tuple)):
        return out
    for x in items:
        if isinstance(x, dict):
            name = norm_name(str(x.get("name", "")).strip())
            graw = str(x.get("grams", "")).strip()
            m = re.search(r"\d+", graw)
            grams = int(m.group(0)) if m else 0
            if name and grams > 0:
                out.append({"name": name, "grams": grams})
        elif isinstance(x, str):
            m = re.search(r"([А-Яа-яA-Za-zёЁ\s]+)\s+(\d+)", x)
            if m:
                out.append({"name": norm_name(m.group(1)), "grams": int(m.group(2))})
    return out

def llm_parse_foods(text:str):
    """вернёт список словарей [{'name','grams'}], даже если LLM вернул мусор."""
    if not oa_client:
        items = []
        for p in re.split(r"[;,\n]+", text):
            m = re.search(r"([А-Яа-яA-Za-zёЁ\s]+)\s+(\d+)\s*(г|гр|грамм|ml|мл)?", p.strip())
            if m: items.append({"name": norm_name(m.group(1)), "grams": int(m.group(2))})
        return _sanitize_items(items)

    prompt = (
        "Ты нутрициолог. Разбери русский текст на продукты с массами в граммах.\n"
        'Верни ТОЛЬКО JSON-массив вида [{"name":"название","grams":число}, ...] без пояснений.'
    )
    try:
        resp = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":prompt},
                      {"role":"user","content":text}],
            temperature=0.1
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        import json as _json
        data = _json.loads(raw)
        return _sanitize_items(data)
    except Exception:
        items = []
        for p in re.split(r"[;,\n]+", text):
            m = re.search(r"([А-Яа-яA-Za-zёЁ\s]+)\s+(\d+)\s*(г|гр|грамм|ml|мл)?", p.strip())
            if m: items.append({"name": norm_name(m.group(1)), "grams": int(m.group(2))})
        return _sanitize_items(items)

def estimate_kbju(items):
    total = {"kcal":0.0,"p":0.0,"f":0.0,"c":0.0}
    details = []
    for it in items:
        n = norm_name(it["name"]); g = float(it.get("grams",0) or 0)
        base = NUTRI_BASE.get(n)
        if not base and oa_client:
            try:
                q = f"Сколько ккал, белков, жиров, углеводов в 100 г продукта: {n}? Ответ JSON {{\"kcal\":число,\"p\":число,\"f\":число,\"c\":число}}"
                r = oa_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"user","content":q}],
                    temperature=0.1
                )
                import json as _json
                base = _json.loads(r.choices[0].message.content)
            except Exception:
                base = {"kcal":100,"p":5,"f":3,"c":12}
        if not base: base = {"kcal":100,"p":5,"f":3,"c":12}
        k = g/100.0
        kcal = base["kcal"]*k; p = base["p"]*k; f = base["f"]*k; c = base["c"]*k
        total["kcal"]+=kcal; total["p"]+=p; total["f"]+=f; total["c"]+=c
        details.append((n, int(g), round(kcal), round(p,1), round(f,1), round(c,1)))
    return round(total["kcal"]), round(total["p"],1), round(total["f"],1), round(total["c"],1), details

# ========= Состояния =========
USER_FLOW = {}  # uid -> {"step": "...", "data": {...}}
def reset_flow(uid: int):
    if uid in USER_FLOW:
        USER_FLOW.pop(uid, None)
        # ========= Меню =========
def main_menu(user_id:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("👩‍🍳 Рецепты от ИИ"), KeyboardButton("ℹ️ Помощь"))
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("📅 Меню на неделю"))
    if user_id and is_admin(user_id): kb.row(KeyboardButton("👨‍💻 Админка"))
    return kb

# ========= Старт / Помощь =========
@bot.message_handler(commands=["start"])
def cmd_start(m):
    reset_flow(m.from_user.id)
    db = _load(); get_user(db, m.from_user.id); _save(db)
    text = (
        "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
        "• «📸 КБЖУ по фото» — пришли фото блюда\n"
        "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
        "Также могу подобрать <b>меню на 7 дней</b> под твои параметры — «📅 Меню на неделю».\n"
        "«👩‍🍳 Рецепты от ИИ» — бесплатно. Премиум открывает доп. функции на 30 дней."
    )
    bot.send_message(m.chat.id, text, reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "ℹ️ Помощь")
def on_help(m):
    reset_flow(m.from_user.id)
    bot.reply_to(m,
        "Как пользоваться:\n"
        "• «📸 КБЖУ по фото» — отправь фото (триал 24ч, затем премиум)\n"
        "• «🧾 КБЖУ по списку» — пример: «Овсянка 60 г; Молоко 200 мл; Банан 120 г» (триал 24ч)\n"
        "• «👩‍🍳 Рецепты от ИИ» — рецепты по названию или на заданные калории\n"
        "• «📅 Меню на неделю» — заполни анкету, если нет премиума — предложу оплату XTR",
        reply_markup=main_menu(m.from_user.id)
    )

# ========= Премиум / Stars =========
@bot.message_handler(func=lambda m: m.text == "📊 Проверить премиум")
def check_premium(m):
    reset_flow(m.from_user.id)
    if has_premium(m.from_user.id):
        db = _load(); u = db.get(str(m.from_user.id), {})
        exp = datetime.datetime.fromtimestamp(u.get("premium_until",0)).strftime("%d.%m.%Y")
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "⭐ Купить премиум")
def buy_premium(m):
    reset_flow(m.from_user.id)
    price = get_current_price()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_premium_stars"))
    bot.send_message(m.chat.id, f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]  # XTR
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",      # Stars не требует токена
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

            db = _load(); u = get_user(db, m.from_user.id)
            pending = u.get("pending_menu")
            if pending:
                plan = generate_week_menu_with_llm(pending, m.from_user.first_name or "пользователь")
                u["pending_menu"] = None; db[str(m.from_user.id)] = u; _save(db)
                bot.send_message(m.chat.id, "✅ Оплата получена! Генерирую меню по твоей анкете…")
                bot.send_message(m.chat.id, plan, reply_markup=main_menu(m.from_user.id))
            else:
                exp = datetime.datetime.fromtimestamp(u.get("premium_until",0)).strftime("%d.%m.%Y")
                bot.send_message(m.chat.id, f"✅ Премиум активен до <b>{exp}</b>.",
                                 reply_markup=main_menu(m.from_user.id))
        else:
            if total: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.chat.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ========= Рецепты (с «Назад») =========
@bot.message_handler(func=lambda m: m.text == "👩‍🍳 Рецепты от ИИ")
def recipes_entry(m):
    reset_flow(m.from_user.id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("По названию блюда"), KeyboardButton("На калории"))
    kb.row(KeyboardButton("⬅️ Назад"))
    USER_FLOW[m.from_user.id] = {"step":"recipe_choice"}
    bot.reply_to(m, "Как сгенерировать рецепт?", reply_markup=kb)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "recipe_choice")
def recipes_choice(m):
    t = (m.text or "").strip().lower()
    if t == "⬅️ назад":
        reset_flow(m.from_user.id)
        bot.send_message(m.chat.id, "Возвращаю в меню.", reply_markup=main_menu(m.from_user.id)); return
    if t == "по названию блюда":
        USER_FLOW[m.from_user.id] = {"step":"recipe_name"}
        kb = ReplyKeyboardMarkup(resize_keyboard=True); kb.row(KeyboardButton("⬅️ Назад"))
        bot.reply_to(m, "Введи название блюда. Пример: «блинчики», «паста карбонара».", reply_markup=kb)
    elif t == "на калории":
        USER_FLOW[m.from_user.id] = {"step":"recipe_kcal"}
        kb = ReplyKeyboardMarkup(resize_keyboard=True); kb.row(KeyboardButton("⬅️ Назад"))
        bot.reply_to(m, "Введи желаемую калорийность порции, например: 600", reply_markup=kb)
    else:
        bot.reply_to(m, "Выбери пункт или нажми «⬅️ Назад».")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "recipe_name")
def recipe_by_name_gen(m):
    if (m.text or "").strip().lower() == "⬅️ назад": return recipes_entry(m)
    name = m.text.strip()
    send_recipe_text(m, name=name, kcal=None)
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Готово ✅", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "recipe_kcal")
def recipe_by_kcal_gen(m):
    if (m.text or "").strip().lower() == "⬅️ назад": return recipes_entry(m)
    try:
        kcal = int("".join(ch for ch in m.text if ch.isdigit()))
    except:
        bot.reply_to(m, "Нужно число, например 600"); return
    send_recipe_text(m, name=None, kcal=kcal)
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Готово ✅", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: (m.text or "").strip().lower() == "⬅️ назад")
def go_back(m):
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))

def send_recipe_text(m, name=None, kcal=None):
    if not oa_client:
        bot.reply_to(m, "LLM не подключён (нет OPENAI_API_KEY). Добавь ключ — и пришлю рецепт.")
        return
    if name:
        sys = "Ты повар. Дай понятный рецепт на русском: ингредиенты с граммовками, пошаговая инструкция, примерные КБЖУ на порцию."
        user = f"Нужен домашний рецепт блюда: {name}. Продукты из супермаркета, 1–2 порции."
    else:
        sys = "Ты повар. Дай рецепт одной порции на указанную калорийность (±10%), ингредиенты с граммовками, инструкция и КБЖУ."
        user = f"Сформируй рецепт на одну порцию примерно на {kcal} ккал. Продукты обычные."
    try:
        resp = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            temperature=0.4
        )
        bot.send_message(m.chat.id, resp.choices[0].message.content, reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"Не удалось сгенерировать рецепт: {e}")
        # ========= КБЖУ ПО ФОТО =========
@bot.message_handler(func=lambda m: m.text == "📸 КБЖУ по фото", content_types=['text'])
def kbju_photo_intro(m):
    reset_flow(m.from_user.id)
    state, deadline = ensure_trial_or_premium(m.from_user.id)
    if state not in ("premium","trial_active","trial_started"):
        price = get_current_price()
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"Купить премиум за {price} ⭐", callback_data="buy_premium_stars"))
        bot.reply_to(m, "⏳ Пробный период закончился. Чтобы анализировать фото — оформи премиум.", reply_markup=kb)
        return
    bot.reply_to(m, "Отправь фото блюда одним сообщением. Для точности можешь подписать ингредиенты и граммы.")

@bot.message_handler(content_types=['photo'])
def kbju_photo_calc(m):
    state, _ = ensure_trial_or_premium(m.from_user.id)
    if state not in ("premium","trial_active","trial_started"):
        price = get_current_price()
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"Купить премиум за {price} ⭐", callback_data="buy_premium_stars"))
        bot.reply_to(m, "⏳ Пробный период закончился. Чтобы анализировать фото — оформи премиум.", reply_markup=kb)
        return
    bot.reply_to(m, "Фото получил 👍 Пока точнее считаю по списку продуктов. Пришли список — посчитаю.")

# ========= КБЖУ ПО СПИСКУ =========
@bot.message_handler(func=lambda m: m.text == "🧾 КБЖУ по списку")
def kbju_list_intro(m):
    reset_flow(m.from_user.id)
    state, deadline = ensure_trial_or_premium(m.from_user.id)
    if state == "premium":
        bot.reply_to(m, "Премиум активен ✅\nПришли список: «Овсянка 60 г; Молоко 200 мл; Банан 120 г».")
    elif state == "trial_started":
        dt = datetime.datetime.fromtimestamp(deadline).strftime("%d.%m %H:%M")
        bot.reply_to(m, f"🎁 Пробный доступ на 24 часа активирован до {dt}.\nТеперь пришли список продуктов и граммов.")
    elif state == "trial_active":
        bot.reply_to(m, "Пробный доступ активен ✅\nПришли список в формате: «Продукт 120 г; ...».")
    else:
        price = get_current_price()
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"Купить премиум за {price} ⭐", callback_data="buy_premium_stars"))
        bot.reply_to(m, "⏳ Пробный период закончился. Чтобы продолжить — оформи премиум.", reply_markup=kb)
        return
    USER_FLOW[m.from_user.id] = {"step":"list_wait"}  # ждём список текстом

@bot.message_handler(
    func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "list_wait",
    content_types=['text']
)
def kbju_list_calc(m):
    txt = (m.text or "").strip()

    # если жмут другую кнопку — мягко уходим из шага
    if txt in ("📸 КБЖУ по фото", "🧾 КБЖУ по списку", "👩‍🍳 Рецепты от ИИ",
               "ℹ️ Помощь", "⭐ Купить премиум", "📊 Проверить премиум",
               "📅 Меню на неделю", "👨‍💻 Админка", "⬅️ Назад"):
        reset_flow(m.from_user.id)
        if txt == "📸 КБЖУ по фото":     return kbju_photo_intro(m)
        if txt == "🧾 КБЖУ по списку":   return kbju_list_intro(m)
        if txt == "👩‍🍳 Рецепты от ИИ":  return recipes_entry(m)
        if txt == "ℹ️ Помощь":          return on_help(m)
        if txt == "⭐ Купить премиум":   return buy_premium(m)
        if txt == "📊 Проверить премиум":return check_premium(m)
        if txt == "📅 Меню на неделю":  return week_menu_start(m)
        if txt == "👨‍💻 Админка":       return admin_panel(m)
        return bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))

    items = llm_parse_foods(txt)
    items = _sanitize_items(items)  # важный шаг!
    if not items:
        bot.reply_to(m, "Не понял список 🤔 Пришли так: «Кур. грудка 150 г; Рис 180 г; Салат 120 г».")
        return

    kcal, p, f, c, det = estimate_kbju(items)
    lines = [f"• {name} — {grams} г → {kcal_i} ккал (Б:{p_i} Ж:{f_i} У:{c_i})"
             for name, grams, kcal_i, p_i, f_i, c_i in det]
    txt_out = "📊 Итог по списку:\n" + "\n".join(lines) + f"\n\nИТОГО: {kcal} ккал — Б:{p} Ж:{f} У:{c}"
    reset_flow(m.from_user.id)
    bot.reply_to(m, txt_out, reply_markup=main_menu(m.from_user.id))

# ========= Меню на неделю (анкета + премиум-проверка) =========
def start_week_menu_wizard(uid):
    USER_FLOW[uid] = {"step":"weight","data":{}}

@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def week_menu_start(m):
    reset_flow(m.from_user.id)
    start_week_menu_wizard(m.from_user.id)
    bot.send_message(m.chat.id, "Введи свой вес (кг), только число. Пример: 72")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "weight")
def week_menu_weight(m):
    try:
        w = float(m.text.replace(",", "."))
        USER_FLOW[m.from_user.id]["data"]["weight"] = w
        USER_FLOW[m.from_user.id]["step"] = "height"
        kb = ReplyKeyboardMarkup(resize_keyboard=True); kb.row(KeyboardButton("⬅️ Назад"))
        bot.reply_to(m, "Теперь рост (см), только число. Пример: 178", reply_markup=kb)
    except:
        bot.reply_to(m, "Нужно число, например 72")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "height")
def week_menu_height(m):
    if (m.text or "").strip().lower() == "⬅️ назад":
        return week_menu_start(m)
    try:
        h = float(m.text.replace(",", "."))
        USER_FLOW[m.from_user.id]["data"]["height"] = h
        USER_FLOW[m.from_user.id]["step"] = "goal"
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"), KeyboardButton("Набор массы"))
        kb.row(KeyboardButton("⬅️ Назад"))
        bot.reply_to(m, "Выбери цель:", reply_markup=kb)
    except:
        bot.reply_to(m, "Нужно число, например 178")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "goal")
def week_menu_goal(m):
    if (m.text or "").strip().lower() == "⬅️ назад":
        USER_FLOW[m.from_user.id]["step"] = "height"
        return bot.reply_to(m, "Вернулись. Введи рост (см).")
    goal = m.text.strip().lower()
    if goal not in ["похудение","поддержание веса","набор массы"]:
        bot.reply_to(m, "Выбери: Похудение | Поддержание веса | Набор массы"); return
    uid = m.from_user.id
    USER_FLOW[uid]["data"]["goal"] = goal
    USER_FLOW[uid]["step"] = None

    if has_premium(uid):
        plan = generate_week_menu_with_llm(USER_FLOW[uid]["data"], m.from_user.first_name or "пользователь")
        reset_flow(uid)
        bot.send_message(m.chat.id, plan, reply_markup=main_menu(uid))
        return

    # нет премиума — сохраняем анкету и предлагаем оплату
    db = _load(); u = get_user(db, uid)
    u["pending_menu"] = USER_FLOW[uid]["data"]; db[str(uid)] = u; _save(db)
    price = get_current_price()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton(f"Купить премиум за {price} ⭐", callback_data="buy_premium_stars"))
    reset_flow(uid)
    bot.send_message(m.chat.id,
        "🔒 Меню на неделю доступно с премиумом.\n"
        "Оформи премиум — и я сразу сгенерирую меню по твоей анкете.",
        reply_markup=kb
    )

def generate_week_menu_with_llm(data, name="пользователь"):
    weight = data.get("weight"); height = data.get("height"); goal = data.get("goal","поддержание веса")
    if not oa_client:
        return (f"🗓 Меню на неделю для {name}\n"
                f"(вес {weight} кг, рост {height} см, цель: {goal}).\n\n"
                "LLM не подключён (нет OPENAI_API_KEY). Добавь ключ — и я сгенерирую персональное меню.")
    sys = ("Ты — диетолог. Составь меню на 7 дней (завтрак/обед/ужин + перекус). "
           "Дай примерные граммовки и краткие КБЖУ по каждому дню и общую калорийность. Без экзотики.")
    user = (f"Данные пользователя: вес {weight} кг, рост {height} см, цель: {goal}. "
            "Бюджет средний. Продукты из супермаркета.")
    try:
        resp = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            temperature=0.3
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Не удалось сгенерировать меню: {e}"
        # ========= Админка =========
@bot.message_handler(func=lambda m: m.text in ("👨‍💻 Админка","/admin"))
def admin_panel(m):
    reset_flow(m.from_user.id)
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Доступ запрещён.", reply_markup=main_menu(m.from_user.id)); return
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
           InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_premiums"))
    kb.row(InlineKeyboardButton("➕ Выдать премиум (ID)", callback_data="adm_grant"),
           InlineKeyboardButton("➖ Снять премиум (ID)", callback_data="adm_revoke"))
    kb.row(InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"),
           InlineKeyboardButton("💵 Изм. цену (звёзды)", callback_data="adm_price"))
    bot.send_message(m.chat.id, "🔧 Админ-панель", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id): bot.answer_callback_query(c.id, "⛔ Нет доступа."); return
    db = _load()
    if c.data == "adm_users":
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db.keys() if k!='__payments__'])}</b>")
    elif c.data == "adm_premiums":
        now = int(time.time())
        active = sum(1 for u in db.values() if isinstance(u, dict) and u.get("premium") and u.get("premium_until",0)>now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data == "adm_income":
        pays = db.get("__payments__", [])
        bot.send_message(c.message.chat.id, f"💰 Зафиксировано: <b>{sum(p['stars'] for p in pays)} ⭐</b> ({len(pays)} оплат)")
    elif c.data == "adm_grant":
        bot.send_message(c.message.chat.id, "Отправь: `<user_id> [дни]`", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_grant"}
    elif c.data == "adm_revoke":
        bot.send_message(c.message.chat.id, "Отправь: `<user_id>`", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_revoke"}
    elif c.data == "adm_price":
        bot.send_message(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число, напр. 150", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_price"}

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "adm_grant")
def admin_grant_step(m):
    if not is_admin(m.from_user.id): return
    try:
        parts = m.text.split()
        uid = int(parts[0]); days = int(parts[1]) if len(parts)>1 else PREMIUM_DAYS
        set_premium(uid, days)
        bot.reply_to(m, f"✅ Выдан премиум <code>{uid}</code> на {days} дн.", reply_markup=main_menu(m.from_user.id))
        try: bot.send_message(uid, f"✅ Вам выдан премиум на {days} дней администратором.")
        except: pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}")
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "adm_revoke")
def admin_revoke_step(m):
    if not is_admin(m.from_user.id): return
    try:
        uid = int(m.text.strip())
        db = _load(); u = db.get(str(uid))
        if not u: raise ValueError("Пользователь не найден")
        u["premium"] = False; u["premium_until"] = 0; db[str(uid)] = u; _save(db)
        bot.reply_to(m, f"✅ Снят премиум у <code>{uid}</code>.", reply_markup=main_menu(m.from_user.id))
        try: bot.send_message(uid, "❌ Ваш премиум был снят администратором.")
        except: pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}")
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step") == "adm_price")
def admin_price_step(m):
    if not is_admin(m.from_user.id): return
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)  # «на лету»
        bot.reply_to(m, f"✅ Новая цена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}")
    reset_flow(m.from_user.id)

# ========= Мини-веб (для Render Web Service) =========
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
    pass  # если Flask не установлен — игнор (на worker не нужен)

# ========= Авто-перезапуск раз в сутки =========
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)
threading.Thread(target=auto_restart, daemon=True).start()

# ========= Запуск поллинга =========
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
