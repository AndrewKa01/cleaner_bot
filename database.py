import sqlite3
from typing import Optional

DB_NAME = "data.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# ИНИЦИАЛИЗАЦИЯ
# =========================

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Участники
    cur.execute("""
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        username TEXT NOT NULL,
        name TEXT NOT NULL,
        active INTEGER DEFAULT 1
    )
    """)

    # Задачи уборки
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        task_type TEXT NOT NULL,
        place TEXT NOT NULL,
        task_date TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        reminder_sent INTEGER DEFAULT 0,
        control_count INTEGER DEFAULT 0,
        description TEXT DEFAULT '',
        notify_at TEXT DEFAULT ''
    )
    """)

    # Миграция: добавить колонки если их нет (для существующих БД)
    try:
        cur.execute("ALTER TABLE tasks ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE tasks ADD COLUMN notify_at TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE tasks ADD COLUMN last_message_id INTEGER")
    except Exception:
        pass

    # Кастомные типы событий
    cur.execute("""
    CREATE TABLE IF NOT EXISTS event_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


# =========================
# УЧАСТНИКИ
# =========================

def add_member(tg_id: int, username: str, name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO members (tg_id, username, name, active)
    VALUES (?, ?, ?, 1)
    ON CONFLICT(tg_id) DO UPDATE SET
        username = excluded.username,
        name = excluded.name,
        active = 1
    """, (tg_id, username, name))
    conn.commit()
    conn.close()


def update_member(tg_id: int, username: str, name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    UPDATE members SET username = ?, name = ?
    WHERE tg_id = ?
    """, (username, name, tg_id))
    conn.commit()
    conn.close()


def deactivate_member(tg_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE members SET active = 0 WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()


def get_all_members(active_only: bool = True):
    conn = get_connection()
    cur = conn.cursor()
    if active_only:
        cur.execute("SELECT tg_id, username, name FROM members WHERE active = 1 ORDER BY name")
    else:
        cur.execute("SELECT tg_id, username, name, active FROM members ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_member(tg_id: int) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT tg_id, username, name, active FROM members WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# =========================
# КАСТОМНЫЕ ТИПЫ СОБЫТИЙ
# =========================

def add_event_type(name: str) -> bool:
    """Добавить кастомный тип события. Возвращает False если уже существует."""
    key = f"custom_{name.lower().replace(' ', '_')}"
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO event_types (key, name) VALUES (?, ?)", (key, name))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_all_event_types() -> list:
    """Все кастомные типы событий."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, key, name FROM event_types ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_event_type(event_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM event_types WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()


def get_event_type_by_key(key: str) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, key, name FROM event_types WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# =========================
# ЗАДАЧИ
# =========================

def add_task(tg_id: int, task_type: str, place: str, task_date: str,
             description: str = '', notify_at: str = '') -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO tasks (tg_id, task_type, place, task_date, status, reminder_sent, control_count, description, notify_at)
    VALUES (?, ?, ?, ?, 'pending', 0, 0, ?, ?)
    """, (tg_id, task_type, place, task_date, description, notify_at))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_task(task_id: int) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_tasks() -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT t.*, m.username, m.name
    FROM tasks t
    JOIN members m ON t.tg_id = m.tg_id
    WHERE t.status IN ('pending', 'waiting_confirm')
    ORDER BY t.task_date
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_task_waiting_confirm(task_id: int):
    """Пометить задачу как ожидающую подтверждения админа."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'waiting_confirm' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def set_task_pending(task_id: int):
    """Вернуть задачу в статус pending (отклонено админом)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'pending' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_tasks_for_reminder(task_date: str) -> list:
    """Задачи на конкретную дату, которым ещё не отправлено пятничное напоминание.
    Включает все типы кроме horse."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT t.*, m.username, m.name
    FROM tasks t
    JOIN members m ON t.tg_id = m.tg_id
    WHERE t.task_date = ?
      AND t.reminder_sent = 0
      AND t.task_type != 'horse'
    """, (task_date,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_last_message_id(task_id: int, message_id: int):
    """Запомнить id последнего отправленного по задаче сообщения (для удаления при следующем напоминании)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET last_message_id = ? WHERE id = ?", (message_id, task_id))
    conn.commit()
    conn.close()


def mark_reminder_sent(task_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET reminder_sent = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def increment_control_count(task_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET control_count = control_count + 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def mark_task_done(task_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def clear_month_cleaning_tasks(year: int, month: int, place: str):
    """Удалить задачи влажной/сухой уборки за месяц для конкретной локации."""
    conn = get_connection()
    cur = conn.cursor()
    month_prefix = f"{year}-{month:02d}-%"
    cur.execute("""
    DELETE FROM tasks
    WHERE task_date LIKE ?
      AND task_type IN ('wet', 'dry')
      AND place = ?
    """, (month_prefix, place))
    conn.commit()
    conn.close()


def delete_task(task_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_custom_tasks_to_notify(notify_at: str) -> list:
    """Кастомные задачи у которых notify_at совпадает с текущим временем (YYYY-MM-DD HH:MM)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
    SELECT t.*, m.username, m.name
    FROM tasks t
    JOIN members m ON t.tg_id = m.tg_id
    WHERE t.notify_at = ?
      AND t.task_type NOT IN ('wet', 'dry', 'horse')
      AND t.status = 'pending'
    """, (notify_at,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tasks_by_member(tg_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE tg_id = ? ORDER BY task_date DESC", (tg_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
