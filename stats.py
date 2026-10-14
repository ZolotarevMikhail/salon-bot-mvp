# -*- coding: utf-8 -*-
"""Статистика и память бота (SQLite — локальная база, файл stats.db).

Что храним:
    - каждое сообщение клиента (для анализа и передачи истории админу)
    - «подхват» переписки: какое сообщение админа относится к какому
      клиенту, чтобы ответить реплаем на пересланное сообщение

Правки сюда вносить не нужно — файл работает сам.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")


def _connect():
    """Новое подключение к базе. check_same_thread=False — бот общается
    с базой из разных обработчиков Telegram."""
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db():
    """Создаём таблицы, если их ещё нет."""
    connection = _connect()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_name TEXT,
            direction TEXT,   -- 'in' — от клиента, 'out' — от бота
            text TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,          -- кто просил администратора
            admin_msg_id INTEGER,     -- id сообщения, отправленного админу
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        """
    )
    connection.commit()
    connection.close()


def log_message(chat_id, user_name, direction, text):
    """Записываем одно сообщение клиента в таблицу messages."""
    try:
        connection = _connect()
        connection.execute(
            "INSERT INTO messages "
            "(chat_id, user_name, direction, text) VALUES (?, ?, ?, ?)",
            (chat_id, user_name, direction, text[:4000]),
        )
        connection.commit()
        connection.close()
    except Exception as error:  # статистика не должна ломать бот
        print(f"[stats] не удалось записать сообщение: {error}")


def save_handoff(chat_id, admin_msg_id):
    """Запоминаем связь «сообщение админу ↔ клиент» для реплаев."""
    try:
        connection = _connect()
        connection.execute(
            "INSERT INTO handoffs (chat_id, admin_msg_id) VALUES (?, ?)",
            (chat_id, admin_msg_id),
        )
        connection.commit()
        connection.close()
    except Exception as error:
        print(f"[stats] не удалось сохранить передачу: {error}")


def find_client_by_admin_msg(admin_msg_id):
    """По id сообщения, на которое админ ответил, вернуть chat_id клиента."""
    try:
        connection = _connect()
        row = connection.execute(
            "SELECT chat_id FROM handoffs "
            "WHERE admin_msg_id = ? ORDER BY id DESC LIMIT 1",
            (admin_msg_id,),
        ).fetchone()
        connection.close()
        return row[0] if row else None
    except Exception as error:
        print(f"[stats] не удалось найти клиента: {error}")
        return None


def dialog_history(chat_id, limit=10):
    """Последние сообщения клиента — прикладываем к передаче админу."""
    try:
        connection = _connect()
        rows = connection.execute(
            "SELECT direction, text, created_at FROM messages "
            "WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        connection.close()
        return list(reversed(rows))
    except Exception:
        return []
