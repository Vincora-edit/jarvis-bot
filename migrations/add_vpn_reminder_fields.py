"""
Миграция: Добавление полей для напоминаний об истечении VPN.
Запускать один раз: python migrations/add_vpn_reminder_fields.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_database.db")


def migrate():
    print(f"Подключаемся к БД: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # === 1. Поля в таблице users ===
    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    print(f"Текущие колонки users: {user_columns}")

    user_new_columns = [
        ("vpn_reminder_3d_sent", "BOOLEAN DEFAULT 0"),
        ("vpn_reminder_1d_sent", "BOOLEAN DEFAULT 0"),
    ]

    for col_name, col_type in user_new_columns:
        if col_name not in user_columns:
            print(f"Добавляем колонку users.{col_name} {col_type}")
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        else:
            print(f"Колонка users.{col_name} уже существует")

    # === 2. Поля в таблице subscriptions ===
    cursor.execute("PRAGMA table_info(subscriptions)")
    sub_columns = {row[1] for row in cursor.fetchall()}
    print(f"Текущие колонки subscriptions: {sub_columns}")

    sub_new_columns = [
        ("reminder_3d_sent", "BOOLEAN DEFAULT 0"),
        ("reminder_1d_sent", "BOOLEAN DEFAULT 0"),
    ]

    for col_name, col_type in sub_new_columns:
        if col_name not in sub_columns:
            print(f"Добавляем колонку subscriptions.{col_name} {col_type}")
            cursor.execute(f"ALTER TABLE subscriptions ADD COLUMN {col_name} {col_type}")
        else:
            print(f"Колонка subscriptions.{col_name} уже существует")

    conn.commit()
    conn.close()
    print("Миграция завершена!")


if __name__ == "__main__":
    migrate()
