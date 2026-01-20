"""
Миграция: Добавление полей для смарт-привычек.
Запускать один раз: python migrations/add_smart_habits_fields.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_database.db")


def migrate():
    print(f"Подключаемся к БД: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Проверяем существующие колонки
    cursor.execute("PRAGMA table_info(habits)")
    columns = {row[1] for row in cursor.fetchall()}
    print(f"Текущие колонки habits: {columns}")

    # Добавляем новые колонки если их нет
    new_columns = [
        ("learned_times", "TEXT"),
        ("last_reminder_adjust", "DATETIME"),
        ("ignored_count", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            print(f"Добавляем колонку: {col_name} {col_type}")
            cursor.execute(f"ALTER TABLE habits ADD COLUMN {col_name} {col_type}")
        else:
            print(f"Колонка {col_name} уже существует")

    conn.commit()
    conn.close()
    print("Миграция завершена!")


if __name__ == "__main__":
    migrate()
