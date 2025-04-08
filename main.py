import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InputFile
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardMarkup, KeyboardButton
import logging
import re
from dotenv import load_dotenv
import os
logging.basicConfig(level=logging.INFO)
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Память
users = {}  # user_id: {"role": "parent"/"child", "points": int, "history": [], "parent_id": int}
tasks = {}  # task_id: {"title": str, "points": int}
task_counter = 0
pending_tasks = {}  # parent_id: [{"child_id", "task_id", "photo_id"}]
children_by_parent = {}  # parent_id: set(child_ids)
# Временное хранилище для действия начисления/списания
adjusting = {}  # parent_id: {"child_id": int, "action": "add"/"remove"}

# Главное меню
def main_menu(role):
    if role == "parent":
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="➕ Добавить задание")],
            [KeyboardButton(text="🗑 Удалить задание")],
            [KeyboardButton(text="📊 Статистика ребёнка")],
            [KeyboardButton(text="💰 Начислить/Списать баллы")]
        ], resize_keyboard=True)
    elif role == "child":
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="📋 Выполнить задание")],
            [KeyboardButton(text="📈 Мои баллы")]
        ], resize_keyboard=True)

# Команда /start
@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    if user_id in users:
        return await message.answer("Вы уже выбрали роль.")
    await message.answer("Кто вы?", reply_markup=ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨 Родитель")],
            [KeyboardButton(text="🧒 Ребёнок")]
        ], resize_keyboard=True
    ))

@dp.message(Command("newroll"))
async def new_roll(message: Message):
    users.pop(message.from_user.id, None)
    await start(message)

# Установка ролей
@dp.message(F.text == "👨 Родитель")
async def set_parent(message: Message):
    users[message.from_user.id] = {"role": "parent"}
    children_by_parent[message.from_user.id] = set()
    await message.answer(
        f"Вы Родитель. Добро пожаловать!\n\nВаш Telegram ID: `{message.from_user.id}`\n"
        f"Передайте этот ID вашему ребёнку, чтобы он мог подключиться к вам.",
        reply_markup=main_menu("parent"),
        parse_mode="Markdown"
    )

@dp.message(F.text == "🧒 Ребёнок")
async def set_child(message: Message):
    if message.from_user.id in users:
        return
    users[message.from_user.id] = {"role": "child", "points": 0, "history": [], "parent_id": None}
    await message.answer("Введите ID Родителя (его Telegram ID):")

@dp.message(lambda m: users.get(m.from_user.id, {}).get("role") == "child" and users[m.from_user.id].get("parent_id") is None and m.text.isdigit())
async def set_child_parent(message: Message):
    parent_id = int(message.text)
    if parent_id in users and users[parent_id]["role"] == "parent":
        users[message.from_user.id]["parent_id"] = parent_id
        children_by_parent[parent_id].add(message.from_user.id)
        await message.answer("Успешно подключено к Родителю!", reply_markup=main_menu("child"))

        # Уведомление Родителю
        child_name = message.from_user.first_name
        await bot.send_message(parent_id, f"🧒 Ребёнок {child_name} успешно подключился к вам!")
    else:
        await message.answer("Родитель с таким ID не найден. Попробуйте снова.")

# Добавить задание
@dp.message(F.text == "➕ Добавить задание")
async def add_task_prompt(message: Message):
    await message.answer("Введите задание в формате:\n\nНазвание (Баллы)\nПример: Помыть посуду (10)")

@dp.message(lambda m: users.get(m.from_user.id, {}).get("role") == "parent" and "(" in m.text and ")" in m.text)
async def add_task(message: Message):
    global task_counter
    match = re.match(r"(.+)\((\d+)\)", message.text.strip())
    if match:
        title = match.group(1).strip()
        points = int(match.group(2))
        tasks[task_counter] = {"title": title, "points": points}
        await message.answer(f"Задание \"{title}\" (+{points}) добавлено")
        # Отправка админу информации о новом задании
        parent_name = message.from_user.username or message.from_user.first_name
        await bot.send_message(
            ADMIN_ID,
            f"📌 Родитель @{parent_name} создал задание:\n\"{title}\" (+{points} баллов)"
        )
        task_counter += 1

# Удалить задание
@dp.message(F.text == "🗑 Удалить задание")
async def delete_task_prompt(message: Message):
    builder = InlineKeyboardBuilder()
    for task_id, task in tasks.items():
        builder.button(text=task["title"], callback_data=f"del_{task_id}")
    await message.answer("Выберите задание для удаления:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def delete_task(call: CallbackQuery):
    task_id = int(call.data.split("_")[1])
    title = tasks[task_id]["title"]
    tasks.pop(task_id)
    await call.message.edit_text(f"Задание \"{title}\" удалено.")

# Выполнение задания (ребёнок)
@dp.message(F.text == "📋 Выполнить задание")
async def show_tasks_to_child(message: Message):
    if not tasks:
        return await message.answer("🕓 Сейчас нет актуальных заданий. Попросите Родителя добавить их.")

    builder = InlineKeyboardBuilder()
    for task_id, task in tasks.items():
        builder.button(
            text=f"{task['title']} (+{task['points']})",
            callback_data=f"take_{task_id}"
        )
    builder.adjust(1)  # 1 кнопка в ряд
    await message.answer("Выберите задание:", reply_markup=builder.as_markup())

# Принятие задания ребёнком
@dp.callback_query(F.data.startswith("take_"))
async def child_take_task(call: CallbackQuery):
    task_id = int(call.data.split("_")[1])
    task = tasks[task_id]
    pending_tasks[call.from_user.id] = {"task_id": task_id}
    await call.message.answer(f"📸 Пришлите фото, подтверждающее выполнение задания:\n\n{task['title']}")

# Обработка фото от ребёнка
# Выполнение задания (ребёнок) — изменим, чтобы задание удалялось
@dp.message(F.photo)
async def handle_task_photo(message: Message):
    user_id = message.from_user.id
    if user_id not in pending_tasks:
        return await message.answer("Вы не выбрали задание для подтверждения.")

    photo_id = message.photo[-1].file_id
    pending_tasks[user_id]["photo_file_id"] = photo_id

    parent_id = users[user_id]["parent_id"]
    if not parent_id:
        return await message.answer("Родитель пока не зарегистрировался.")

    task_id = pending_tasks[user_id]["task_id"]
    task = tasks[task_id]

    # Отправить фото админу
    await bot.send_photo(
        ADMIN_ID,
        photo=photo_id,
        caption=f"🛡 Фото от ребёнка @{message.from_user.username or message.from_user.first_name}\n"
                f"Задание: {task['title']} (+{task['points']})",
        parse_mode="Markdown"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"accept_{user_id}_{task_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{user_id}")
    await bot.send_photo(parent_id, photo=photo_id,
                         caption=f"🧒 Ребёнок отправил подтверждение по заданию:\n*{task['title']}* (+{task['points']})",
                         parse_mode="Markdown",
                         reply_markup=builder.as_markup())

    # Убираем задание из доступных
    # Не удаляем здесь! Только сохраняем в pending_tasks
    pending_tasks[user_id] = {
        "task_id": task_id,
        "photo_file_id": photo_id
    }

    await message.answer("📤 Фото отправлено Родителю на проверку.")


# Подтверждение задания Родителем
@dp.callback_query(F.data.startswith("accept_"))
async def accept_task(call: CallbackQuery):
    _, child_id, task_id = call.data.split("_")
    child_id = int(child_id)
    task_id = int(task_id)
    task = tasks[task_id]
    task = tasks.pop(task_id, None)  # Удаляем здесь
    if not task:
        return await call.message.answer("Задание уже было удалено.")

    users[child_id]["points"] += task["points"]
    users[child_id]["history"].append(f"{task['title']} (+{task['points']})")
    await bot.send_message(child_id, f"✅ Родитель подтвердил задание: {task['title']}\n+{task['points']} баллов!")
    await call.message.edit_caption(f"✅ Задание подтверждено. У ребёнка теперь {users[child_id]['points']} баллов.")
    pending_tasks.pop(child_id, None)

@dp.callback_query(F.data.startswith("reject_"))
async def reject_task(call: CallbackQuery):
    child_id = int(call.data.split("_")[1])
    await bot.send_message(child_id, "❌ Родитель отклонил выполнение задания.")
    await call.message.edit_caption("❌ Задание отклонено.")
    pending_tasks.pop(child_id, None)

# Статистика ребёнка
@dp.message(F.text == "📊 Статистика ребёнка")
async def stats_prompt(message: Message):
    builder = InlineKeyboardBuilder()
    for child_id in children_by_parent.get(message.from_user.id, []):
        name = (await bot.get_chat(child_id)).first_name
        builder.button(text=f"{name} ({child_id})", callback_data=f"stat_{child_id}")
    builder.adjust(1)
    await message.answer("Выберите ребёнка:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("stat_"))
async def show_stat(call: CallbackQuery):
    child_id = int(call.data.split("_")[1])
    user = users.get(child_id)
    history = "\n".join(user["history"][-10:]) or "Нет выполненных заданий."
    await call.message.edit_text(f"Баллы: {user['points']}\nИстория:\n{history}")

# Баллы вручную
@dp.message(F.text == "💰 Начислить/Списать баллы")
async def adjust_start(message: Message):
    builder = InlineKeyboardBuilder()
    for child_id in children_by_parent.get(message.from_user.id, []):
        name = (await bot.get_chat(child_id)).first_name
        builder.button(text=f"{name} ({child_id})", callback_data=f"adjchild_{child_id}")
    builder.adjust(1)
    await message.answer("Выберите ребёнка:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("adjchild_"))
async def choose_adjust_action(call: CallbackQuery):
    child_id = int(call.data.split("_")[1])
    adjusting[call.from_user.id] = {"child_id": child_id}

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Начислить", callback_data="adjact_add")
    builder.button(text="➖ Списать", callback_data="adjact_remove")
    builder.adjust(2)
    await call.message.edit_text("Что вы хотите сделать?", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("adjact_"))
async def ask_adjust_amount(call: CallbackQuery):
    action = call.data.split("_")[1]
    adjusting[call.from_user.id]["action"] = action
    await call.message.edit_text("Введите количество баллов (только число):")

@dp.message(lambda m: m.from_user.id in adjusting and m.text.isdigit())
async def apply_adjust(message: Message):
    info = adjusting.pop(message.from_user.id)
    child_id = info["child_id"]
    action = info["action"]
    amount = int(message.text)

    if child_id not in users:
        return await message.answer("Ребёнок не найден.")

    if action == "add":
        users[child_id]["points"] += amount
        await message.answer(f"✅ Начислено {amount} баллов.\nТеперь у ребёнка {users[child_id]['points']} баллов.")
        await bot.send_message(child_id,
                               f"🟢 Вам начислено {amount} баллов!\nТеперь у вас {users[child_id]['points']} баллов.")
    else:
        users[child_id]["points"] = max(0, users[child_id]["points"] - amount)
        await message.answer(f"❌ Списано {amount} баллов.\nТеперь у ребёнка {users[child_id]['points']} баллов.")
        await bot.send_message(child_id,
                               f"🔴 У вас списали {amount} баллов.\nТеперь у вас {users[child_id]['points']} баллов.")

# Статистика ребёнка
@dp.message(F.text == "📈 Мои баллы")
async def my_points(message: Message):
    user = users[message.from_user.id]
    history = "\n".join(user["history"][-10:]) or "Нет выполненных заданий."
    await message.answer(f"✨ У вас {user['points']}\nИстория:\n{history}")

# Запуск
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
