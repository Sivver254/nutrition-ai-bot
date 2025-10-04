# ---------- bot.py (Часть 1) ----------
import os
import json
import time
import datetime
import threading

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)

# ========= КОНФИГ =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set in environment variables")

# Цена и срок премиума (по умолчанию; можно менять через админку)
STAR_PRICE_PREMIUM_DEFAULT = int(os.getenv("STAR_PRICE_PREMIUM", "100"))   # ⭐
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))

# --- список админов ---
def _parse_admins():
    ids = set()
    if os.getenv("ADMIN_ID"):
        try:
            ids.add(int(os.getenv("ADMIN_ID")))
        except:
            pass
    if os.getenv("ADMIN_IDS"):
        for x in os.getenv("ADMIN_IDS").split(","):
            x = x.strip()
            if x.isdigit():
                ids.add(int(x))
    if not ids:
        ids.add(123456789)  # <- замени или задай ADMIN_ID/ADMIN_IDS в Render
    return ids

ADMIN_IDS = _parse_admins()
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# Хранилище шагов пользователя (для “Назад” и next-step)
USER_FLOW = {}  # {user_id: {"step": "..."}}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "users.json"   # простая локальная БД
# Структура:
# {
#   "<uid>": {"joined":ts,"premium":bool,"premium_until":ts, "trial_until": ts|0},
#   "__payments__":[{"uid":..., "stars":int, "ts":ts, "payload":str}]
# }
# ---------- bot.py (Часть 2) ----------
# ====== База ======
def _load():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            f.write("{}")
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db, uid: int):
    s = str(uid)
    if s not in db:
        db[s] = {
            "joined": int(time.time()),
            "premium": False,
            "premium_until": 0,
            "trial_until": 0   # 24ч пробный доступ для базовых фич
        }
    return db[s]

# ====== Премиум ======
def set_premium(uid: int, days: int):
    db = _load()
    u = get_user(db, uid)
    now = int(time.time())
    base = u["premium_until"] if u["premium_until"] > now else now
    u["premium_until"] = base + days * 86400
    u["premium"] = True
    db[str(uid)] = u
    _save(db)

def has_premium(uid: int) -> bool:
    db = _load()
    u = db.get(str(uid))
    if not u:
        return False
    if u["premium"] and u["premium_until"] > int(time.time()):
        return True
    # авто-сброс просрочки
    if u["premium"] and u["premium_until"] <= int(time.time()):
        u["premium"] = False
        db[str(uid)] = u
        _save(db)
    return False

def ensure_trial(uid: int):
    """Выдать 24ч trial для бесплатных фич (если ещё не выдавался)."""
    db = _load()
    u = get_user(db, uid)
    if u.get("trial_until", 0) == 0:
        u["trial_until"] = int(time.time()) + 24 * 3600
        db[str(uid)] = u
        _save(db)

def trial_active(uid: int) -> bool:
    db = _load()
    u = db.get(str(uid), {})
    return int(u.get("trial_until", 0)) > int(time.time())

def log_payment(uid: int, stars: int, payload: str):
    db = _load()
    db.setdefault("__payments__", []).append({
        "uid": uid, "stars": int(stars), "ts": int(time.time()), "payload": payload
    })
    _save(db)

def get_current_price() -> int:
    try:
        return int(os.getenv("STAR_PRICE_PREMIUM", str(STAR_PRICE_PREMIUM_DEFAULT)))
    except:
        return STAR_PRICE_PREMIUM_DEFAULT

# ====== Утилиты ======
def reset_flow(uid: int):
    USER_FLOW.pop(uid, None)

def ask_and_wait(chat_id: int, text: str, next_func):
    """Отправляем вопрос и регистрируем обработчик именно на это сообщение."""
    sent = bot.send_message(chat_id, text, parse_mode=None)
    bot.register_next_step_handler(sent, next_func)

# ====== Клавиатуры ======
BTN_KBJU_PHOTO = "📸 КБЖУ по фото"
BTN_KBJU_LIST  = "🧾 КБЖУ по списку"
BTN_RECIPES    = "👨‍🍳 Рецепты от ИИ"
BTN_WEEK_MENU  = "📅 Меню на неделю"
BTN_BUY        = "⭐ Купить премиум"
BTN_CHECK      = "📊 Проверить премиум"
BTN_BACK       = "⬅️ Назад"
BTN_ADMIN      = "👨‍💻 Админка"

def main_menu(uid: int = None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(BTN_KBJU_PHOTO), KeyboardButton(BTN_KBJU_LIST))
    kb.row(KeyboardButton(BTN_RECIPES), KeyboardButton(BTN_WEEK_MENU))
    kb.row(KeyboardButton(BTN_BUY),     KeyboardButton(BTN_CHECK))
    if uid and is_admin(uid):
        kb.row(KeyboardButton(BTN_ADMIN))
    return kb

def back_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(BTN_BACK))
    return kb
    # ---------- bot.py (Часть 3) ----------
@bot.message_handler(commands=["start"])
def cmd_start(m):
    db = _load(); get_user(db, m.from_user.id); _save(db)
    bot.send_message(
        m.chat.id,
        "Привет! 🤖 Я помогу посчитать КБЖУ еды:\n"
        f"• «{BTN_KBJU_PHOTO}» — пришли фото блюда\n"
        f"• «{BTN_KBJU_LIST}» — напиши продукты и граммы\n\n"
        f"Также могу подобрать меню на 7 дней под твои параметры — «{BTN_WEEK_MENU}».\n"
        f"«{BTN_RECIPES}» — бесплатно.\n"
        "Премиум открывает доп. функции на 30 дней.",
        reply_markup=main_menu(m.from_user.id)
    )
    reset_flow(m.from_user.id)

@bot.message_handler(func=lambda m: m.text == BTN_BACK)
def go_back(m):
    reset_flow(m.from_user.id)
    bot.send_message(m.chat.id, "Окей, вернул в меню.", reply_markup=main_menu(m.from_user.id))

# ====== КБЖУ по списку (упрощённая логика + 24ч trial) ======
@bot.message_handler(func=lambda m: m.text == BTN_KBJU_LIST)
def kbju_list_ask(m):
    ensure_trial(m.from_user.id)
    if not trial_active(m.from_user.id) and not has_premium(m.from_user.id):
        bot.send_message(m.chat.id, "🔒 Пробный доступ закончился. Нужен премиум.", reply_markup=main_menu(m.from_user.id))
        return
    USER_FLOW[m.from_user.id] = {"step": "kbju_list"}
    bot.send_message(m.chat.id, "Пришли список в формате: «Продукт 120 г; ...».", reply_markup=back_menu())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "kbju_list")
def kbju_list_calc(m):
    txt = (m.text or "").strip()
    if txt == BTN_BACK:
        return go_back(m)

    # Очень простая “нейросеть” :) — парсим шаблон и считаем ~1 ккал/грамм
    total_g = 0
    items = [x.strip() for x in txt.split(";") if x.strip()]
    parsed = []
    for it in items:
        # ищем последнее число в строке — как граммы
        grams = 0
        num = ""
        for ch in it:
            if ch.isdigit():
                num += ch
            elif num:
                break
        if num:
            grams = int(num)
        name = it.replace(num, "").replace("г", "").strip(" .,-")
        if grams <= 0 or not name:
            continue
        parsed.append((name, grams))
        total_g += grams

    if not parsed:
        bot.reply_to(m, "Не понял список 🤔 Пришли так: «Кур. грудка 150 г; Рис 180 г; Салат 120 г».", reply_markup=back_menu())
        return

    # на коленке: калории ≈ 1 ккал/г, БЖУ — условно
    kcal = total_g * 1.0
    protein = round(total_g * 0.15, 1)
    fat = round(total_g * 0.08, 1)
    carbs = round(total_g * 0.12, 1)

    lines = [f"• {n} — {g} г" for n, g in parsed]
    bot.send_message(
        m.chat.id,
        "🧾 Ваш список:\n" + "\n".join(lines) +
        f"\n\nИтого: <b>{kcal:.0f} ккал</b>\nБ: <b>{protein} г</b>, Ж: <b>{fat} г</b>, У: <b>{carbs} г</b>",
        reply_markup=main_menu(m.from_user.id)
    )
    reset_flow(m.from_user.id)

# ====== КБЖУ по фото (заглушка) ======
@bot.message_handler(func=lambda m: m.text == BTN_KBJU_PHOTO)
def kbju_photo_hint(m):
    ensure_trial(m.from_user.id)
    if not trial_active(m.from_user.id) and not has_premium(m.from_user.id):
        bot.send_message(m.chat.id, "🔒 Пробный доступ закончился. Нужен премиум.", reply_markup=main_menu(m.from_user.id))
        return
    USER_FLOW[m.from_user.id] = {"step": "kbju_photo_wait"}
    bot.send_message(m.chat.id, "Пришли фото блюдa одним сообщением.", reply_markup=back_menu())

@bot.message_handler(content_types=["photo"])
def kbju_photo_stub(m):
    step = USER_FLOW.get(m.from_user.id, {}).get("step")
    if step != "kbju_photo_wait":
        return  # игнорим чужие фото
    # Без внешнего ИИ просто заглушка
    bot.send_message(m.chat.id, "📷 Принял фото. Оценка КБЖУ (stub): ~450 ккал.\n(Для точности включи премиум и ИИ-распознавание).",
                     reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

# ====== Рецепты от ИИ (без премиума; простая генерация по запросу) ======
@bot.message_handler(func=lambda m: m.text == BTN_RECIPES)
def recipes_ask(m):
    USER_FLOW[m.from_user.id] = {"step": "recipes"}
    bot.send_message(m.chat.id, "Что приготовить? Пример: «блинчики», «паста 600 ккал», «курица и рис».", reply_markup=back_menu())

@bot.message_handler(func=lambda m: USER_FLOW.get(m.from_user.id, {}).get("step") == "recipes")
def recipes_generate(m):
    if m.text == BTN_BACK:
        return go_back(m)
    q = (m.text or "").lower()
    # супер-простая генерация (без внешних API)
    if "блин" in q:
        txt = "🥞 Блинчики (≈ 600 ккал): яйцо, молоко, мука, щепотка сахара и соли. Сковорода, 2–3 мин/сторона."
    elif "паста" in q or "макарон" in q:
        txt = "🍝 Паста 600 ккал: сварить пасту 80–100 г сух., соус из томатов, чеснока, оливк. масла; натёртый сыр."
    else:
        txt = "👨‍🍳 Рецепт: запечь курицу с овощами 25–30 мин при 200°C, подать с рисом. Примерно 600–700 ккал."
    bot.send_message(m.chat.id, txt, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)

# ====== Меню на неделю (анкета → премиум) ======
@bot.message_handler(func=lambda m: m.text == BTN_WEEK_MENU)
def week_menu_ask(m):
    USER_FLOW[m.from_user.id] = {"step": "anketa_w", "ank": {}}
    ask_and_wait(m.chat.id, "Укажи рост в см:", week_menu_q_height)

def week_menu_q_height(m):
    if m.text == BTN_BACK:
        return go_back(m)
    try:
        h = int(m.text.strip())
        USER_FLOW[m.from_user.id]["ank"]["h"] = h
        ask_and_wait(m.chat.id, "Вес в кг:", week_menu_q_weight)
    except:
        ask_and_wait(m.chat.id, "Введите число. Рост в см:", week_menu_q_height)

def week_menu_q_weight(m):
    if m.text == BTN_BACK:
        return go_back(m)
    try:
        w = float(m.text.strip().replace(",", "."))
        USER_FLOW[m.from_user.id]["ank"]["w"] = w
        ask_and_wait(m.chat.id, "Цель: похудение / поддержание / набор", week_menu_q_goal)
    except:
        ask_and_wait(m.chat.id, "Введите число. Вес в кг:", week_menu_q_weight)

def week_menu_q_goal(m):
    if m.text == BTN_BACK:
        return go_back(m)
    goal = (m.text or "").strip().lower()
    USER_FLOW[m.from_user.id]["ank"]["goal"] = goal

    if not has_premium(m.from_user.id):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(f"Купить премиум за {get_current_price()} ⭐", callback_data="buy_premium_stars"))
        bot.send_message(
            m.chat.id,
            "🔒 Меню на неделю — премиум-функция. Купи премиум, и я сгенерирую рацион под твою анкету.",
            reply_markup=kb
        )
        reset_flow(m.from_user.id)
        return

    ank = USER_FLOW[m.from_user.id]["ank"]
    plan = f"📅 Меню (черновик): цель — {ank['goal']}, рост {ank['h']} см, вес {ank['w']} кг.\n" \
           f"День 1: завтрак овсянка, обед курица+рис, ужин салат.\nДалее аналогично с вариациями."
    bot.send_message(m.chat.id, plan, reply_markup=main_menu(m.from_user.id))
    reset_flow(m.from_user.id)
    # ---------- bot.py (Часть 4) ----------
@bot.message_handler(func=lambda m: m.text == BTN_CHECK)
def check_premium(m):
    if has_premium(m.from_user.id):
        db = _load(); u = db.get(str(m.from_user.id), {})
        exp = datetime.datetime.fromtimestamp(u.get("premium_until", 0)).strftime("%d.%m.%Y")
        bot.reply_to(m, f"✅ Премиум активен до <b>{exp}</b>.", reply_markup=main_menu(m.from_user.id))
    else:
        bot.reply_to(m, "❌ Премиум не активен.", reply_markup=main_menu(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == BTN_BUY)
def buy_premium(m):
    price = get_current_price()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Оплатить {price} ⭐", callback_data="buy_premium_stars"))
    bot.send_message(m.chat.id, f"Премиум на {PREMIUM_DAYS} дней открывает все функции.\nЦена: {price} ⭐", reply_markup=kb)

# ====== Stars invoice ======
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_stars")
def cb_buy_premium_stars(c):
    # убираем спиннер обязательно
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass

    price_now = get_current_price()
    prices = [LabeledPrice(label="Премиум на 30 дней", amount=price_now)]
    bot.send_invoice(
        chat_id=c.message.chat.id,
        title="Премиум-доступ",
        description=f"Доступ ко всем функциям на {PREMIUM_DAYS} дней.",
        invoice_payload=f"premium_stars:{c.from_user.id}",
        provider_token="",      # для Stars токен не нужен
        currency="XTR",
        prices=prices,
        is_flexible=False
    )

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
@bot.message_handler(func=lambda m: m.text in (BTN_ADMIN, "/admin"))
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
    # КРИТИЧЕСКОЕ: всегда отвечаем на callback — иначе вечная загрузка
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass

    if not is_admin(c.from_user.id):
        bot.send_message(c.message.chat.id, "⛔ Нет доступа.")
        return

    db = _load()
    data = c.data

    if data == "adm_users":
        cnt = len([k for k in db.keys() if k != "__payments__"])
        bot.send_message(c.message.chat.id, f"👥 Всего пользователей: <b>{cnt}</b>")

    elif data == "adm_premiums":
        now = int(time.time())
        active = sum(
            1 for u in db.values()
            if isinstance(u, dict) and u.get("premium") and u.get("premium_until", 0) > now
        )
        bot.send_message(c.message.chat.id, f"💎 Активных премиумов: <b>{active}</b>")

    elif data == "adm_income":
        pays = db.get("__payments__", [])
        total = sum(p.get("stars", 0) for p in pays)
        bot.send_message(c.message.chat.id, f"💰 Локально зафиксировано: <b>{total} ⭐</b> ({len(pays)} оплат)")

    elif data == "adm_grant":
        ask_and_wait(c.message.chat.id, "Отправь: `<user_id> [дни]` (без скобок).", admin_grant_step)

    elif data == "adm_revoke":
        ask_and_wait(c.message.chat.id, "Отправь: `<user_id>` для снятия премиума.", admin_revoke_step)

    elif data == "adm_price":
        ask_and_wait(c.message.chat.id, f"Текущая цена: {get_current_price()} ⭐\nОтправь новое число:", admin_price_step)

# next-step обработчики (фикс: регистрируются через ask_and_wait)
def admin_grant_step(m):
    if not is_admin(m.from_user.id):
        return
    try:
        parts = m.text.strip().split()
        uid = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else PREMIUM_DAYS
        set_premium(uid, days)
        bot.reply_to(m, f"✅ Выдан премиум пользователю <code>{uid}</code> на {days} дн.",
                     reply_markup=main_menu(m.from_user.id))
        try:
            bot.send_message(uid, f"✅ Вам выдан премиум на {days} дней администратором.")
        except Exception:
            pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))

def admin_revoke_step(m):
    if not is_admin(m.from_user.id):
        return
    try:
        uid = int(m.text.strip())
        db = _load()
        u = db.get(str(uid))
        if not u:
            raise ValueError("Пользователь не найден")
        u["premium"] = False
        u["premium_until"] = 0
        db[str(uid)] = u
        _save(db)
        bot.reply_to(m, f"✅ Снят премиум у <code>{uid}</code>.", reply_markup=main_menu(m.from_user.id))
        try:
            bot.send_message(uid, "❌ Ваш премиум был снят администратором.")
        except Exception:
            pass
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))

def admin_price_step(m):
    if not is_admin(m.from_user.id):
        return
    try:
        new_price = int(m.text.strip())
        os.environ["STAR_PRICE_PREMIUM"] = str(new_price)  # меняем "на лету"
        bot.reply_to(m, f"✅ Новая цена установлена: {new_price} ⭐", reply_markup=main_menu(m.from_user.id))
    except Exception as e:
        bot.reply_to(m, f"⚠️ Ошибка: {e}", reply_markup=main_menu(m.from_user.id))
        # ---------- bot.py (Часть 5) ----------
# ====== Мини-веб (Render Web Service ping) ======
try:
    import flask
    app = flask.Flask(__name__)

    @app.route("/")
    def index():
        return "Bot is running!"

    def run_web():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    threading.Thread(target=run_web, daemon=True).start()
except Exception:
    # если Flask не установлен (например, в режиме worker) — просто пропускаем
    pass

# ====== Автоперезапуск раз в сутки (на всякий случай) ======
def auto_restart():
    while True:
        time.sleep(24 * 3600)
        os._exit(0)

threading.Thread(target=auto_restart, daemon=True).start()

# ====== Запуск бота ======
print("✅ Bot started")
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=90)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print("polling error:", e)
        time.sleep(3)
