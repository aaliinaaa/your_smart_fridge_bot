import logging
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart, Command
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from IPython import get_ipython
import nest_asyncio
from datetime import datetime, timedelta

class Form(StatesGroup):
    waiting_for_name = State()
    waiting_for_product = State()
    deleting_product = State()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS products (
        user_id INTEGER,
        product TEXT,
        expiry TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("Привет! Как тебя зовут?")
    await state.set_state(Form.waiting_for_name)

@router.message(Form.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"Приятно познакомиться, {message.text}!")
    await state.clear()

@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await message.answer("Добавьте продукт и его срок годности в формате:\nМолоко\n18.07")
    await state.set_state(Form.waiting_for_product)

@router.message(Form.waiting_for_product)
async def process_product_entry(message: Message, state: FSMContext):
    lines = message.text.strip().split('\n')
    entries = []
    for i in range(0, len(lines) - 1, 2):
        product = lines[i].strip()
        date = lines[i + 1].strip()
        entries.append((product, date))

    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    for product, date in entries:
        cursor.execute("INSERT INTO products (user_id, product, expiry) VALUES (?, ?, ?)",
                       (message.from_user.id, product, date))
    conn.commit()
    conn.close()

    await message.answer(f"Добавлены: {', '.join([f'{p} ({d})' for p, d in entries])}")
    await state.clear()

@router.message(Command("list"))
async def cmd_list(message: Message):
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute("SELECT product, expiry FROM products WHERE user_id = ?", (message.from_user.id,))
    items = cursor.fetchall()
    conn.close()

    if not items:
        await message.answer("Список пуст")
        return

    today = datetime.today().date()
    warning_limit = today + timedelta(days=3)
    expired = []
    warning = []
    fresh = []

    for product, expiry_str in items:
        try:
            expiry_date = datetime.strptime(expiry_str + ".2025", "%d.%m.%Y").date()
        except ValueError:
            fresh.append((product, expiry_str, None))
            continue

        if expiry_date < today:
            expired.append((product, expiry_str, expiry_date))
        elif expiry_date <= warning_limit:
            warning.append((product, expiry_str, expiry_date))
        else:
            fresh.append((product, expiry_str, expiry_date))

    # сортировка по дате
    expired.sort(key=lambda x: x[2])
    warning.sort(key=lambda x: x[2])
    fresh.sort(key=lambda x: x[2] if x[2] else datetime.max.date())

    parts = []
    if expired:
        parts.append("🔴 Просроченные:\n" + '\n'.join([f"{p} — {d}" for p, d, _ in expired]))
    if warning:
        if parts: parts.append("")
        parts.append("🟡 Истекают в течение 3 дней:\n" + '\n'.join([f"{p} — {d}" for p, d, _ in warning]))
    if fresh:
        if parts: parts.append("")
        parts.append("🟢 Остальные:\n" + '\n'.join([f"{p} — {d}" for p, d, _ in fresh]))

    await message.answer('\n'.join(parts))

@router.message(Command("delete"))
async def cmd_delete(message: Message, state: FSMContext):
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute("SELECT rowid, product FROM products WHERE user_id = ?", (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("Список пуст")
        return

    buttons = [
        [InlineKeyboardButton(text=product, callback_data=f"del_{rowid}")]
        for rowid, product in rows
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите продукт для удаления:", reply_markup=keyboard)
    await state.set_state(Form.deleting_product)

@router.callback_query(lambda c: c.data.startswith("del_"))
async def handle_delete(callback_query: types.CallbackQuery, state: FSMContext):
    rowid = int(callback_query.data.split('_')[1])

    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute("SELECT product FROM products WHERE rowid = ?", (rowid,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM products WHERE rowid = ?", (rowid,))
        conn.commit()
        await callback_query.message.edit_text(f"Удалено: {row[0]}")
    else:
        await callback_query.message.edit_text("Ошибка при удалении")
    conn.close()

    await state.clear()

async def daily_notify(bot: Bot):
    while True:
        now = datetime.now()
        # Рассчитываем сколько секунд до следующего 00:40
        next_run = now.replace(hour=14, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # После пробуждения — делаем рассылку
        conn = sqlite3.connect("products.db")
        cursor = conn.cursor()

        today = datetime.today().date()
        warning_limit = today + timedelta(days=3)

        cursor.execute("SELECT DISTINCT user_id FROM products")
        users = cursor.fetchall()

        for (user_id,) in users:
            cursor.execute(
                "SELECT product, expiry FROM products WHERE user_id = ?",
                (user_id,)
            )
            items = cursor.fetchall()

            warning_products = []
            for product, expiry_str in items:
                try:
                    expiry_date = datetime.strptime(expiry_str + ".2025", "%d.%m.%Y").date()
                except ValueError:
                    continue

                if today <= expiry_date <= warning_limit:
                    warning_products.append(f"{product} — {expiry_str}")

            if warning_products:
                text = "🟡 Внимание! Срок годности следующих продуктов истекает в течение 3 дней:\n\n"
                text += "\n".join(warning_products)
                try:
                    await bot.send_message(user_id, text)
                except Exception as e:
                    print(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

        conn.close()

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp.include_router(router)
    asyncio.create_task(daily_notify(bot))
    await dp.start_polling(bot)

if get_ipython():
    nest_asyncio.apply()
    await main()
else:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())