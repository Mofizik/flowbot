"""Хранилище бота: SQLite, один файл.

Таблицы:
  users     — кто пользовался: id, username, имя, язык, когда впервые и в последний раз, сколько запросов;
  settings  — настройки каждого пользователя (JSON);
  requests  — каждый присланный код: кто, когда, откуда (файл/текст), сам код и исходные байты файла,
              номер задания/вариант/лабораторная/группа/автор из кода, настройки, результат, ошибки, время;
  results   — по строке на нарисованную функцию: размер схемы, предупреждения, JSON функции (часть 2);
  events    — команды, нажатия кнопок, изменения настроек, отказы и ошибки.
Время везде — UTC в формате ISO 8601.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,          -- Telegram user id
    username        TEXT,
    first_name      TEXT,
    last_name       TEXT,
    language_code   TEXT,
    is_premium      INTEGER,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    requests_count  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    data        TEXT NOT NULL,                    -- JSON
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    chat_id         INTEGER,
    chat_type       TEXT,
    message_id      INTEGER,
    message_date    TEXT,                         -- когда сообщение отправлено (по данным Telegram)
    created_at      TEXT NOT NULL,                -- когда бот его получил
    finished_at     TEXT,
    duration_ms     INTEGER,
    source          TEXT NOT NULL,                -- file | text | again
    file_name       TEXT,
    mime_type       TEXT,
    file_size       INTEGER,
    file_id         TEXT,                         -- можно заново скачать файл из Telegram
    file_unique_id  TEXT,
    raw             BLOB,                         -- байты файла как есть
    encoding        TEXT,
    code            TEXT,                         -- текст кода
    code_sha256     TEXT,
    code_lines      INTEGER,
    task            TEXT,                         -- номер задания, если нашёлся в коде
    subpoint        TEXT,                         -- пункт задания
    variant         TEXT,
    lab             TEXT,                         -- номер лабораторной
    student_group   TEXT,
    author          TEXT,                         -- «Автор: …» из кода
    settings        TEXT,                         -- JSON настроек на момент запроса
    status          TEXT NOT NULL,                -- processing | ok | no_functions | error | rejected | timeout
    error           TEXT,
    functions_count INTEGER,
    functions       TEXT,                         -- JSON: имена функций
    warnings        TEXT,                         -- JSON
    problems        TEXT,                         -- JSON: замечания проверки раскладки
    files_sent      TEXT,                         -- JSON: имена отправленных файлов
    flowgen_version TEXT,
    bot_version     TEXT
);
CREATE INDEX IF NOT EXISTS requests_user ON requests(user_id, created_at);
CREATE INDEX IF NOT EXISTS requests_sha ON requests(code_sha256);
CREATE INDEX IF NOT EXISTS requests_task ON requests(lab, task, variant);
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER NOT NULL REFERENCES requests(id),
    function    TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    width_in    REAL,
    height_in   REAL,
    columns     INTEGER,
    warnings    TEXT,                             -- JSON
    problems    TEXT,                             -- JSON
    error       TEXT,
    files       TEXT,                             -- JSON
    ir          TEXT                              -- JSON функции (выход части 2) — по нему схему можно перерисовать
);
CREATE INDEX IF NOT EXISTS results_request ON results(request_id);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    chat_id     INTEGER,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,                    -- command | button | settings | rejected | error | start
    data        TEXT                              -- JSON
);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _j(v: Any) -> Optional[str]:
    return None if v is None else json.dumps(v, ensure_ascii=False)


class Database:
    """Тонкая обёртка над sqlite3. Все методы потокобезопасны."""

    def __init__(self, path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _exec(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    # ------------------------------------------------------------ пользователи
    def touch_user(self, user, count_request: bool = False) -> None:
        """Записывает/обновляет пользователя (объект aiogram User или похожий)."""
        t = now()
        self._exec(
            """INSERT INTO users (id, username, first_name, last_name, language_code, is_premium,
                                  first_seen, last_seen, requests_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name,
                   last_name=excluded.last_name, language_code=excluded.language_code,
                   is_premium=excluded.is_premium, last_seen=excluded.last_seen,
                   requests_count=users.requests_count + excluded.requests_count""",
            (user.id, getattr(user, "username", None), getattr(user, "first_name", None),
             getattr(user, "last_name", None), getattr(user, "language_code", None),
             int(bool(getattr(user, "is_premium", False))), t, t, 1 if count_request else 0))

    def user(self, user_id: int) -> Optional[sqlite3.Row]:
        return self._exec("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    # ------------------------------------------------------------ настройки
    def get_settings(self, user_id: int) -> Optional[dict]:
        row = self._exec("SELECT data FROM settings WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def set_settings(self, user_id: int, data: dict) -> None:
        self._exec("""INSERT INTO settings (user_id, data, updated_at) VALUES (?, ?, ?)
                      ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                   (user_id, _j(data), now()))

    # ------------------------------------------------------------ запросы
    def add_request(self, **f) -> int:
        f.setdefault("created_at", now())
        f.setdefault("status", "processing")
        for k in ("settings",):
            if k in f and not isinstance(f[k], (str, type(None))):
                f[k] = _j(f[k])
        cols = ", ".join(f)
        marks = ", ".join("?" for _ in f)
        return self._exec(f"INSERT INTO requests ({cols}) VALUES ({marks})", tuple(f.values())).lastrowid

    def finish_request(self, request_id: int, **f) -> None:
        f.setdefault("finished_at", now())
        for k in ("functions", "warnings", "problems", "files_sent"):
            if k in f and not isinstance(f[k], (str, type(None))):
                f[k] = _j(f[k])
        sets = ", ".join(f"{k}=?" for k in f)
        self._exec(f"UPDATE requests SET {sets} WHERE id=?", tuple(f.values()) + (request_id,))

    def add_result(self, request_id: int, function: str, ok: bool, width: float = None, height: float = None,
                   columns: int = None, warnings=None, problems=None, error: str = None, files=None,
                   ir: Optional[str] = None) -> None:
        self._exec("""INSERT INTO results (request_id, function, ok, width_in, height_in, columns, warnings,
                                           problems, error, files, ir)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (request_id, function, int(ok), width, height, columns, _j(warnings), _j(problems), error,
                    _j(files), ir))

    def last_code(self, user_id: int) -> Optional[sqlite3.Row]:
        """Последний присланный пользователем код (для «Перерисовать»)."""
        return self._exec("""SELECT * FROM requests WHERE user_id=? AND code IS NOT NULL AND source != 'again'
                             ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()

    def recent_requests(self, user_id: int, seconds: int) -> int:
        row = self._exec("""SELECT COUNT(*) AS n FROM requests WHERE user_id=?
                            AND created_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?)""",
                         (user_id, f"-{int(seconds)} seconds")).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------ события
    def event(self, kind: str, user_id: Optional[int] = None, chat_id: Optional[int] = None, **data) -> None:
        self._exec("INSERT INTO events (user_id, chat_id, created_at, kind, data) VALUES (?, ?, ?, ?, ?)",
                   (user_id, chat_id, now(), kind, _j(data) if data else None))

    # ------------------------------------------------------------ статистика
    def stats(self) -> Dict[str, Any]:
        q = lambda sql, a=(): self._exec(sql, a).fetchone()[0]
        return {
            "users": q("SELECT COUNT(*) FROM users"),
            "active_7d": q("SELECT COUNT(*) FROM users WHERE last_seen >= strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-7 days')"),
            "requests": q("SELECT COUNT(*) FROM requests"),
            "requests_today": q("SELECT COUNT(*) FROM requests WHERE created_at >= strftime('%Y-%m-%dT00:00:00+00:00','now')"),
            "ok": q("SELECT COUNT(*) FROM requests WHERE status='ok'"),
            "errors": q("SELECT COUNT(*) FROM requests WHERE status IN ('error','timeout')"),
            "functions": q("SELECT COUNT(*) FROM results"),
            "top_tasks": [tuple(r) for r in self._exec(
                """SELECT COALESCE(lab,'?') AS lab, COALESCE(task,'?') AS task, COUNT(*) AS n FROM requests
                   WHERE task IS NOT NULL OR lab IS NOT NULL GROUP BY lab, task ORDER BY n DESC LIMIT 5""").fetchall()],
        }

    def backup(self, dest) -> Path:
        """Целостная копия базы (можно делать во время работы бота)."""
        dest = Path(dest)
        with self.lock:
            out = sqlite3.connect(str(dest))
            try:
                self.conn.backup(out)
            finally:
                out.close()
        return dest

    def rows(self, sql: str, args=()) -> List[sqlite3.Row]:
        return self._exec(sql, args).fetchall()
