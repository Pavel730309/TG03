import asyncio
import logging
import sqlite3
from typing import List, Tuple, Optional
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, FSInputFile

from config import TOKEN

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("SchoolDBBot")

# -------------------- Константы --------------------
DB_NAME = "school_data.db"
EXPORTS_DIR = Path("exports")
EXPORTS_DIR.mkdir(exist_ok=True)

# -------------------- FSM --------------------
class StudentForm(StatesGroup):
    name = State()
    age = State()
    grade = State()

# -------------------- База данных --------------------
def init_db() -> None:
    """Создание таблицы, если её нет."""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    grade TEXT NOT NULL
                )
            """)
            conn.commit()
        logger.info("База данных готова (таблица students).")
    except sqlite3.Error:
        logger.exception("Ошибка при инициализации БД")

def add_student(name: str, age: int, grade: str) -> int:
    """Добавить ученика, вернуть его ID."""
    name = name.strip()
    grade = grade.strip()
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO students (name, age, grade) VALUES (?, ?, ?)",
            (name, age, grade)
        )
        conn.commit()
        return cur.lastrowid

def list_students(limit: int = 10) -> List[Tuple[int, str, int, str]]:
    """Последние N учеников (id, name, age, grade)."""
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, age, grade FROM students ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return cur.fetchall()

def export_students_csv() -> Path:
    """Экспорт всех записей в CSV, вернуть путь к файлу."""
    filename = EXPORTS_DIR / f"students_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with sqlite3.connect(DB_NAME) as conn, open(filename, "w", encoding="utf-8") as f:
        cur = conn.cursor()
        cur.execute("SELECT id, name, age, grade FROM students ORDER BY id ASC")
        rows = cur.fetchall()
        f.write("id,name,age,grade\n")
        for _id, name, age, grade in rows:
            # простейший CSV (без экранирования запятых в тексте)
            f.write(f"{_id},{name},{age},{grade}\n")
    return filename

# -------------------- Утилиты --------------------
def parse_age(text: str) -> Optional[int]:
    try:
        age = int(text.strip())
    except ValueError:
        return None
    return age if 1 <= age <= 120 else None

# -------------------- Роутер --------------------
router = Router()

@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот для записи учеников в базу данных.\n"
        "Давай внесём твою информацию.\n\n"
        "Как тебя зовут?\n\n"
        "ℹ️ В любой момент можно отменить: /cancel"
    )
    await state.set_state(StudentForm.name)

@router.message(Command("help"))
async def on_help(message: Message):
    await message.answer(
        "Доступные команды:\n"
        "/start — начать ввод ученика\n"
        "/cancel — отменить текущий ввод\n"
        "/students — показать последние записи\n"
        "/export_csv — выгрузить всех учеников в CSV"
    )

@router.message(Command("cancel"))
async def on_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Операция отменена. Чтобы начать заново — /start")

@router.message(StudentForm.name)
async def on_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не должно быть пустым. Введи имя ещё раз.")
        return
    await state.update_data(name=name)
    await message.answer("Сколько тебе лет? (целое число 1–120)")
    await state.set_state(StudentForm.age)

@router.message(StudentForm.age)
async def on_age(message: Message, state: FSMContext):
    age = parse_age(message.text or "")
    if age is None:
        await message.answer("Возраст должен быть целым числом от 1 до 120. Попробуй ещё раз.")
        return
    await state.update_data(age=age)
    await message.answer("В каком ты классе? (например: 5А или 5)")
    await state.set_state(StudentForm.grade)

@router.message(StudentForm.grade)
async def on_grade(message: Message, state: FSMContext):
    grade = (message.text or "").strip()
    if not grade:
        await message.answer("Класс не должен быть пустым. Введи класс ещё раз.")
        return

    data = await state.get_data()
    name = data.get("name", "").strip()
    age = data.get("age")

    # финальная валидация
    if not name or not isinstance(age, int):
        await message.answer("Данные некорректны. Начни заново: /start")
        await state.clear()
        return

    try:
        student_id = add_student(name=name, age=age, grade=grade)
        await message.answer(
            "✅ Сохранено!\n\n"
            f"ID: {student_id}\n"
            f"Имя: {name}\n"
            f"Возраст: {age}\n"
            f"Класс: {grade}\n\n"
            "Чтобы добавить ещё — /start\n"
            "Посмотреть последние записи — /students"
        )
    except sqlite3.Error:
        logger.exception("Ошибка при сохранении в БД")
        await message.answer("Ошибка БД. Попробуйте позже.")
    finally:
        await state.clear()

@router.message(Command("students"))
async def on_students(message: Message):
    rows = list_students(limit=10)
    if not rows:
        await message.answer("Список пуст. Пока нет записей в таблице students.")
        return
    lines = ["Последние записи (до 10):"]
    for _id, name, age, grade in rows:
        lines.append(f"• #{_id}: {name}, {age} лет, класс {grade}")
    await message.answer("\n".join(lines))

@router.message(Command("export_csv"))
async def on_export_csv(message: Message):
    try:
        path = export_students_csv()
        await message.answer_document(FSInputFile(path), caption="Экспорт учеников (CSV).")
    except Exception:
        logger.exception("Ошибка при экспорте CSV")
        await message.answer("Не удалось сформировать CSV. Попробуйте позже.")

# -------------------- Запуск --------------------
async def main():
    init_db()
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен. Ожидаю обновления...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())