# =======================
# Calories AI — webhook version with background tasks
# =======================
import os, json, time, threading, base64, re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
import telebot
from telebot import types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ---------- ENV ----------
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")
EXTERNAL_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")  # напр. nutrition-ai-bot.onrender.com
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)

# ---------- SIMPLE DB (json) ----------
DB_FILE = "db.json"
LOCK = threading.RLock()

def _load():
    if not os.path.exists(DB_FILE):
        return {"users": {}, "welcome": DEFAULT_WELCOME, "broadcast_log": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "welcome": DEFAULT_WELCOME, "broadcast_log": []}

def _save(db):
    with LOCK:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

def db_get():
    with LOCK:
        return _load()

def db_set_user(uid, data: dict):
    with LOCK:
        db = _load()
        u = db["users"].get(str(uid), {})
        u.update(data)
        db["users"][str(uid)] = u
        _save(db)

def db_get_user(uid):
    with LOCK:
        db = _load()
        return db["users"].get(str(uid), {})

def db_get_welcome():
    with LOCK:
        db = _load()
        return db.get("welcome", DEFAULT_WELCOME)

def db_set_welcome(text):
    with LOCK:
        db = _load()
        db["welcome"] = text
        _save(db)

# ---------- STATES ----------
USER_FLOW = {}  # uid -> {"step": "...", ...}

def set_step(uid, step, **extra):
    USER_FLOW[uid] = {"step": step, **extra}

def get_step(uid):
    return USER_FLOW.get(uid, {}).get("step")

def reset_flow(uid):
    USER_FLOW.pop(uid, None)

def is_admin(uid):
    return uid in ADMIN_IDS

# ---------- KEYBOARDS ----------
def main_menu(uid=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("📅 Меню на неделю"), KeyboardButton("👨‍🍳 Рецепты от ИИ"))
    if uid and is_admin(uid):
        kb.row(KeyboardButton("🛠 Админ-панель"))
    return kb

def back_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

# Приветствие по умолчанию
DEFAULT_WELCOME = (
    "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
    "• «📸 КБЖУ по фото» — пришли фото блюда\n"
    "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
    "Также подберу <b>меню на 7 дней</b> под твои параметры — «📅 Меню на неделю».\n"
    "«👨‍🍳 Рецепты от ИИ» — бесплатно.\n\n"
    "Премиум открывает доп. функции на 30 дней."
)

# ---------- BG EXECUTOR ----------
EXEC = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "6")))

def run_bg(target, *args, **kwargs):
    EXEC.submit(_safe_wrap, target, *args, **kwargs)

def _safe_wrap(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as e:
        print("BG task error:", e)

def safe_delete(chat_id, message_id):
    try: bot.delete_message(chat_id, message_id)
    except: pass

def safe_edit(chat_id, message_id, text, **kw):
    try:
        bot.edit_message_text(text, chat_id, message_id, **kw)
    except:
        try: bot.send_message(chat_id, text, **kw)
        except: pass

# ---------- OpenAI helpers ----------
def oai_chat(messages, temperature=0.7, max_tokens=800):
    """
    Унифицированный вызов OpenAI Chat (текст).
    """
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("openai text error:", e)
        return None

def oai_vision(prompt_text, image_bytes, temperature=0.2, max_tokens=700):
    """
    Визуальная подсказка: передаём картинку (base64) + текст.
    """
    try:
        import openai, base64
        openai.api_key = OPENAI_API_KEY
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages = [
            {"role":"system","content":"Ты нутрициолог. Определи блюдо/ингредиенты по фото и оцени КБЖУ (ккал, Б/Ж/У)."},
            {"role":"user","content":[
                {"type":"text","text": prompt_text},
                {"type":"image_url","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
            ]}
        ]
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("openai vision error:", e)
        return None

# ---------- Webhook (Flask) ----------
from flask import Flask, request, abort
app = Flask(__name__)

@app.get("/")
def index():
    return "Bot is running", 200

@app.post(f"/tg/{WEBHOOK_SECRET}")
def tg_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)
    update = telebot.types.Update.de_json(request.get_data(as_text=True))
    bot.process_new_updates([update])
    return "ok", 200

def setup_webhook():
    try:
        bot.remove_webhook()  # на всякий
    except Exception as e:
        print("remove_webhook warn:", e)
    url = f"https://{EXTERNAL_HOST}/tg/{WEBHOOK_SECRET}"
    ok = bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
        max_connections=40
    )
    print("✅ Webhook set:", ok)
    # ========== START / BACK ==========
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.from_user.id
    reset_flow(uid)
    bot.send_message(m.chat.id, db_get_welcome(), reply_markup=main_menu(uid))

@bot.message_handler(func=lambda m: m.text == "⬅️ Назад")
def go_back(m):
    uid = m.from_user.id
    reset_flow(uid)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(uid))

# ========== PROFILE (для плана) ==========
def profile_complete(uid):
    u = db_get_user(uid)
    need = all(k in u for k in ("sex","height","weight","goal"))
    return need

def ask_profile(uid, chat_id):
    set_step(uid, "sex")
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("👨 Мужчина"), KeyboardButton("👩 Женщина"))
    kb.row(KeyboardButton("⬅️ Назад"))
    bot.send_message(chat_id, "Выберите пол:", reply_markup=kb)

@bot.message_handler(func=lambda m: get_step(m.from_user.id) == "sex")
def prof_sex(m):
    uid = m.from_user.id
    if m.text not in ["👨 Мужчина","👩 Женщина"]:
        bot.reply_to(m, "Выберите на клавиатуре.", reply_markup=back_menu()); return
    sex = "male" if "Мужчина" in m.text else "female"
    db_set_user(uid, {"sex": sex})
    set_step(uid, "height")
    msg = bot.send_message(m.chat.id, "Введите рост (см):", reply_markup=back_menu())
    bot.register_next_step_handler(msg, prof_height)

def prof_height(m):
    uid = m.from_user.id
    try:
        h = int(re.sub(r"\D+","", m.text))
        if not (120 <= h <= 230): raise ValueError
        db_set_user(uid, {"height": h})
        set_step(uid, "weight")
        msg = bot.send_message(m.chat.id, "Введите вес (кг):", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_weight)
    except:
        msg = bot.reply_to(m, "Введите число, напр. 178", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_height)

def prof_weight(m):
    uid = m.from_user.id
    try:
        w = float(re.sub(r"[^\d\.]+","", m.text))
        if not (35 <= w <= 300): raise ValueError
        db_set_user(uid, {"weight": w})
        set_step(uid, "goal")
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"), KeyboardButton("Набор массы"))
        kb.row(KeyboardButton("⬅️ Назад"))
        bot.send_message(m.chat.id, "Выберите цель:", reply_markup=kb)
    except:
        msg = bot.reply_to(m, "Введите число, напр. 74", reply_markup=back_menu())
        bot.register_next_step_handler(msg, prof_weight)

@bot.message_handler(func=lambda m: get_step(m.from_user.id) == "goal")
def prof_goal(m):
    uid = m.from_user.id
    opts = ["Похудение","Поддержание веса","Набор массы"]
    if m.text not in opts:
        bot.reply_to(m, "Выберите на клавиатуре.", reply_markup=back_menu()); return
    goal_map = {"Похудение":"cut","Поддержание веса":"maintain","Набор массы":"bulk"}
    db_set_user(uid, {"goal": goal_map[m.text]})
    reset_flow(uid)
    bot.send_message(m.chat.id, "Готово! Анкета сохранена ✅", reply_markup=main_menu(uid))

# ========== КБЖУ по СПИСКУ ==========
@bot.message_handler(func=lambda m: m.text == "🧾 КБЖУ по списку")
def kbju_list_start(m):
    uid = m.from_user.id
    set_step(uid, "kbju_list")
    bot.send_message(
        m.chat.id,
        "Пришли список в формате: «Продукт 120 г; ...».\nПример:\n"
        "Кур. грудка 150 г; Рис 180 г; Салат 120 г",
        reply_markup=back_menu()
    )

@bot.message_handler(func=lambda m: get_step(m.from_user.id) == "kbju_list")
def kbju_list_calc(m):
    uid = m.from_user.id
    text = (m.text or "").strip()
    if not text or "Назад" in text:
        reset_flow(uid)
        bot.send_message(m.chat.id, "Отменил. Возвращаю в меню.", reply_markup=main_menu(uid))
        return

    wait = bot.send_message(m.chat.id, "🧠 Считаю КБЖУ по списку…", reply_markup=back_menu())
    run_bg(_kbju_by_list_bg, m, wait.message_id)

def _kbju_by_list_bg(m, wait_id):
    chat_id = m.chat.id
    uid = m.from_user.id
    try:
        user = db_get_user(uid)
        prompt = (
            "Ты нутрициолог. Посчитай суммарное КБЖУ (ккал/б/ж/у) по списку продуктов с граммовками.\n"
            "Если встречается «ложка/щепотка» — оцени разумно.\n"
            "Верни:\n"
            "1) Краткий разбор каждого пункта (ккал/Б/Ж/У)\n"
            "2) Итого (ккал и Б/Ж/У)\n\n"
            f"Список: {m.text}"
        )
        res = oai_chat([{"role":"user","content":prompt}], temperature=0.2, max_tokens=900)
        if not res:
            raise RuntimeError("AI calc failed")
        safe_delete(chat_id, wait_id)
        reset_flow(uid)
        bot.send_message(chat_id, res, reply_markup=main_menu(uid))
    except Exception as e:
        print("kbju_list error:", e)
        safe_edit(chat_id, wait_id, "⚠️ Ошибка. Попробуй ещё раз.", reply_markup=main_menu(uid))
        reset_flow(uid)
        # ========== КБЖУ по ФОТО ==========
@bot.message_handler(func=lambda m: m.text == "📸 КБЖУ по фото")
def kbju_photo_prompt(m):
    bot.send_message(m.chat.id, "Пришли фото блюда. Можно добавить подпись с ингредиентами.", reply_markup=back_menu())

@bot.message_handler(content_types=['photo'])
def kbju_photo_received(m):
    wait = bot.send_message(m.chat.id, "🧠 Начинаю анализ изображения на КБЖУ…", reply_markup=back_menu())
    try:
        file_id = m.photo[-1].file_id
    except:
        safe_edit(m.chat.id, wait.message_id, "Нужно фото.", reply_markup=main_menu(m.from_user.id))
        return
    run_bg(_kbju_from_photo_bg, m, wait.message_id, file_id)

def _kbju_from_photo_bg(m, wait_id, file_id):
    chat_id = m.chat.id
    uid = m.from_user.id
    try:
        # скачиваем файл
        f = bot.get_file(file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
        img = requests.get(url, timeout=20).content

        caption = m.caption or ""
        prompt = (
            "Определи блюдо и перечисли основные ингредиенты.\n"
            "Дай оценку КБЖУ порции (ккал, Б/Ж/У). Если уверенность низкая — укажи это и предложи уточнить состав.\n"
            "Формат:\n"
            "Название\nИнгредиенты\nОценка: ~XXX ккал, Б/Ж/У xx/xx/xx\nКраткий комментарий."
        )
        res = oai_vision(prompt, img)
        if not res:
            raise RuntimeError("vision failed")
        safe_delete(chat_id, wait_id)
        bot.send_message(chat_id, res, reply_markup=main_menu(uid))
    except Exception as e:
        print("photo kbju error:", e)
        safe_edit(chat_id, wait_id, "⚠️ Не удалось распознать фото. Попробуй ещё раз.", reply_markup=main_menu(uid))

# ========== РЕЦЕПТЫ ==========
@bot.message_handler(func=lambda m: m.text == "👨‍🍳 Рецепты от ИИ")
def recipes_menu(m):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🍽 Рецепт по запросу"), KeyboardButton("🔥 Рецепт на N ккал"))
    kb.row(KeyboardButton("⬅️ Назад"))
    bot.send_message(m.chat.id, "Выбери вид рецепта:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "🍽 Рецепт по запросу")
def recipe_freeform(m):
    msg = bot.send_message(m.chat.id, "Напиши, что именно хочешь (например «блинчики без сахара»):", reply_markup=back_menu())
    bot.register_next_step_handler(msg, _recipe_freeform_step)

def _recipe_freeform_step(m):
    query = (m.text or "").strip()
    if not query or "Назад" in query:
        bot.send_message(m.chat.id, "Отменил.", reply_markup=main_menu(m.from_user.id))
        return
    wait = bot.send_message(m.chat.id, "🧠 Создаю рецепт…", reply_markup=back_menu())
    run_bg(_make_recipe_bg, m, wait.message_id, {"type":"freeform", "q":query})

@bot.message_handler(func=lambda m: m.text == "🔥 Рецепт на N ккал")
def recipe_kcal(m):
    msg = bot.send_message(m.chat.id, "Введи целевую калорийность (например 600):", reply_markup=back_menu())
    bot.register_next_step_handler(msg, _recipe_kcal_step)

def _recipe_kcal_step(m):
    try:
        kcal = int(''.join([c for c in m.text if c.isdigit()]))
        wait = bot.send_message(m.chat.id, "🧠 Создаю рецепт…", reply_markup=back_menu())
        run_bg(_make_recipe_bg, m, wait.message_id, {"type":"kcal", "kcal":kcal})
    except:
        bot.reply_to(m, "Нужно число, например 600.", reply_markup=back_menu())

def _make_recipe_bg(m, wait_id, params:dict):
    chat_id = m.chat.id
    uid = m.from_user.id
    try:
        if params["type"] == "freeform":
            prompt = (
                "Ты нутрициолог и шеф.\nСгенерируй один понятный рецепт под запрос пользователя.\n"
                "Верни список ингредиентов (с граммами), пошаговое приготовление и оценку КБЖУ (ккал, Б/Ж/У).\n"
                f"Запрос: {params['q']}"
            )
        else:
            prompt = (
                "Ты нутрициолог и шеф.\nСгенерируй интересный рецепт на указанную калорийность (+/- 5%).\n"
                "Верни ингредиенты (г/шт), шаги, оценку КБЖУ и короткий совет по замене/подстройке.\n"
                f"Цель: {params['kcal']} ккал."
            )
        res = oai_chat([{"role":"user","content":prompt}], temperature=0.6, max_tokens=1000)
        if not res:
            raise RuntimeError("recipe failed")
        safe_delete(chat_id, wait_id)
        bot.send_message(chat_id, res, reply_markup=main_menu(uid))
    except Exception as e:
        print("recipe error:", e)
        safe_edit(chat_id, wait_id, "⚠️ Не удалось сгенерировать рецепт. Попробуй ещё раз.", reply_markup=main_menu(uid))

# ========== МЕНЮ НА НЕДЕЛЮ ==========
@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def week_menu(m):
    uid = m.from_user.id
    if not profile_complete(uid):
        ask_profile(uid, m.chat.id)
        return
    wait = bot.send_message(m.chat.id, "🧠 Создаю план под вас! Это может занять 5–15 секунд…", reply_markup=back_menu())
    run_bg(_build_week_plan_bg, m, wait.message_id)

def _build_week_plan_bg(m, wait_id):
    chat_id = m.chat.id
    uid = m.from_user.id
    try:
        u = db_get_user(uid)
        sex = "мужчина" if u["sex"]=="male" else "женщина"
        goal_map = {"cut":"похудение","maintain":"поддержание веса","bulk":"набор массы"}
        goal = goal_map[u["goal"]]
        prompt = (
            "Ты профессиональный нутрициолог.\n"
            "Составь подробный план питания на 7 дней в виде:\n"
            "День X (Пн/Вт/…):\n- Завтрак: ... (ккал, Б/Ж/У)\n- Перекус: ...\n- Обед: ...\n- Перекус: ...\n- Ужин: ...\nИтого за день: ккал, Б/Ж/У\n\n"
            "Учитывай цель (дефицит/поддержание/профицит) и параметры. Укажи примерно 3–5 приёмов пищи в день.\n"
            "Добавь короткие подсказки по замене продуктов.\n\n"
            f"Параметры: пол — {sex}, рост — {u['height']} см, вес — {u['weight']} кг, цель — {goal}."
        )
        res = oai_chat([{"role":"user","content":prompt}], temperature=0.5, max_tokens=2200)
        if not res:
            raise RuntimeError("week plan failed")
        safe_delete(chat_id, wait_id)
        bot.send_message(chat_id, res, reply_markup=main_menu(uid))
    except Exception as e:
        print("week plan error:", e)
        safe_edit(chat_id, wait_id, "⚠️ Не удалось построить план. Попробуй ещё раз.", reply_markup=main_menu(uid))
        # ========== АДМИНКА ==========
@bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель")
def adm_panel(m):
    uid = m.from_user.id
    if not is_admin(uid):
        bot.reply_to(m, "Доступ только админам.", reply_markup=main_menu(uid)); return
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("👥 Пользователи"), KeyboardButton("📣 Рассылка"))
    kb.row(KeyboardButton("✏️ Сменить приветствие"))
    kb.row(KeyboardButton("⬅️ Назад"))
    bot.send_message(m.chat.id, "Админ-панель", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "👥 Пользователи")
def adm_users(m):
    if not is_admin(m.from_user.id): return
    db = db_get()
    bot.send_message(m.chat.id, f"Всего пользователей: <b>{len(db.get('users',{}))}</b>", reply_markup=back_menu())

@bot.message_handler(func=lambda m: m.text == "📣 Рассылка")
def adm_broadcast(m):
    if not is_admin(m.from_user.id): return
    set_step(m.from_user.id, "adm_broadcast")
    bot.send_message(m.chat.id, "Пришлите текст рассылки (HTML разрешён).", reply_markup=back_menu())

@bot.message_handler(func=lambda m: get_step(m.from_user.id) == "adm_broadcast")
def adm_broadcast_send(m):
    uid = m.from_user.id
    if "Назад" in (m.text or ""):
        reset_flow(uid); bot.send_message(m.chat.id, "Отменено.", reply_markup=main_menu(uid)); return
    reset_flow(uid)
    run_bg(_broadcast_bg, m)
    bot.send_message(m.chat.id, "🚀 Запустил рассылку в фоне.", reply_markup=main_menu(uid))

def _broadcast_bg(m):
    text = m.text
    db = db_get()
    users = list(db.get("users", {}).keys())
    sent = 0
    for suid in users:
        try:
            bot.send_message(int(suid), text, disable_web_page_preview=True)
            sent += 1
            time.sleep(0.03)
        except Exception as e:
            print("broadcast err to", suid, e)
    with LOCK:
        db = _load()
        db["broadcast_log"].append({"at": datetime.utcnow().isoformat(), "sent": sent})
        _save(db)
    try:
        bot.send_message(m.chat.id, f"Готово. Отправлено: <b>{sent}</b>")
    except: pass

@bot.message_handler(func=lambda m: m.text == "✏️ Сменить приветствие")
def adm_welcome(m):
    if not is_admin(m.from_user.id): return
    set_step(m.from_user.id, "adm_welcome")
    bot.send_message(m.chat.id, "Пришлите новый текст приветствия (HTML ок).", reply_markup=back_menu())

@bot.message_handler(func=lambda m: get_step(m.from_user.id) == "adm_welcome")
def adm_welcome_set(m):
    uid = m.from_user.id
    if "Назад" in (m.text or ""):
        reset_flow(uid); bot.send_message(m.chat.id, "Отменено.", reply_markup=main_menu(uid)); return
    db_set_welcome(m.text)
    reset_flow(uid)
    bot.send_message(m.chat.id, "Готово. Новый текст сохранён ✅", reply_markup=main_menu(uid))
    # ========== AUTO-REGISTER USER ==========
@bot.message_handler(func=lambda m: True, content_types=['text','photo','document','audio','video','sticker','voice'])
def ensure_user(m):
    """
    Перехватчик в самом конце: регистрируем юзера в БД при первом действии.
    Возвращает управление предыдущим хендлерам (мы уже всё описали выше),
    поэтому тут только быстрая запись и no-op.
    """
    try:
        u = db_get_user(m.from_user.id)
        if not u:
            db_set_user(m.from_user.id, {
                "first_name": m.from_user.first_name,
                "username": m.from_user.username,
                "created_at": datetime.utcnow().isoformat()
            })
    except Exception as e:
        print("ensure_user err:", e)
    # НИЧЕГО не отвечаем — чтобы не мешать конкретным хендлерам.

# ========== MAIN ==========
if __name__ == "__main__":
    print("Starting (webhook)…")
    setup_webhook()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
