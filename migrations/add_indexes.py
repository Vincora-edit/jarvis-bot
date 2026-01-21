"""
Миграция: Добавление индексов для оптимизации запросов

Добавляет индексы на user_id во всех таблицах где они отсутствовали.
"""
import sqlite3
import os

DB_PATH = os.getenv("JARVIS_DB_PATH", "/opt/jarvis-bot/bot_database.db")


def migrate():
    """Добавляет индексы"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Список индексов для создания: (имя_индекса, таблица, колонка)
    indexes = [
        ("ix_tasks_user_id", "tasks", "user_id"),
        ("ix_diary_entries_user_id", "diary_entries", "user_id"),
        ("ix_habits_user_id", "habits", "user_id"),
        ("ix_habit_logs_user_id", "habit_logs", "user_id"),
        ("ix_habit_logs_habit_id", "habit_logs", "habit_id"),
        ("ix_memory_contexts_user_id", "memory_contexts", "user_id"),
        ("ix_conversations_user_id", "conversations", "user_id"),
    ]

    for index_name, table, column in indexes:
        try:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})")
            print(f"✅ Created index {index_name}")
        except Exception as e:
            print(f"⚠️ Index {index_name}: {e}")

    conn.commit()
    conn.close()
    print("\n✅ Migration completed!")


if __name__ == "__main__":
    migrate()
