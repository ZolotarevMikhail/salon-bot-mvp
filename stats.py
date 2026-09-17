# -*- coding: utf-8 -*-
# ============================================================
# СТАТИСТИКА И ПАМЯТЬ БОТА (SQLite — обычная локальная база, файл stats.db)
#
# Что храним:
#  - каждое сообщение клиента (для анализа и передачи истории админу)
#  - «подхват» переписки: какое сообщение админа относится к какому клиенту,
#    чтобы ответить реплаем на пересланное сообщение
#
# Правки сюда вносить не нужно — файл работает сам.
# ============================================================

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")


def _connect():
    # check_same_thread=False — бот общается с базой из разных обработчиков
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    conn.executescript(
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
    conn.commit()
    conn.close()


def log_message(chat_id, user_name, direction, text):
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO messages (chat_id, user_name, direction, text) VALUES (?, ?, ?, ?)",
            (chat_id, user_name, direction, text[:4000]),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # статистика не должна ломать бот
        print(f"[stats] не удалось записать сообщение: {e}")


def save_handoff(chat_id, admin_msg_id):
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO handoffs (chat_id, admin_msg_id) VALUES (?, ?)",
            (chat_id, admin_msg_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[stats] не удалось сохранить передачу: {e}")


def find_client_by_admin_msg(admin_msg_id):
    """По id сообщения, на которое админ ответил, вернуть chat_id клиента."""
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT chat_id FROM handoffs WHERE admin_msg_id = ? ORDER BY id DESC LIMIT 1",
            (admin_msg_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[stats] не удалось найти клиента: {e}")
        return None


def dialog_history(chat_id, limit=10):
    """Последние сообщения клиента — прикладываем к передаче админу."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT direction, text, created_at FROM messages "
            "WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        conn.close()
        return list(reversed(rows))
    except Exception:
        return []