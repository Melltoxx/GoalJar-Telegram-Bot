# -*- coding: utf-8 -*-
"""
Telegram-бот «Скарбничка цілей»
Бібліотека: aiogram 3.x
Збереження даних: локальний JSON-файл (bot_data.json)
"""

# ============================================================
# 1. ІМПОРТИ — підключаємо потрібні інструменти
# ============================================================
import asyncio                      # бібліотека для асинхронної роботи бота
import html                         # щоб безпечно виводити назву цілі в повідомленнях
import json                         # робота з JSON-файлом
import logging                      # виводить у консоль інформацію про роботу бота
import math                         # перевірка чисел (щоб відсіяти nan та inf)
import os                           # робота з файлами
from datetime import datetime       # поточна дата й час для історії

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ============================================================
# 2. НАЛАШТУВАННЯ
# ============================================================
# Токен НЕ пишемо в коді, щоб випадково не виставити його на GitHub.
# Він береться зі змінної середовища BOT_TOKEN або з файлу .env (див. README.md).
try:
    from dotenv import load_dotenv   # необов'язкова бібліотека python-dotenv
    load_dotenv()                    # читає файл .env, якщо він є поруч
except ImportError:
    pass                             # якщо бібліотеки немає — просто йдемо далі

TOKEN = os.getenv("BOT_TOKEN", "")

# Назва файлу, де зберігатимуться дані. Він з'явиться поруч із bot.py
DATA_FILE = "bot_data.json"

# Максимальна сума, яку можна ввести (захист від безглуздих значень)
MAX_AMOUNT = 1_000_000_000

# Скільки записів історії показувати
HISTORY_LIMIT = 10

# Router — це «збірка обробників подій» (хто що натиснув/написав)
router = Router()


# ============================================================
# 3. СТАНИ (FSM) — «пам'ять» бота про те, на якому кроці діалогу ми зараз
# ============================================================
class NewGoal(StatesGroup):
    name = State()      # чекаємо, поки користувач введе назву цілі
    target = State()    # чекаємо, поки користувач введе суму цілі


class Topup(StatesGroup):
    custom = State()    # чекаємо, поки користувач введе власну суму поповнення


# ============================================================
# 4. РОБОТА З JSON-ФАЙЛОМ (збереження та завантаження)
# ============================================================
def load_data() -> dict:
    """Читає всі дані з файлу. Якщо файлу немає або він пошкоджений — повертає порожній словник."""
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Якщо файл зіпсований, не падаємо, а починаємо з чистого аркуша
        return {}


def save_data(data: dict) -> None:
    """Записує дані у файл. Спочатку пишемо у тимчасовий файл, потім підміняємо —
    так файл не пошкодиться, навіть якщо програму вимкнуть посеред запису."""
    tmp_file = DATA_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, DATA_FILE)


def get_user(data: dict, user_id: int) -> dict:
    """Повертає дані конкретного користувача. Якщо його ще немає — створює порожній запис.
    Ключі в JSON завжди рядки, тому перетворюємо user_id на str."""
    key = str(user_id)
    if key not in data:
        data[key] = {"goal": None, "history": []}
    return data[key]


def add_history(user: dict, text: str, amount: float = 0.0) -> None:
    """Додає запис в історію користувача."""
    user["history"].append({
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "text": text,
        "amount": amount,
    })


# ============================================================
# 5. ДОПОМІЖНІ ФУНКЦІЇ (форматування, перевірка чисел, прогрес-бар)
# ============================================================
def parse_amount(text: str):
    """Перетворює текст на додатне число.
    Повертає float, якщо все добре, або None, якщо введено щось некоректне."""
    # Користувачі часто пишуть кому замість крапки: 12,5 -> 12.5
    cleaned = text.strip().replace(",", ".").replace(" ", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None  # це був текст, а не число
    # Відсіюємо nan/inf, від'ємні числа, нуль та занадто великі суми
    if not math.isfinite(value) or value <= 0 or value > MAX_AMOUNT:
        return None
    return round(value, 2)


def fmt_money(value: float) -> str:
    """Гарно форматує суму: 12500.5 -> '12 500.50 грн'."""
    return f"{value:,.2f}".replace(",", " ") + " грн"


def progress_bar(saved: float, target: float) -> str:
    """Малює прогрес-бар з емодзі, напр. 🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜ 30%"""
    percent = min(saved / target * 100, 100)   # відсоток, але не більше 100
    filled = int(percent // 10)                # скільки зелених квадратів (шкала з 10 клітинок)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return f"{bar} {int(percent)}%"


def progress_text(goal: dict) -> str:
    """Формує повне повідомлення про прогрес цілі."""
    name = html.escape(goal["name"])           # екрануємо, щоб спецсимволи не ламали розмітку
    saved = goal["saved"]
    target = goal["target"]
    left = max(target - saved, 0)              # скільки залишилось (не менше 0)

    text = (
        f"🎯 <b>{name}</b>\n\n"
        f"{progress_bar(saved, target)}\n\n"
        f"💰 Накопичено: <b>{fmt_money(saved)}</b>\n"
        f"🏁 Вартість цілі: <b>{fmt_money(target)}</b>\n"
        f"⏳ Залишилось зібрати: <b>{fmt_money(left)}</b>"
    )
    if saved >= target:
        text += "\n\n🎉 <b>Вітаю! Ціль досягнуто!</b>"
    return text


# ============================================================
# 6. КЛАВІАТУРИ (кнопки)
# ============================================================
def main_menu() -> ReplyKeyboardMarkup:
    """Головне меню — постійні кнопки внизу екрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 Нова ціль"), KeyboardButton(text="💰 Поповнити")],
            [KeyboardButton(text="📊 Мій прогрес"), KeyboardButton(text="📜 Історія")],
            [KeyboardButton(text="🗑 Скинути")],
        ],
        resize_keyboard=True,   # кнопки не будуть занадто великими
    )


def topup_keyboard() -> InlineKeyboardMarkup:
    """Кнопки під повідомленням для швидкого поповнення.
    У callback_data ми ховаємо «команду»: add:50 означає «додати 50»."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="+50 грн", callback_data="add:50"),
            InlineKeyboardButton(text="+100 грн", callback_data="add:100"),
            InlineKeyboardButton(text="+500 грн", callback_data="add:500"),
        ],
        [InlineKeyboardButton(text="✏️ Ввести свою суму", callback_data="add:custom")],
    ])


def reset_keyboard() -> InlineKeyboardMarkup:
    """Кнопки підтвердження скидання."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, скинути", callback_data="reset:yes"),
        InlineKeyboardButton(text="❌ Ні, залишити", callback_data="reset:no"),
    ]])


# ============================================================
# 7. ЛОГІКА ПОПОВНЕННЯ (одна функція для кнопок і для ручного вводу)
# ============================================================
def apply_topup(user_id: int, amount: float):
    """Додає суму до цілі, зберігає у файл. Повертає текст відповіді або None, якщо цілі немає."""
    data = load_data()
    user = get_user(data, user_id)
    goal = user["goal"]
    if goal is None:
        return None

    goal["saved"] = round(goal["saved"] + amount, 2)
    add_history(user, "Поповнення", amount)
    save_data(data)   # <-- ось тут прогрес записується у файл

    return f"✅ Додано <b>{fmt_money(amount)}</b>\n\n" + progress_text(goal)


# ============================================================
# 8. ОБРОБНИКИ ГОЛОВНОГО МЕНЮ
#    Вони зареєстровані першими й без вказання стану,
#    тому спрацьовують завжди — навіть посеред введення суми.
# ============================================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Спрацьовує, коли користувач вперше відкриває бота або натискає /start."""
    await state.clear()   # скидаємо будь-який незавершений діалог
    await message.answer(
        "👋 Привіт! Я — <b>Скарбничка цілей</b>.\n"
        "Допоможу накопичити на мрію крок за кроком.\n\n"
        "Користуйся кнопками внизу 👇",
        reply_markup=main_menu(),
    )


@router.message(F.text == "🎯 Нова ціль")
async def new_goal_start(message: Message, state: FSMContext):
    """Крок 1 створення цілі: питаємо назву."""
    await state.clear()
    data = load_data()
    goal = get_user(data, message.from_user.id)["goal"]

    warning = ""
    if goal is not None:
        warning = "⚠️ У тебе вже є ціль — вона буде замінена новою.\n\n"

    await state.set_state(NewGoal.name)   # переходимо до кроку «чекаємо назву»
    await message.answer(warning + "Як називається твоя ціль? Наприклад: <i>Ноутбук</i> 💻")


@router.message(F.text == "💰 Поповнити")
async def topup_start(message: Message, state: FSMContext):
    """Показує кнопки швидкого поповнення."""
    await state.clear()
    data = load_data()
    goal = get_user(data, message.from_user.id)["goal"]

    if goal is None:
        await message.answer("У тебе ще немає цілі. Натисни «🎯 Нова ціль» 🙂")
        return

    await message.answer("Скільки додаємо до скарбнички?", reply_markup=topup_keyboard())


@router.message(F.text == "📊 Мій прогрес")
async def show_progress(message: Message, state: FSMContext):
    """Показує прогрес-бар та суми."""
    await state.clear()
    data = load_data()
    goal = get_user(data, message.from_user.id)["goal"]

    if goal is None:
        await message.answer("У тебе ще немає цілі. Натисни «🎯 Нова ціль» 🙂")
        return

    await message.answer(progress_text(goal))


@router.message(F.text == "📜 Історія")
async def show_history(message: Message, state: FSMContext):
    """Показує останні операції."""
    await state.clear()
    data = load_data()
    history = get_user(data, message.from_user.id)["history"]

    if not history:
        await message.answer("Історія поки порожня.")
        return

    lines = ["📜 <b>Останні операції:</b>\n"]
    # [-HISTORY_LIMIT:] — беремо останні N записів, reversed — найновіші зверху
    for item in reversed(history[-HISTORY_LIMIT:]):
        if item["amount"] > 0:
            lines.append(f"• {item['date']} — {html.escape(item['text'])}: +{fmt_money(item['amount'])}")
        else:
            lines.append(f"• {item['date']} — {html.escape(item['text'])}")
    await message.answer("\n".join(lines))


@router.message(F.text == "🗑 Скинути")
async def reset_start(message: Message, state: FSMContext):
    """Просить підтвердження перед видаленням."""
    await state.clear()
    await message.answer(
        "Точно скинути ціль та всю історію? Це не можна скасувати.",
        reply_markup=reset_keyboard(),
    )


# ============================================================
# 9. ОБРОБНИКИ ІНЛАЙН-КНОПОК (callback)
# ============================================================
@router.callback_query(F.data.startswith("add:"))
async def topup_button(callback: CallbackQuery, state: FSMContext):
    """Натиснули одну з кнопок поповнення (+50, +100, +500 або «своя сума»)."""
    await callback.answer()   # прибирає «годинник» на кнопці
    value = callback.data.split(":")[1]   # "add:50" -> "50"

    if value == "custom":
        await state.set_state(Topup.custom)
        await callback.message.answer("Введи суму поповнення числом, наприклад: <i>250</i> або <i>99.50</i>")
        return

    result = apply_topup(callback.from_user.id, float(value))
    if result is None:
        await callback.message.answer("У тебе ще немає цілі. Натисни «🎯 Нова ціль» 🙂")
    else:
        await callback.message.answer(result)


@router.callback_query(F.data.startswith("reset:"))
async def reset_button(callback: CallbackQuery):
    """Відповідь на питання «Точно скинути?»."""
    await callback.answer()
    if callback.data == "reset:yes":
        data = load_data()
        data[str(callback.from_user.id)] = {"goal": None, "history": []}
        save_data(data)
        await callback.message.edit_text("🗑 Ціль та історію скинуто. Можна почати заново!")
    else:
        await callback.message.edit_text("Добре, нічого не змінюю 🙂")


# ============================================================
# 10. ОБРОБНИКИ КРОКІВ ДІАЛОГУ (стани)
# ============================================================
@router.message(NewGoal.name, F.text)
async def new_goal_name(message: Message, state: FSMContext):
    """Крок 2: отримали назву цілі."""
    name = message.text.strip()
    if len(name) > 50:
        await message.answer("Назва задовга 😅 Спробуй коротше (до 50 символів).")
        return

    await state.update_data(name=name)    # тимчасово запам'ятовуємо назву
    await state.set_state(NewGoal.target)
    await message.answer(f"Чудово! Скільки коштує «{html.escape(name)}»? Введи суму в гривнях:")


@router.message(NewGoal.target, F.text)
async def new_goal_target(message: Message, state: FSMContext):
    """Крок 3: отримали суму цілі й перевіряємо її."""
    amount = parse_amount(message.text)
    if amount is None:
        # Валідація: це не число, воно від'ємне, нуль або надто велике
        await message.answer("Будь ласка, введи коректну суму числом більше нуля, наприклад: <i>15000</i>")
        return   # залишаємось на цьому кроці, чекаємо нову спробу

    fsm_data = await state.get_data()     # дістаємо назву, яку запам'ятали раніше

    data = load_data()
    user = get_user(data, message.from_user.id)
    user["goal"] = {"name": fsm_data["name"], "target": amount, "saved": 0.0}
    user["history"] = []                  # нова ціль — нова історія
    add_history(user, f"Створено ціль «{fsm_data['name']}» на {fmt_money(amount)}")
    save_data(data)

    await state.clear()
    await message.answer("🎯 Ціль створено!\n\n" + progress_text(user["goal"]), reply_markup=main_menu())


@router.message(Topup.custom, F.text)
async def topup_custom(message: Message, state: FSMContext):
    """Користувач ввів свою суму поповнення."""
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Це не схоже на суму 🤔 Введи додатне число, наприклад: <i>250</i>")
        return

    await state.clear()
    result = apply_topup(message.from_user.id, amount)
    if result is None:
        await message.answer("У тебе ще немає цілі. Натисни «🎯 Нова ціль» 🙂")
    else:
        await message.answer(result)


@router.message(NewGoal.name)
@router.message(NewGoal.target)
@router.message(Topup.custom)
async def wrong_type_in_state(message: Message):
    """Якщо замість тексту надіслали стікер, фото тощо."""
    await message.answer("Мені потрібен текст або число 🙂 Спробуй ще раз.")


# ============================================================
# 11. ЗАПАСНИЙ ОБРОБНИК — будь-який інший текст
#     Має бути ОСТАННІМ у файлі.
# ============================================================
@router.message()
async def fallback(message: Message):
    await message.answer("Користуйся кнопками внизу 👇", reply_markup=main_menu())


# ============================================================
# 12. ЗАПУСК БОТА
# ============================================================
async def main():
    logging.basicConfig(level=logging.INFO)   # показувати в консолі, що відбувається

    if not TOKEN:
        print("❌ Токен не знайдено! Створіть файл .env з рядком BOT_TOKEN=ваш_токен (див. README.md)")
        return

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    print("✅ Бот запущено. Щоб зупинити — натисніть Ctrl+C.")
    await dp.start_polling(bot)   # безперервно слухаємо нові повідомлення


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бота зупинено.")
