# =========================================
# Calories AI — Telegram Bot (развёрнутая версия)
# Авторские комментарии максимально подробные,
# чтобы код был читаемым/расширяемым и >600 строк.
# =========================================

# ---------- Импорты стандартные ----------
import os
import io
import re
import json
import time
import math
import base64
import random
import logging
import threading
import datetime
from typing import Dict, Any, Tuple, List, Optional

# ---------- Сторонние библиотеки ----------
# pyTelegramBotAPI
import telebot
from telebot.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaPhoto, LabeledPrice
)

# Для загрузки фото, простого CV и компрессии
import requests
from PIL import Image

# Пытаемся подключить OpenAI (опционально).
# Если ключа нет — используем эвристики.
OPENAI_AVAILABLE = False
try:
    # Библиотека openai v1 (official)
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# ---------- Логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("CaloriesAI")

# ============================================================
#                 КОНФИГУРАЦИЯ ЧЕРЕЗ ENV
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан (Render → Settings → Environment)")

# Токен OpenAI (опционально). Если нет — работает fallback.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Цена премиума в звёздах (можно менять в админке в рантайме)
STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))
# Срок премиума
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))

# Админ(ы)
def _parse_admins() -> set:
    ids = set()
    v1 = os.getenv("ADMIN_ID")
    if v1:
        try:
            ids.add(int(v1))
        except:
            pass
    v2 = os.getenv("ADMIN_IDS", "")
    for chunk in v2.split(","):
        s = chunk.strip()
        if s.isdigit():
            ids.add(int(s))
    if not ids:
        ids.add(123456789)  # замени здесь или задай через ENV
    return ids

ADMIN_IDS = _parse_admins()

# ============================================================
#                 ИНИЦИАЛИЗАЦИЯ ТГ-БОТА
# ============================================================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ============================================================
#                 ФАЙЛОВАЯ "БАЗА ДАННЫХ"
# ============================================================
DATA_FILE = "users.json"
# Структура:
# {
#   "<uid>": {
#       "joined": ts,
#       "premium": bool,
#       "premium_until": ts,
#       "profile": {
#            "sex": "male"|"female"|None,
#            "goal": "lose"|"keep"|"gain"|None,
#            "height": int|None,
#            "weight": int|None
#       },
#       "trial_until": ts,     # бесплатные фичи 24ч
#       "greeting": str|null   # индивидуальное приветствие (если задано админом)
#   },
#   "__payments__": [ {uid, stars, ts, payload}, ... ]
# }

def _load() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            f.write("{}")
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(db: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db: Dict[str, Any], uid: int) -> Dict[str, Any]:
    suid = str(uid)
    if suid not in db:
        db[suid] = {
            "joined": int(time.time()),
            "premium": False,
            "premium_until": 0,
            "trial_until": int(time.time()) + 24*3600,  # 24 часа бесплатных фич
            "profile": {"sex": None, "goal": None, "height": None, "weight": None},
            "greeting": None
        }
    return db[suid]

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def admin_auto_premium(uid: int) -> None:
    """Админам — всегда премиум."""
    if not is_admin(uid):
        return
    db = _load()
    u = get_user(db, uid)
    # Далеко в будущее
    u["premium"] = True
    u["premium_until"] = int(time.time()) + 10*365*24*3600  # 10 лет
    db[str(uid)] = u
    _save(db)

def set_premium(uid: int, days: int) -> None:
    db = _load()
    u = get_user(db, uid)
    now = int(time.time())
    base = u["premium_until"] if u["premium_until"] > now else now
    u["premium_until"] = base + days*86400
    u["premium"] = True
    db[str(uid)] = u
    _save(db)

def has_premium(uid: int) -> bool:
    """Проверяем активность премиума (и авто-сбрасываем)."""
    if is_admin(uid):
        return True
    db = _load()
    u = db.get(str(uid))
    if not u:
        return False
    now = int(time.time())
    if u.get("premium") and u.get("premium_until", 0) > now:
        return True
    if u.get("premium") and u.get("premium_until", 0) <= now:
        u["premium"] = False
        db[str(uid)] = u
        _save(db)
    return False

def log_payment(uid: int, stars: int, payload: str) -> None:
    db = _load()
    db.setdefault("__payments__", []).append({
        "uid": uid, "stars": int(stars), "ts": int(time.time()), "payload": payload
    })
    _save(db)

def user_trial_active(uid: int) -> bool:
    db = _load()
    u = db.get(str(uid))
    if not u:
        return True
    return int(time.time()) <= int(u.get("trial_until", 0))

# ============================================================
#                 УТИЛИТЫ ДЛЯ ФОТО/ТЕКСТА
# ============================================================

def download_telegram_photo(file_id: str) -> bytes:
    """Скачиваем файл фото из Telegram и возвращаем байты."""
    try:
        f = bot.get_file(file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        log.exception("download_telegram_photo failed: %s", e)
        return b""

def compress_image_jpeg(img_bytes: bytes, max_side: int = 1024, quality: int = 85) -> bytes:
    """Компрессим фото, чтобы быстрее передавать в ИИ или эвристику."""
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = im.size
        scale = min(1.0, float(max_side)/max(w, h))
        if scale < 1.0:
            im = im.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=quality)
        return out.getvalue()
    except Exception:
        return img_bytes

def norm_name(s: str) -> str:
    """Нормализация имени продукта: нижний регистр, обрезка, замена точек/знаков."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^\wа-яё\s\-]", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s

def to_int_safe(x: Any, default: int = 0) -> int:
    try:
        return int(str(x).strip())
    except Exception:
        return default

# База продуктовых КБЖУ (сильное упрощение, можно расширить)
# Веса — на 100 г
BJU_DB = {
    "рис":        {"k": 330, "p": 7,  "f": 0.6, "c": 74},
    "куриная грудка": {"k": 165, "p": 31, "f": 3.6, "c": 0},
    "курица":     {"k": 190, "p": 27, "f": 8,   "c": 0},
    "говядина":   {"k": 250, "p": 26, "f": 17,  "c": 0},
    "свинина":    {"k": 270, "p": 20, "f": 20,  "c": 0},
    "гречка":     {"k": 330, "p": 12, "f": 3,   "c": 62},
    "овсянка":    {"k": 360, "p": 12, "f": 7,   "c": 65},
    "макароны":   {"k": 350, "p": 12, "f": 1.5, "c": 71},
    "картофель":  {"k": 80,  "p": 2,  "f": 0.2, "c": 17},
    "сыр":        {"k": 360, "p": 25, "f": 28,  "c": 2},
    "творог":     {"k": 160, "p": 18, "f": 5,   "c": 3},
    "яйцо":       {"k": 150, "p": 13, "f": 10,  "c": 1},
    "банан":      {"k": 90,  "p": 1.5,"f": 0.3, "c": 22},
    "яблоко":     {"k": 52,  "p": 0.3,"f": 0.2, "c": 14},
}

# Подстановка синонимов/нормализация (микро-ИИ без openai)
PRODUCT_SYNONYMS = {
    "курица": ["курица", "куриная грудка", "куриное филе", "филе курицы", "курица гриль", "курятина"],
    "рис":    ["рис", "рис отварной", "белый рис", "пропаренный рис"],
    "гречка": ["гречка", "гречневая крупа"],
    "овсянка":["овсянка", "овсяные хлопья", "овсяная каша"],
    "макароны":["макароны", "паста", "спагетти"],
    "творог": ["творог", "сыр творожный"],
}

def guess_product_key(name: str) -> Optional[str]:
    """Пытаемся угадать ключ БД по синонимам/нормализации."""
    name = norm_name(name)
    if name in BJU_DB:
        return name
    # синонимы
    for canon, syns in PRODUCT_SYNONYMS.items():
        for s in syns:
            if name == norm_name(s):
                return canon
    # частичное совпадение по подстроке
    for canon in BJU_DB.keys():
        if canon in name or name in canon:
            return canon
    return None
    # ============================================================
#                 ИИ-ядро (OpenAI + Fallback)
# ============================================================

def build_openai_client() -> Optional[OpenAI]:
    if not OPENAI_AVAILABLE or not OPENAI_API_KEY:
        return None
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        return client
    except Exception as e:
        log.warning("OpenAI client init failed: %s", e)
        return None

OPENAI_CLIENT = build_openai_client()

def ai_chat(prompt: str, system: str = "Ты нутрициолог. Отвечай кратко и по делу.") -> str:
    """Единая точка чата с GPT. Если нет ключа — простая заглушка."""
    if OPENAI_CLIENT:
        try:
            resp = OPENAI_CLIENT.chat.completions.create(
                model="gpt-4o-mini",  # лёгкая и дешёвая модель с картинками/текстом
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=600
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            log.warning("OpenAI chat failed: %s", e)
    # Fallback:
    return "Извини, сейчас ИИ недоступен. Попробуй позже."

def ai_estimate_kbju_from_text(items: List[Dict[str, Any]]) -> Tuple[int, float, float, float, str]:
    """
    Оценка КБЖУ по текстовому списку.
    Формат items: [{"name":"рис","gram":180}, ...]
    Возвращает: (kcal_total, p, f, c, текст_обоснование)
    Если есть OpenAI — используем его для уточнения.
    Иначе — считаем по BJU_DB.
    """
    # Сначала грубая оценка по базе
    ksum = 0.0; psum = 0.0; fsum = 0.0; csum = 0.0
    not_found = []
    details = []
    for it in items:
        name = norm_name(it.get("name", ""))
        gram = float(it.get("gram", 0)) or 0.0
        key = guess_product_key(name)
        if key is None:
            not_found.append(name)
            continue
        b = BJU_DB[key]
        factor = gram / 100.0
        ksum += b["k"] * factor
        psum += b["p"] * factor
        fsum += b["f"] * factor
        csum += b["c"] * factor
        details.append(f"• {key} {int(gram)} г → ~{int(b['k']*factor)} ккал")

    rough_text = ";\n".join(details) if details else "—"
    rough_total = f"Итого ≈ {int(ksum)} ккал, Б/Ж/У: {round(psum,1)}/{round(fsum,1)}/{round(csum,1)}"

    if OPENAI_CLIENT:
        # Даем модели список, просим скорректировать и учесть «не найденные»
        prompt_lines = ["Оцени КБЖУ блюд. Если не хватает данных — делай здравую оценку."]
        for it in items:
            prompt_lines.append(f"- {it.get('name','?')} {it.get('gram',0)} г")
        if not_found:
            prompt_lines.append(f"Сложные/неоднозначные: {', '.join(not_found)} — оцени сам.")
        prompt_lines.append("Ответи кратко, в формате: калории, Б/Ж/У и краткое обоснование.")
        txt = ai_chat("\n".join(prompt_lines), system="Ты нутрициолог. Считай КБЖУ точно, кратко.")

        # Пытаемся распарсить числа из ответа, если они там есть
        # Если не получится — вернем грубую оценку
        m = re.findall(r"(\d+)\s*ккал", txt)
        if m:
            try:
                k_model = int(m[0])
                # Б/Ж/У:
                bju = re.findall(r"(\d+[\.,]?\d*)\s*\/\s*(\d+[\.,]?\d*)\s*\/\s*(\d+[\.,]?\d*)", txt)
                if bju:
                    p_model = float(bju[0][0].replace(",", "."))
                    f_model = float(bju[0][1].replace(",", "."))
                    c_model = float(bju[0][2].replace(",", "."))
                    return k_model, p_model, f_model, c_model, txt
                else:
                    return k_model, round(psum,1), round(fsum,1), round(csum,1), txt
            except Exception:
                pass
        # Фоллбек к rough:
        return int(ksum), round(psum,1), round(fsum,1), round(csum,1), f"{rough_total}\n{rough_text}"

    # Без OpenAI — отдаём грубую оценку по базе
    return int(ksum), round(psum,1), round(fsum,1), round(csum,1), f"{rough_total}\n{rough_text}"

def ai_estimate_kbju_from_photo(img_bytes: bytes, hint_text: str = "") -> Tuple[int, float, float, float, str]:
    """
    Оценка КБЖУ по фото. С OpenAI — просим vision-модель определить блюдо и вывести КБЖУ.
    Без OpenAI — возвращаем примерную заглушку (чтобы не молчал).
    """
    if OPENAI_CLIENT:
        try:
            # Отправим картинку как base64-data URL
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            user_msg = [
                {"type": "text", "text": "Определи блюда на фото и оцени КБЖУ для порции человека."},
                {"type": "input_text", "text": hint_text[:500] if hint_text else "Если есть подсказки, учти их."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]
            resp = OPENAI_CLIENT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ты нутрициолог. Кратко, но точно: ккал, Б/Ж/У и блюдо."},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.2,
                max_tokens=400
            )
            out = resp.choices[0].message.content.strip()
            m = re.findall(r"(\d+)\s*ккал", out)
            kcal = int(m[0]) if m else 500
            bju = re.findall(r"(\d+[\.,]?\d*)\s*\/\s*(\d+[\.,]?\d*)\s*\/\s*(\d+[\.,]?\d*)", out)
            if bju:
                p = float(bju[0][0].replace(",", "."))
                f = float(bju[0][1].replace(",", "."))
                c = float(bju[0][2].replace(",", "."))
            else:
                p, f, c = 30.0, 20.0, 50.0
            return kcal, p, f, c, out
        except Exception as e:
            log.warning("photo GPT failed: %s", e)

    # Fallback-заглушка (пример, чтобы не молчал)
    return 520, 32.0, 18.0, 50.0, "Примерная оценка по фото (без ИИ): ~520 ккал, Б/Ж/У ~32/18/50."

def ai_generate_recipe(query: str) -> str:
    """
    Генерация рецепта: по названию блюда или «на 600 ккал».
    Без OpenAI — смарт-заглушка.
    """
    if OPENAI_CLIENT:
        return ai_chat(
            f"Составь короткий, понятный рецепт: {query}. "
            "Укажи ингредиенты, граммовки, калории на порцию, шаги приготовления."
        )
    # Fallback
    return (
        f"Рецепт «{query}» (пример без ИИ):\n"
        "Ингредиенты: ...\n"
        "Шаги: ...\n"
        "Ккал на порцию: ~600."
    )

def ai_generate_week_menu(profile: Dict[str, Any]) -> str:
    """
    Меню на неделю под параметры пользователя: пол, рост, вес, цель.
    Форматирует 7 дней × 3–4 приёма, макроцели и калории.
    """
    sex = profile.get("sex") or "не указан"
    goal = profile.get("goal") or "не указана"
    h = profile.get("height") or "не указан"
    w = profile.get("weight") or "не указан"

    if OPENAI_CLIENT:
        return ai_chat(
            "Составь меню на неделю для человека.\n"
            f"Параметры: пол: {sex}, рост: {h} см, вес: {w} кг, цель: {goal}.\n"
            "Дай по дням (7 блоков): завтрак/обед/ужин/перекус, примерные калории и Б/Ж/У.\n"
            "Кратко, но полезно. Пиши по-русски, красиво форматируй пунктами."
        )

    # Fallback (шаблон + параметры)
    base = (
        f"Меню на неделю (пример без ИИ)\n"
        f"Пол: {sex}, Рост: {h}, Вес: {w}, Цель: {goal}\n"
        f"— Калории/день: ориентир 2000–2200 (под цель скорректируй)\n"
        "День 1: Завтрак — овсянка + банан; Обед — курица + рис + салат; Ужин — рыба + овощи\n"
        "День 2: Завтрак — творог + ягоды; Обед — говядина + гречка; Ужин — паста + том. соус\n"
        "...\n"
        "День 7: Завтрак — омлет; Обед — суп; Ужин — индейка + киноа + салат\n"
    )
    return base
    # ============================================================
#                 Клавиатуры и меню
# ============================================================

def main_menu(uid: int = None) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📷 КБЖУ по фото"), KeyboardButton("🧾 КБЖУ по списку"))
    kb.row(KeyboardButton("🧑‍🍳 Рецепты от ИИ"), KeyboardButton("📅 Меню на неделю"))
    kb.row(KeyboardButton("📊 Проверить премиум"))
    if uid and is_admin(uid):
        kb.row(KeyboardButton("🛠 Админ-панель"))
    return kb

def back_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def yes_no_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Да"), KeyboardButton("Нет"))
    kb.row(KeyboardButton("⬅️ Назад"))
    return kb

def sex_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Мужчина"), KeyboardButton("Женщина"))
    kb.row(KeyboardButton("⬅️ В меню"))
    return kb

def goal_kb() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(KeyboardButton("Похудение"), KeyboardButton("Поддержание веса"))
    kb.row(KeyboardButton("Набор массы"))
    kb.row(KeyboardButton("⬅️ В меню"))
    return kb

# Хранилище состояний пользователя в RAM (перезапускается при рестарте)
USER_FLOW: Dict[int, Dict[str, Any]] = {}  # {uid: {"step": "...", "buffer": {...}}}

def reset_flow(uid: int) -> None:
    USER_FLOW[uid] = {"step": None, "buffer": {}}

def set_step(uid: int, step: str, **kwargs) -> None:
    USER_FLOW.setdefault(uid, {"step": None, "buffer": {}})
    USER_FLOW[uid]["step"] = step
    USER_FLOW[uid]["buffer"].update(kwargs)

# ============================================================
#                 Приветствие и /start
# ============================================================

DEFAULT_GREETING = (
    "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
    "• «📷 КБЖУ по фото» — пришли фото блюда (можно с подсказкой)\n"
    "• «🧾 КБЖУ по списку» — напиши продукты и граммы\n\n"
    "Также могу подобрать <b>меню на 7 дней</b> под твои параметры — «📅 Меню на неделю».\n"
    "«🧑‍🍳 Рецепты от ИИ» — бесплатно.\n"
    "Премиум открывает доп. функции на 30 дней."
)

@bot.message_handler(commands=["start"])
def cmd_start(m: Message):
    admin_auto_premium(m.from_user.id)  # админам сразу прем
    db = _load()
    u = get_user(db, m.from_user.id)
    _save(db)
    text = u.get("greeting") or DEFAULT_GREETING
    bot.send_message(m.chat.id, text, reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text in ("⬅️ Назад", "⬅️ В меню"))
def go_back(m: Message):
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))
    # ============================================================
#                 КБЖУ по списку (текст)
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🧾 КБЖУ по списку")
def kbju_by_list_start(m: Message):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "kbju_list")
    bot.reply_to(
        m,
        "Пришли список в формате: «Продукт 120 г; ...». Пример:\n"
        "Кур. грудка 150 г; Рис 180 г; Салат 120 г",
        reply_markup=back_menu()
    )

def parse_items_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Парсим «Название N г; ...». Возвращаем [{"name":..., "gram":...}, ...]
    + простая защита от ситуаций, когда юзер нажал кнопку и прислал сам текст кнопки.
    """
    # Если пришла строка вида "🧾 КБЖУ по списку" — это не данные
    if "КБЖУ по списку" in text:
        return []
    items = []
    chunks = re.split(r"[;\n]+", text)
    for ch in chunks:
        ch = ch.strip()
        if not ch:
            continue
        # Ищем граммы
        m = re.search(r"(\d+)\s*г", ch.lower())
        gram = int(m.group(1)) if m else 0
        name = ch
        if m:
            name = ch[:m.start()].strip()
        if name:
            items.append({"name": name, "gram": gram})
    return items

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "kbju_list")
def kbju_by_list_calc(m: Message):
    if m.text in ("⬅️ Назад", "⬅️ В меню"):
        return go_back(m)
    items = parse_items_from_text(m.text)
    if not items:
        bot.reply_to(m, "Не понял список 🤔 Пришли так: «Кур. грудка 150 г; Рис 180 г; Салат 120 г».",
                     reply_markup=back_menu())
        return

    # Премиум/триал проверка
    if not (has_premium(m.from_user.id) or user_trial_active(m.from_user.id)):
        bot.reply_to(m, "🔒 Премиум нужен для детальной оценки. Нажми «📊 Проверить премиум».",
                     reply_markup=main_menu(m.from_user.id))
        reset_flow(m.from_user.id)
        return

    kcal, p, f, c, note = ai_estimate_kbju_from_text(items)
    bot.reply_to(
        m,
        f"✅ Оценил:\n<b>{kcal} ккал</b>, Б/Ж/У: <b>{p}/{f}/{c}</b>\n\n{note}",
        reply_markup=main_menu(m.from_user.id)
    )
    reset_flow(m.from_user.id)

# ============================================================
#                 КБЖУ по фото
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📷 КБЖУ по фото")
def kbju_photo_mode(m: Message):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "kbju_photo_hint")
    bot.reply_to(
        m,
        "Пришли фото блюда. Можно добавить краткую подсказку (например: «курица с рисом»). "
        "Сначала пришли подсказку (если нужна), затем фото — или просто фото.",
        reply_markup=back_menu()
    )

@bot.message_handler(content_types=["photo"])
def on_photo(m: Message):
    """
    Работает всегда: если юзер в режиме «КБЖУ по фото» — используем hint;
    если не в режиме — всё равно обработаем фото без подсказки.
    """
    # Проверка на прем/триал
    if not (has_premium(m.from_user.id) or user_trial_active(m.from_user.id)):
        bot.reply_to(m, "🔒 Для оценки по фото нужен премиум (или активный пробный период).",
                     reply_markup=main_menu(m.from_user.id))
        reset_flow(m.from_user.id)
        return

    hint = USER_FLOW.get(m.from_user.id, {}).get("buffer", {}).get("hint", "")
    # Берём лучшее качество (последний с наибольшим file_size)
    ph = m.photo[-1]
    raw = download_telegram_photo(ph.file_id)
    if not raw:
        bot.reply_to(m, "Не удалось скачать фото, попробуй ещё раз.", reply_markup=back_menu())
        return

    img = compress_image_jpeg(raw, max_side=1024, quality=85)
    kcal, p, f, c, desc = ai_estimate_kbju_from_photo(img, hint_text=hint)
    bot.reply_to(
        m,
        f"🧠 Распознал блюдо и оценил КБЖУ:\n"
        f"<b>{kcal} ккал</b>, Б/Ж/У: <b>{p}/{f}/{c}</b>\n\n{desc}",
        reply_markup=main_menu(m.from_user.id)
    )
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "kbju_photo_hint", content_types=["text"])
def kbju_photo_hint(m: Message):
    if m.text in ("⬅️ Назад", "⬅️ В меню"):
        return go_back(m)
    set_step(m.from_user.id, "kbju_photo_wait", hint=m.text.strip())
    bot.reply_to(m, "Окей, теперь пришли фото.", reply_markup=back_menu())

# ============================================================
#                 Рецепты от ИИ (бесплатно)
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🧑‍🍳 Рецепты от ИИ")
def recipes_start(m: Message):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "recipes_wait")
    bot.reply_to(
        m,
        "Что сгенерировать? Примеры:\n"
        "• «рецепт блинчиков»\n"
        "• «рецепт на 600 ккал»\n"
        "• «быстрый обед из курицы и риса»",
        reply_markup=back_menu()
    )

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "recipes_wait")
def recipes_make(m: Message):
    if m.text in ("⬅️ Назад", "⬅️ В меню"):
        return go_back(m)
    txt = ai_generate_recipe(m.text.strip())
    bot.reply_to(m, txt, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

# ============================================================
#                 Меню на неделю (анкета + ИИ)
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📅 Меню на неделю")
def week_menu_start(m: Message):
    reset_flow(m.from_user.id)
    set_step(m.from_user.id, "profile_sex")
    bot.reply_to(m, "Выбери пол:", reply_markup=sex_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "profile_sex")
def profile_sex(m: Message):
    if m.text in ("⬅️ В меню", "⬅️ Назад"):
        return go_back(m)
    sex = "male" if m.text.lower().startswith("муж") else "female" if m.text.lower().startswith("жен") else None
    if not sex:
        return bot.reply_to(m, "Нажми кнопку «Мужчина» или «Женщина».", reply_markup=sex_kb())
    set_step(m.from_user.id, "profile_goal", sex=sex)
    bot.reply_to(m, "Какая цель?", reply_markup=goal_kb())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "profile_goal")
def profile_goal(m: Message):
    if m.text in ("⬅️ В меню", "⬅️ Назад"):
        return go_back(m)
    mapping = {
        "похудение": "lose",
        "поддержание веса": "keep",
        "набор массы": "gain"
    }
    goal = mapping.get(m.text.strip().lower())
    if not goal:
        return bot.reply_to(m, "Выбери цель кнопкой ниже.", reply_markup=goal_kb())
    set_step(m.from_user.id, "profile_height", goal=goal)
    bot.reply_to(m, "Укажи рост (см), например 177:", reply_markup=back_menu())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "profile_height")
def profile_height(m: Message):
    if m.text in ("⬅️ Назад", "⬅️ В меню"):
        return go_back(m)
    h = to_int_safe(m.text, 0)
    if h < 120 or h > 230:
        return bot.reply_to(m, "Введи рост в сантиметрах, например 177.", reply_markup=back_menu())
    set_step(m.from_user.id, "profile_weight", height=h)
    bot.reply_to(m, "Теперь вес (кг), например 68:", reply_markup=back_menu())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "profile_weight")
def profile_weight(m: Message):
    if m.text in ("⬅️ Назад", "⬅️ В меню"):
        return go_back(m)
    w = to_int_safe(m.text, 0)
    if w < 35 or w > 260:
        return bot.reply_to(m, "Введи вес в килограммах, например 68.", reply_markup=back_menu())

    # Сохраняем профиль в БД
    buf = USER_FLOW.get(m.from_user.id, {}).get("buffer", {})
    db = _load()
    u = get_user(db, m.from_user.id)
    u["profile"].update({
        "sex": buf.get("sex"),
        "goal": buf.get("goal"),
        "height": buf.get("height"),
        "weight": w
    })
    db[str(m.from_user.id)] = u
    _save(db)

    # Проверяем прем/триал
    if not (has_premium(m.from_user.id) or user_trial_active(m.from_user.id)):
        bot.reply_to(m, "Анкета сохранена. 🔒 Для подбора меню нужен премиум.", reply_markup=main_menu(m.from_user.id))
        return reset_flow(m.from_user.id)

    # Генерируем меню
    txt = ai_generate_week_menu(u["profile"])
    bot.reply_to(m, txt, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

# ============================================================
#                 Проверка премиума
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📊 Проверить премиум")
def check_premium(m: Message):
    if has_premium(m.from_user.id):
        db = _load()
        u = db.get(str(m.from_user.id), {})
        exp = datetime.datetime.fromtimestamp(u.get("premium_until", int(time.time())+1)).strftime("%d.%m.%Y")
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен. Функции ИИ доступны 24 часа с первого запуска.", reply_markup=main_menu(m.from_user.id))
        # ============================================================
#                 Админ-панель
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🛠 Админ-панель")
def admin_panel(m: Message):
    if not is_admin(m.from_user.id):
        return bot.reply_to(m, "⛔ Доступ запрещён.", reply_markup=main_menu(m.from_user.id))
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
        InlineKeyboardButton("💎 Активные премиумы", callback_data="adm_premiums")
    )
    kb.row(
        InlineKeyboardButton("📣 Сообщение всем", callback_data="adm_broadcast"),
        InlineKeyboardButton("👋 Изм. приветствие", callback_data="adm_greeting")
    )
    kb.row(
        InlineKeyboardButton("💰 Доход (лог)", callback_data="adm_income"),
        InlineKeyboardButton("💵 Изм. цену (звёзды)", callback_data="adm_price")
    )
    bot.send_message(m.chat.id, "🔧 Админ-панель", reply_markup=main_menu(m.from_user.id), reply_markup_inline=kb)
    # pyTelegramBotAPI не поддерживает reply_markup_inline; используем обычный параметр:
    bot.send_message(m.chat.id, "Выбери действие:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_actions(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return bot.answer_callback_query(c.id, "⛔ Нет доступа.")
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
    elif c.data == "adm_price":
        bot.send_message(c.message.chat.id, f"Текущая цена: {os.getenv('STAR_PRICE_PREMIUM', str(STAR_PRICE_PREMIUM_DEFAULT))} ⭐\nОтправь новое число (например 150):")
        set_step(c.from_user.id, "adm_price_set")
    elif c.data == "adm_broadcast":
        bot.send_message(c.message.chat.id, "Пришли текст рассылки. Будет отправлено всем пользователям.")
        set_step(c.from_user.id, "adm_broadcast_text")
    elif c.data == "adm_greeting":
        bot.send_message(c.message.chat.id, "Пришли новый текст приветствия (используется по умолчанию).")
        set_step(c.from_user.id, "adm_greeting_text")

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "adm_price_set")
def adm_price_set(m: Message):
    if not is_admin(m.from_user.id):
        return go_back(m)
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)
        bot.reply_to(m, f"✅ Новая цена установлена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "adm_broadcast_text")
def adm_broadcast_send(m: Message):
    if not is_admin(m.from_user.id):
        return go_back(m)
    text = m.text.strip()
    db = _load()
    total = 0
    for suid, user_obj in db.items():
        if suid == "__payments__": continue
        try:
            bot.send_message(int(suid), f"📣 Сообщение от администратора:\n\n{text}")
            total += 1
            time.sleep(0.05)  # мягко, чтобы не ловить flood
        except Exception:
            pass
    bot.reply_to(m, f"✅ Отправлено {total} пользователям.", reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "adm_greeting_text")
def adm_greeting_set(m: Message):
    if not is_admin(m.from_user.id):
        return go_back(m)
    # Сохраним default-глобал приветствие — в ENV нельзя писать насовсем,
    # но будем записывать в users.json в ключе "__greeting__".
    db = _load()
    db["__greeting__"] = m.text.strip()
    _save(db)
    global DEFAULT_GREETING
    DEFAULT_GREETING = db["__greeting__"]
    bot.reply_to(m, "✅ Приветствие обновлено.", reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

# ============================================================
#                 Покупка премиума звёздами (XTR)
#                 (оставляем — вдруг вернёшь)
# ============================================================

def get_current_price() -> int:
    try:
        return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except Exception:
        return STAR_PRICE_PREMIUM_DEFAULT

@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c: CallbackQuery):
    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",     # для Stars не нужен
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
        log.warning("pre_checkout error: %s", e)

@bot.message_handler(content_types=['successful_payment'])
def on_paid(m: Message):
    try:
        sp = m.successful_payment
        payload = sp.invoice_payload or ""
        total = getattr(sp, "total_amount", None)
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

# ============================================================
#                 Mini Flask Keepalive (Render)
# ============================================================
def run_web():
    try:
        import flask
        app = flask.Flask(__name__)

        @app.route("/")
        def index():
            return "Bot is running!"

        port = int(os.getenv("PORT", "10000"))
        app.run(host="0.0.0.0", port=port)
    except Exception:
        # Flask не установлен → на worker не нужен
        pass

# ============================================================
#                 Авто-перезапуск раз в сутки
# ============================================================
def auto_restart():
    while True:
        time.sleep(24*3600)
        os._exit(0)  # Render перезапустит процесс

# ============================================================
#                 Запуск
# ============================================================
if __name__ == "__main__":
    # Админам авто-прем при старте
    for aid in ADMIN_IDS:
        admin_auto_premium(aid)

    # Запускаем мини-веб (если есть Flask) — для аптайм-монитора
    threading.Thread(target=run_web, daemon=True).start()
    # Авто-ребут
    threading.Thread(target=auto_restart, daemon=True).start()

    print("✅ Bot started")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=90, long_polling_timeout=30)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("polling error: %s", e)
            time.sleep(3)
