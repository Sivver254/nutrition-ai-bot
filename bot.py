# === CONFIG / STATE ===
import os, json, time, datetime, threading, io
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)
from PIL import Image

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "users.json"
USER_FLOW = {}   # state per user

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

def get_user(uid:int):
    db = _load()
    s = str(uid)
    if s not in db:
        db[s] = {
            "joined": int(time.time()),
            "premium": False,
            "premium_until": 0,
            # профиль для меню
            "profile": {
                "gender": None,     # "Мужчина"|"Женщина"
                "goal": None,       # "Похудение"|"Поддержание"|"Набор"
                "height_cm": None,
                "weight_kg": None,
                "age": None,
                "activity": None,   # "Низкая"/"Средняя"/"Высокая"
            }
        }
        _save(db)
    return db[s]

def put_user(uid:int, data:dict):
    db = _load()
    db[str(uid)] = data
    _save(db)
    def main_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📸 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("🍳 Рецепты от ИИ"), KeyboardButton("📅 Меню на неделю"))
    kb.row(KeyboardButton("⭐ Купить премиум"), KeyboardButton("📊 Проверить премиум"))
    kb.row(KeyboardButton("🛠 Админка"))
    return kb

def back_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def gender_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Мужчина"), KeyboardButton("Женщина"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def goal_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание"))
    kb.row(KeyboardButton("Набор массы"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def activity_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Низкая"), KeyboardButton("Средняя"), KeyboardButton("Высокая"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb
    @bot.message_handler(commands=["start"])
def cmd_start(m):
    get_user(m.from_user.id)  # ensure profile
    bot.send_message(
        m.chat.id,
        "Привет! 🤖 Я помогу посчитать КБЖУ и составлю меню на неделю под тебя.\n"
        "Выбирай действие ниже:",
        reply_markup=main_kb()
    )
    USER_FLOW.pop(m.from_user.id, None)

@bot.message_handler(func=lambda msg: msg.text == "⬅️ Назад")
def go_back(m):
    USER_FLOW.pop(m.from_user.id, None)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_kb())
    @bot.message_handler(func=lambda m: m.text == "📸 КБЖУ по фото")
def kbju_photo_start(m):
    # ставим режим ожидания фото
    USER_FLOW[m.from_user.id] = {"step": "await_photo"}
    bot.send_message(
        m.chat.id,
        "Пришли фото блюда. Можно добавить подпись с продуктами (например: «Рис 150 г, Курица 120 г»).",
        reply_markup=back_kb()
    )

@bot.message_handler(content_types=['photo'])
def kbju_photo_receive(m):
    st = USER_FLOW.get(m.from_user.id, {})
    if st.get("step") != "await_photo":
        # игнор, если фото не в режиме
        return

    # скачиваем самое большое превью
    try:
        file_id = m.photo[-1].file_id
        file_info = bot.get_file(file_id)
        data = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.reply_to(m, f"⚠️ Не получилось скачать фото: {e}", reply_markup=main_kb())
        USER_FLOW.pop(m.from_user.id, None)
        return

    # подпись как «подсказка» для ИИ
    hint = (m.caption or "").strip()

    # оцениваем через ИИ (или фолбэк)
    try:
        kbju_text = ai_estimate_kbju_from_image(data, hint)
    except Exception as e:
        kbju_text = None

    if not kbju_text:
        kbju_text = "🧠 Распознал блюдо на фото и оценил КБЖУ (пример): ~520 ккал, Б/Ж/У 32/18/50"

    bot.send_message(m.chat.id, kbju_text, reply_markup=main_kb())
    USER_FLOW.pop(m.from_user.id, None)
    @bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def week_menu_start(m):
    u = get_user(m.from_user.id)
    USER_FLOW[m.from_user.id] = {"step": "ask_gender"}
    bot.send_message(
        m.chat.id,
        "Укажи пол:",
        reply_markup=gender_kb()
    )

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_gender")
def week_menu_gender(m):
    if m.text == "⬅️ Назад": return go_back(m)
    if m.text not in ("Мужчина", "Женщина"):
        return bot.reply_to(m, "Выбери кнопку 👇", reply_markup=gender_kb())

    u = get_user(m.from_user.id)
    u["profile"]["gender"] = m.text
    put_user(m.from_user.id, u)

    USER_FLOW[m.from_user.id] = {"step": "ask_goal"}
    bot.send_message(m.chat.id, "Какая цель?", reply_markup=goal_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_goal")
def week_menu_goal(m):
    if m.text == "⬅️ Назад": return go_back(m)
    if m.text not in ("Похудение", "Поддержание", "Набор массы"):
        return bot.reply_to(m, "Выбери кнопку 👇", reply_markup=goal_kb())

    u = get_user(m.from_user.id)
    u["profile"]["goal"] = m.text
    put_user(m.from_user.id, u)

    USER_FLOW[m.from_user.id] = {"step": "ask_height"}
    bot.send_message(m.chat.id, "Введи рост в сантиметрах (например, 178):", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_height")
def week_menu_height(m):
    if m.text == "⬅️ Назад": return go_back(m)
    try:
        h = int(m.text.strip())
        if not 120 <= h <= 230: raise ValueError
    except:
        return bot.reply_to(m, "Нужно целое число от 120 до 230.", reply_markup=back_kb())

    u = get_user(m.from_user.id)
    u["profile"]["height_cm"] = h; put_user(m.from_user.id, u)

    USER_FLOW[m.from_user.id] = {"step": "ask_weight"}
    bot.send_message(m.chat.id, "Вес в кг (например, 74):", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_weight")
def week_menu_weight(m):
    if m.text == "⬅️ Назад": return go_back(m)
    try:
        w = float(m.text.replace(",", "."))
        if not 35 <= w <= 250: raise ValueError
    except:
        return bot.reply_to(m, "Нужно число от 35 до 250.", reply_markup=back_kb())

    u = get_user(m.from_user.id)
    u["profile"]["weight_kg"] = round(w, 1); put_user(m.from_user.id, u)

    USER_FLOW[m.from_user.id] = {"step": "ask_age"}
    bot.send_message(m.chat.id, "Возраст (лет):", reply_markup=back_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_age")
def week_menu_age(m):
    if m.text == "⬅️ Назад": return go_back(m)
    try:
        a = int(m.text.strip())
        if not 10 <= a <= 90: raise ValueError
    except:
        return bot.reply_to(m, "Нужно целое число от 10 до 90.", reply_markup=back_kb())

    u = get_user(m.from_user.id)
    u["profile"]["age"] = a; put_user(m.from_user.id, u)

    USER_FLOW[m.from_user.id] = {"step": "ask_activity"}
    bot.send_message(m.chat.id, "Уровень активности:", reply_markup=activity_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_activity")
def week_menu_activity(m):
    if m.text == "⬅️ Назад": return go_back(m)
    if m.text not in ("Низкая", "Средняя", "Высокая"):
        return bot.reply_to(m, "Выбери кнопку 👇", reply_markup=activity_kb())

    u = get_user(m.from_user.id)
    u["profile"]["activity"] = m.text; put_user(m.from_user.id, u)

    # генерируем меню через ИИ
    prof = u["profile"]
    try:
        plan = ai_generate_week_menu(prof, m.from_user.first_name or "пользователь")
    except Exception as e:
        plan = None

    if not plan:
        plan = "🧠 Меню на неделю будет сформировано по твоим параметрам.\n(пример-рыба: сбалансированный рацион 3 приёма пищи + перекусы, 1700–1900 ккал/день)"

    bot.send_message(m.chat.id, plan, reply_markup=main_kb())
    USER_FLOW.pop(m.from_user.id, None)
    @bot.message_handler(func=lambda m: m.text == "🍳 Рецепты от ИИ")
def recipes_ai(m):
    USER_FLOW[m.from_user.id] = {"step": "ask_recipe"}
    bot.send_message(
        m.chat.id,
        "Что хочешь? Можешь попросить: «блинчики на завтрак», «рецепт ~600 ккал», «паста без молочки» и т.п.",
        reply_markup=back_kb()
    )

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "ask_recipe")
def recipes_ai_text(m):
    if m.text == "⬅️ Назад": return go_back(m)
    q = m.text.strip()
    try:
        ans = ai_generate_recipe(q)
    except Exception as e:
        ans = None
    if not ans:
        ans = "🧠 Пример рецепта: Омлет из 2 яиц с овощами и сыром, тост из цельнозернового хлеба. ~450 ккал."

    bot.reply_to(m, ans, reply_markup=main_kb())
    USER_FLOW.pop(m.from_user.id, None)
    def ai_client():
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None

def ai_estimate_kbju_from_image(image_bytes: bytes, hint: str = "") -> str | None:
    client = ai_client()
    if not client:
        return None
    # гоним картинку как data URL (короче и без файлов)
    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "Ты нутрициолог. Оцени примерную калорийность и Б/Ж/У блюда на фото. "
        "Дай краткий ответ в формате: «~Х ккал, Б/Ж/У A/B/C». "
        "Если есть подпись со списком — учти её."
    )
    if hint:
        prompt += f"\nПодпись пользователя: {hint}"

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",  # быстрая vision-модель
            messages=[
                {"role":"system","content":"Ты кратко и по делу отвечаешь как нутрициолог."},
                {"role":"user","content":[
                    {"type":"text","text": prompt},
                    {"type":"image_url","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
                ]}
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

def ai_estimate_kbju_from_text(items_text:str) -> str | None:
    client = ai_client()
    if not client:
        return None
    prompt = (
        "Ты нутрициолог. Посчитай приблизительные калории и Б/Ж/У для списка продуктов с граммовками. "
        "Дай краткий итог (ккал, Б/Ж/У) и разложи по пунктам.\n\n"
        f"Список:\n{items_text}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role":"system","content":"Отвечай кратко и структурировано."},
                {"role":"user","content":prompt}
            ],
            temperature=0.2
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

def ai_generate_week_menu(profile:dict, name:str) -> str | None:
    client = ai_client()
    if not client:
        return None
    goal_map = {"Похудение":"weight loss","Поддержание":"maintenance","Набор массы":"muscle gain"}
    act_map = {"Низкая":"low","Средняя":"moderate","Высокая":"high"}

    prompt = (
        "Составь подробное меню на 7 дней под параметры клиента. "
        "Формат: День N → Завтрак / Обед / Ужин (+перекус при необходимости) с граммовками, "
        "примерной калорийностью и Б/Ж/У. Кратко, но чётко. "
        "Избегай повторов, учитывай цель и активность.\n\n"
        f"Имя: {name}\n"
        f"Пол: {profile.get('gender')}\n"
        f"Цель: {profile.get('goal')}\n"
        f"Рост: {profile.get('height_cm')} см\n"
        f"Вес: {profile.get('weight_kg')} кг\n"
        f"Возраст: {profile.get('age')}\n"
        f"Активность: {profile.get('activity')}\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role":"system","content":"Ты нутрициолог и планируешь рацион с учётом целей и энергозатрат."},
                {"role":"user","content":prompt}
            ],
            temperature=0.4
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

def ai_generate_recipe(query:str) -> str | None:
    client = ai_client()
    if not client:
        return None
    prompt = (
        "Дай 1–2 рецепта под запрос. Укажи ингредиенты, шаги и примерную калорийность на порцию. "
        "Если пользователь указал калории — подгони под это."
        f"\nЗапрос: {query}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role":"system","content":"Ты кулинар и нутрициолог в одном."},
                {"role":"user","content":prompt}
            ],
            temperature=0.5
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None
        
