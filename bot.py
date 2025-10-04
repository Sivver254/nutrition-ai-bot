# bot.py — Nutrition AI Bot: Stars + Admin Premium + AI everywhere
import os, json, time, datetime, threading, re
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ====== КОНФИГ ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
DATA_FILE = "users.json"

def _parse_admins():
    ids = set()
    if os.getenv("ADMIN_ID"):
        try: ids.add(int(os.getenv("ADMIN_ID"))); 
        except: pass
    if os.getenv("ADMIN_IDS"):
        for x in os.getenv("ADMIN_IDS").split(","):
            x = x.strip()
            if x.isdigit(): ids.add(int(x))
    if not ids: ids.add(123456789)  # подстраховка
    return ids

ADMIN_IDS = _parse_admins()
def is_admin(uid:int) -> bool: return uid in ADMIN_IDS

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
USER_FLOW = {}     # простая FSM
WELCOME_TEXT = ("Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
                "• «📸 КБЖУ по фото» — пришли фото блюда\n"
                "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
                "Могу подобрать меню на 7 дней под твои параметры — «📅 Меню на неделю».\n"
                "«👨‍🍳 Рецепты от ИИ» — бесплатно.\n"
                "Премиум открывает доп. функции на 30 дней.")

# ====== МИНИ-БД ======
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
    # админам — авто-премиум
    if is_admin(uid):
        db = _load()
        u = get_user(db, uid)
        u["premium"] = True
        u["premium_until"] = int(time.time()) + 10*365*24*3600
        db[str(uid)] = u
        _save(db)
        return True
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

# ====== ИИ (OpenAI) ======
def _get_openai_client():
    key = os.getenv("OPENAI_API_KEY") or os.getenv("openai_api_key")
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None

def ai_complete(system_prompt: str, user_prompt: str, max_tokens=600, temperature=0.4):
    client = _get_openai_client()
    if not client:
        return None
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user","content":user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return None

def ai_parse_food_list(text: str):
    client = _get_openai_client()
    if not client: return None
    prompt = (
        "Парсинг списка продуктов. Верни JSON-массив объектов [{\"name\":\"строка\",\"grams\":число}]. "
        "Если граммы не указаны — поставь 100. Игнорируй эмодзи и лишние слова. "
        "Ответь ТОЛЬКО JSON без комментариев.\n\n"
        f"Ввод:\n{text}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0.1,
            max_tokens=350
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r'\[.*\]', raw, re.S)
        if not m: return None
        arr = json.loads(m.group(0))
        out = []
        for it in arr:
            name = str(it.get("name","")).strip()
            grams = it.get("grams", 100)
            try: grams = int(float(grams))
            except: grams = 100
            if name: out.append({"name":name, "grams":grams})
        return out if out else None
    except Exception:
        return None

def ai_recipe_suggestion(query: str = "", target_kcal: int | None = None) -> str:
    client = _get_openai_client()
    if not client:
        base = ("👨‍🍳 Пример: Омлет с овощами — 2 яйца, 80 г перца, 50 г томатов, 10 г масла.\n"
                "Обжарь овощи, залей яйцами, доведи под крышкой. ~350 ккал, Б/Ж/У 22/26/6.")
        if target_kcal: base += f"\nЦель ≈ {target_kcal} ккал."
        return base
    sys = ("Ты повар-нутрициолог. Дай компактный рецепт: название, ингредиенты с граммами, "
           "5–7 шагов, итоговые ккал и Б/Ж/У. Если задана целевая калорийность — подгони порцию.")
    user = "Придумай рецепт."
    if query: user = f"Придумай рецепт: {query}."
    if target_kcal: user += f" Цель: {target_kcal} ккал."
    txt = ai_complete(sys, user, max_tokens=700, temperature=0.6)
    return ("👨‍🍳 " + txt) if txt else "⚠️ Не получилось сгенерировать рецепт."

def ai_estimate_kbju_from_image(image_url: str) -> str | None:
    client = _get_openai_client()
    if not client: return None
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":
                 "Ты нутрициолог. По фото прикинь калории и Б/Ж/У на порцию. Кратко."},
                {"role":"user","content":[
                    {"type":"text","text":"Оцени калории и Б/Ж/У на фото."},
                    {"type":"image_url","image_url":{"url": image_url}}
                ]}
            ],
            temperature=0.2,
            max_tokens=300
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return None
        # ====== КНОПКИ ======
def back_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def main_menu(uid:int=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("🍳 Рецепты от ИИ"), KeyboardButton("📅 Меню на неделю"))
    if uid and is_admin(uid):
        kb.row(KeyboardButton("🛠 Админка"))
    return kb

# ====== СТАРТ ======
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); get_user(db, m.from_user.id); _save(db)
    bot.send_message(m.chat.id, WELCOME_TEXT, reply_markup=main_menu(m.from_user.id))

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
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_premium_stars")
    )
    bot.send_message(m.chat.id,
        f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐",
        reply_markup=kb
    )

# ====== КБЖУ ПО СПИСКУ ======
@bot.message_handler(func=lambda m: m.text == "🧾 КБЖУ по списку")
def kbju_list_start(m):
    USER_FLOW[m.from_user.id] = {"step":"kbju_list_wait"}
    bot.reply_to(m, "Пришли список в формате: «Продукт 120 г; ...». "
                    "Можно писать свободно — ИИ поймёт.", reply_markup=back_keyboard())

# грубый расчёт (пример)
NORM_DB = {
    "курица": (165, 31, 4, 0),
    "рис": (130, 2.7, 0.3, 28),
    "гречка": (110, 3.6, 1.1, 21),
    "яйцо": (157, 13, 11, 1.1),
    "творог": (156, 18, 5, 3)
}
def norm_name(x:str):
    x=x.lower()
    for k in NORM_DB:
        if k in x: return k
    return None

def estimate_kbju(items):
    # items: [{'name':..., 'grams':...}]
    kcal=p=f=c=0.0; det=[]
    for it in items:
        nm = norm_name(it.get("name",""))
        g = float(it.get("grams",0) or 0)
        if not nm or g<=0: 
            det.append(f"• {it.get('name','?')} {int(g)} г — нет в базе (учтено 0)")
            continue
        K,P,F,C = NORM_DB[nm]
        mul = g/100.0
        kcal += K*mul; p+=P*mul; f+=F*mul; c+=C*mul
        det.append(f"• {it['name']} {int(g)} г ≈ {int(K*mul)} ккал")
    return int(kcal), int(p), int(f), int(c), "\n".join(det)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="kbju_list_wait")
def kbju_list_calc(m):
    if m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id)); return
    try:
        items = ai_parse_food_list(m.text) or []
        if not items:
            # наивный фолбэк: "название 120 г; ..."
            items=[]
            for part in re.split(r"[;,]\s*", m.text.strip()):
                if not part: continue
                g_m = re.search(r"(\d+)\s*г", part.lower())
                grams = int(g_m.group(1)) if g_m else 100
                name = re.sub(r"\d+\s*г","",part, flags=re.I).strip()
                items.append({"name":name, "grams":grams})
        kcal, p, f, c, det = estimate_kbju(items)
        bot.reply_to(m, f"~{kcal} ккал, Б/Ж/У {p}/{f}/{c}\n{det}", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
    finally:
        USER_FLOW.pop(m.from_user.id, None)

# ====== КБЖУ ПО ФОТО ======
@bot.message_handler(content_types=['photo'])
def on_photo(m):
    try:
        file_id = m.photo[-1].file_id
        f = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    except Exception as e:
        bot.reply_to(m, f"⚠️ Не удалось получить фото: {e}", reply_markup=main_menu(m.from_user.id)); return

    ai_text = ai_estimate_kbju_from_image(file_url)
    if ai_text:
        bot.reply_to(m, f"🧠 {ai_text}", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "🤖 Не удалось распознать. Пришли названия и граммы: «Кур. грудка 150 г; Рис 180 г; ...»",
                     reply_markup=main_menu(m.from_user.id))

# ====== РЕЦЕПТЫ ======
@bot.message_handler(func=lambda m: m.text == "🍳 Рецепты от ИИ")
def recipes_entry(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🔍 Рецепт по запросу"), KeyboardButton("🔥 Рецепт на N ккал"))
    kb.row(KeyboardButton("⬅️ Назад"))
    USER_FLOW[m.from_user.id] = {"step":"recipes_menu"}
    bot.reply_to(m, "Выбери режим:", reply_markup=kb)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipes_menu")
def recipes_menu_handler(m):
    if m.text == "🔍 Рецепт по запросу":
        USER_FLOW[m.from_user.id] = {"step":"recipe_query"}
        bot.reply_to(m, "Напиши запрос: «блинчики без сахара», «паста с курицей»…", reply_markup=back_keyboard())
    elif m.text == "🔥 Рецепт на N ккал":
        USER_FLOW[m.from_user.id] = {"step":"recipe_kcal"}
        bot.reply_to(m, "На сколько калорий нужен рецепт? Например 600", reply_markup=back_keyboard())
    elif m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None)
        bot.reply_to(m, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipe_query")
def recipe_query_step(m):
    if m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None); bot.reply_to(m, "Окей.", reply_markup=main_menu(m.from_user.id)); return
    ans = ai_recipe_suggestion(query=m.text.strip())
    bot.reply_to(m, ans, reply_markup=main_menu(m.from_user.id))
    USER_FLOW.pop(m.from_user.id, None)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="recipe_kcal")
def recipe_kcal_step(m):
    if m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None); bot.reply_to(m, "Окей.", reply_markup=main_menu(m.from_user.id)); return
    try:
        n = int(float(m.text.strip()))
        ans = ai_recipe_suggestion(target_kcal=n)
        bot.reply_to(m, ans, reply_markup=main_menu(m.from_user.id))
        USER_FLOW.pop(m.from_user.id, None)
    except:
        bot.reply_to(m, "Введи просто число, например 600.", reply_markup=back_keyboard())

# ====== МЕНЮ НА НЕДЕЛЮ (анкета → проверка премиума) ======
@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def weekly_menu_entry(m):
    USER_FLOW[m.from_user.id] = {"step":"wq_height"}
    bot.reply_to(m, "Введи свой рост в сантиметрах:", reply_markup=back_keyboard())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="wq_height")
def wq_height(m):
    if m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None); bot.reply_to(m,"Окей.", reply_markup=main_menu(m.from_user.id)); return
    try:
        h = int(float(m.text.strip()))
        USER_FLOW[m.from_user.id] = {"step":"wq_weight", "height":h}
        bot.reply_to(m, "Вес (кг):", reply_markup=back_keyboard())
    except:
        bot.reply_to(m, "Введи число в см.", reply_markup=back_keyboard())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="wq_weight")
def wq_weight(m):
    try:
        w = int(float(m.text.strip()))
        s = USER_FLOW[m.from_user.id]; s["weight"]=w; s["step"]="wq_goal"
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"), KeyboardButton("Набор массы"))
        kb.row(KeyboardButton("⬅️ Назад"))
        bot.reply_to(m, "Цель?", reply_markup=kb)
    except:
        bot.reply_to(m, "Введи число в кг.", reply_markup=back_keyboard())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="wq_goal")
def wq_goal(m):
    if m.text == "⬅️ Назад":
        USER_FLOW.pop(m.from_user.id, None); bot.reply_to(m,"Окей.", reply_markup=main_menu(m.from_user.id)); return
    goal = m.text.strip().lower()
    s = USER_FLOW.get(m.from_user.id, {})
    s["goal"]=goal

    if not has_premium(m.from_user.id):
        price = get_current_price()
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton(f"Купить за {price} ⭐", callback_data="buy_premium_stars")
        )
        bot.send_message(m.chat.id, "🔒 Для генерации меню нужен премиум. Админам уже открыт доступ.",
                         reply_markup=kb)
        USER_FLOW.pop(m.from_user.id, None)
        return

    # простая заглушка генерации
    h, w = s.get("height"), s.get("weight")
    plan = (f"📅 Меню (черновик): цель — {goal}\n"
            f"- День 1: овсянка, курица с рисом, творог\n"
            f"- День 2: яйца, гречка с индейкой, салат\n"
            f"- День 3: сырники, паста с тунцом, кефир\n"
            f"(рост {h} см, вес {w} кг)")
    bot.reply_to(m, plan, reply_markup=main_menu(m.from_user.id))
    USER_FLOW.pop(m.from_user.id, None)
    # ====== ОПЛАТА TELEGRAM STARS ======
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]  # amount=звёзды
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",   # Stars — без токена
        currency="XTR",
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
            if total: log_payment(m.from_user.id, total, payload)
            db = _load(); u = db.get(str(m.from_user.id), {})
            exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
            bot.send_message(m.from_user.id, f"✅ Оплата получена! Премиум активен до <b>{exp}</b>.",
                             reply_markup=main_menu(m.from_user.id))
        else:
            if total: log_payment(m.from_user.id, total, payload)
            bot.send_message(m.from_user.id, "✅ Оплата получена.", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.send_message(m.chat.id, f"⚠️ Ошибка обработки платежа: {e}", reply_markup=main_menu(m.from_user.id))

# ====== АДМИНКА ======
@bot.message_handler(func=lambda m: m.text in ("🛠 Админка", "/admin"))
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
        InlineKeyboardButton("✏️ Сменить приветствие", callback_data="adm_welcome")
    )
    kb.row(
        InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"),
        InlineKeyboardButton("🔄 Обновить цену", callback_data="adm_price")
    )
    bot.send_message(m.chat.id, "🛠 Админ-панель", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "⛔ Нет доступа."); return
    db = _load()
    if c.data == "adm_users":
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{len([k for k in db if k!='__payments__'])}</b>")
    elif c.data == "adm_premiums":
        now = int(time.time())
        active = sum(1 for u in db.values() if isinstance(u, dict) and u.get("premium") and u.get("premium_until",0)>now)
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")
    elif c.data == "adm_income":
        pays = db.get("__payments__", [])
        total = sum(p.get("stars",0) for p in pays)
        bot.send_message(c.message.chat.id, f"💰 Зафиксировано локально: <b>{total} ⭐</b> ({len(pays)} оплат)")
    elif c.data == "adm_price":
        bot.send_message(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число:", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_price"}
    elif c.data == "adm_broadcast":
        bot.send_message(c.message.chat.id, "Введи текст рассылки. Будет отправлен всем пользователям.", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_bc"}
    elif c.data == "adm_welcome":
        bot.send_message(c.message.chat.id, "Введи новый текст приветствия (заменит текущее /start):", parse_mode=None)
        USER_FLOW[c.from_user.id] = {"step":"adm_welcome"}

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="adm_price")
def admin_price_step(m):
    if not is_admin(m.from_user.id): return
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)
        bot.reply_to(m, f"✅ Новая цена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
    finally:
        USER_FLOW.pop(m.from_user.id, None)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="adm_bc")
def admin_bc_step(m):
    if not is_admin(m.from_user.id): return
    msg = m.text
    db = _load()
    ok=0; fail=0
    for k,v in list(db.items()):
        if k == "__payments__": continue
        try:
            uid = int(k)
            bot.send_message(uid, msg)
            ok+=1
        except:
            fail+=1
            continue
    bot.reply_to(m, f"📣 Разослано: {ok}, ошибок: {fail}", reply_markup=main_menu(m.from_user.id))
    USER_FLOW.pop(m.from_user.id, None)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id,{}).get("step")=="adm_welcome")
def admin_welcome_step(m):
    if not is_admin(m.from_user.id): return
    global WELCOME_TEXT
    WELCOME_TEXT = m.text.strip()
    bot.reply_to(m, "✅ Приветствие обновлено.", reply_markup=main_menu(m.from_user.id))
    USER_FLOW.pop(m.from_user.id, None)

# ====== МИНИ-ВЕБ (Render пинг) ======
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
    pass

# ====== АВТО-ПЕРЕЗАПУСК РАЗ В СУТКИ ======
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)

threading.Thread(target=auto_restart, daemon=True).start()

# ====== ЗАПУСК ПОЛЛИНГА ======
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90, long_polling_timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
