"""Бот целиком, но вместо Telegram — поддельный API: настоящие обработчики, процессы рисования и SQLite."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiogram")

from aiogram import Bot                                                      # noqa: E402
from aiogram.client.session.base import BaseSession                          # noqa: E402
from aiogram.methods import (GetFile, SendDocument, SendMediaGroup, SendMessage,  # noqa: E402
                             EditMessageText)
from aiogram.types import (CallbackQuery, Chat, Document, File, Message,     # noqa: E402
                           Update, User)

from flowgen_bot.bot import Runner, build_dispatcher                         # noqa: E402
from flowgen_bot.config import Config                                        # noqa: E402
from flowgen_bot.db import Database                                          # noqa: E402
from flowgen_bot.settings import DEFAULTS, apply, normalize                  # noqa: E402
from flowgen_bot.taskinfo import task_info                                   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = (ROOT / "examples" / "demo.cpp").read_bytes()
TOKEN = "123456:TEST-token-for-tests_0123456789abcdef"
CODE = """// Лабораторная работа №5. Задание 8 пункт в, вариант 3
// Автор: Иванов Иван гр. 6352
int sq(int x) { return x * x; }
int main() { int a = sq(2); if (a > 3) a = 0; return 0; }
"""


class FakeSession(BaseSession):
    """Отвечает на запросы бота как Telegram и запоминает их."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.files = {}
        self.mid = 1000

    def _msg(self, chat_id, text=None, document=None):
        self.mid += 1
        return Message(message_id=self.mid, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"),
                       text=text, document=document)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return self._msg(method.chat_id, method.text)
        if isinstance(method, SendDocument):
            return self._msg(method.chat_id, document=Document(file_id="x", file_unique_id="x"))
        if isinstance(method, SendMediaGroup):
            return [self._msg(method.chat_id, document=Document(file_id="x", file_unique_id="x"))
                    for _ in method.media]
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u" + method.file_id, file_path=method.file_id)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]

    async def close(self):
        pass

    def sent_files(self):
        names = []
        for c in self.calls:
            if isinstance(c, SendMediaGroup):
                names += [m.media.filename for m in c.media]
            elif isinstance(c, SendDocument):
                names.append(c.document.filename)
        return names

    def texts(self):
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]


USER = User(id=42, is_bot=False, first_name="Илья", last_name="К", username="ilya", language_code="ru")
CHAT = Chat(id=42, type="private")


class Env:
    def __init__(self, tmp_path, **cfg):
        self.session = FakeSession()
        self.bot = Bot(TOKEN, session=self.session)
        self.db = Database(tmp_path / "bot.sqlite3")
        self.config = Config(token=TOKEN, db_path=tmp_path / "bot.sqlite3", **cfg)
        self.runner = Runner(1)
        self.dp = build_dispatcher(self.config, self.db, self.runner)
        self.uid = 1

    async def send(self, user=USER, **kw):
        self.uid += 1
        msg = Message(message_id=self.uid, date=datetime.now(timezone.utc), chat=Chat(id=user.id, type="private"),
                      from_user=user, **kw)
        await self.dp.feed_update(self.bot, Update(update_id=self.uid, message=msg))

    async def text(self, text, user=USER):
        await self.send(user=user, text=text)

    async def file(self, name, data, user=USER):
        fid = f"f{self.uid + 1}"
        self.session.files[fid] = data
        await self.send(user=user, document=Document(file_id=fid, file_unique_id="u" + fid, file_name=name,
                                                     file_size=len(data), mime_type="text/x-c++src"))

    async def press(self, data, user=USER):
        self.uid += 1
        msg = Message(message_id=500, date=datetime.now(timezone.utc), chat=Chat(id=user.id, type="private"),
                      text="Настройки")
        cb = CallbackQuery(id=str(self.uid), from_user=user, chat_instance="ci", data=data, message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=self.uid, callback_query=cb))

    def close(self):
        self.runner.close()
        self.db.close()


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_task_info():
    i = task_info(CODE)
    assert i == {"task": "8", "subpoint": "в", "variant": "3", "lab": "5", "group": "6352", "author": "Иванов Иван"}
    assert task_info("int main() { return 0; }") == dict.fromkeys(i)


def test_settings_buttons():
    s = normalize(None)
    assert s == DEFAULTS
    s, alert = apply(s, "s:fmt:png")
    assert alert and s["formats"] == ["png"]          # последний формат не выключить
    s, _ = apply(s, "s:fmt:svg")
    s, _ = apply(s, "s:fmt:png")
    assert s["formats"] == ["svg"]
    s, _ = apply(s, "s:labels")
    assert s["labels"] == ["+", "−"]
    s, _ = apply(s, "s:col:2")
    s, _ = apply(s, "s:dpi")
    assert s["columns"] == "2" and s["dpi"] == 400
    assert normalize({"formats": ["exe"], "columns": "99", "dpi": 7}) == DEFAULTS


def test_start_and_not_code(env):
    run(env.text("/start"))
    run(env.text("привет"))
    texts = env.session.texts()
    assert "Привет" in texts[0] and "не похоже на код" in texts[1]
    u = env.db.user(42)
    assert u["username"] == "ilya" and u["first_name"] == "Илья"
    kinds = [r["kind"] for r in env.db.rows("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["start", "not_code"]


def test_file_is_drawn_and_everything_is_saved(env):
    run(env.file("demo.cpp", DEMO))
    assert env.session.sent_files() == ["read_numbers.png", "index_of_max.png", "grade.png", "print_report.png",
                                        "main.png"]
    assert "Готово: 5 функций, 5 файлов." in env.session.texts()[-1]
    r = env.db.rows("SELECT * FROM requests")[0]
    assert r["status"] == "ok" and r["source"] == "file" and r["file_name"] == "demo.cpp"
    assert r["raw"] == DEMO and r["code"] == DEMO.decode("utf-8") and r["encoding"] == "utf-8"
    assert r["functions_count"] == 5 and json.loads(r["files_sent"])[0] == "read_numbers.png"
    assert r["message_date"] and r["created_at"] and r["finished_at"] and r["duration_ms"] > 0
    assert r["flowgen_version"] == "1.0.0" and r["file_id"]
    res = env.db.rows("SELECT * FROM results ORDER BY id")
    assert [x["function"] for x in res] == ["read_numbers", "index_of_max", "grade", "print_report", "main"]
    assert json.loads(res[0]["ir"])["name"] == "read_numbers"
    assert env.db.user(42)["requests_count"] == 1


def test_text_code_with_task_number(env):
    run(env.text(CODE))
    r = env.db.rows("SELECT * FROM requests")[0]
    assert (r["lab"], r["task"], r["subpoint"], r["variant"], r["student_group"], r["author"]) == \
        ("5", "8", "в", "3", "6352", "Иванов Иван")
    assert r["source"] == "text" and r["status"] == "ok"
    assert "ЛР 5, задание 8 пункт в, вариант 3" in env.session.texts()[-1]


def test_settings_menu_and_again(env):
    run(env.text("/settings"))
    assert "Настройки" in env.session.texts()[-1]
    run(env.press("s:fmt:svg"))
    run(env.press("s:json"))
    assert env.db.get_settings(42)["formats"] == ["png", "svg"]
    run(env.press("s:again"))                 # кода ещё не было
    assert "Сначала пришлите код" in env.session.texts()[-1]
    run(env.text(CODE))
    before = len(env.session.sent_files())
    run(env.text("/again"))
    files = env.session.sent_files()[before:]
    assert sorted(files) == sorted(["sq.png", "sq.svg", "sq.json", "main.png", "main.svg", "main.json"])
    rows = env.db.rows("SELECT source, settings FROM requests ORDER BY id")
    assert [r["source"] for r in rows] == ["text", "again"]
    assert json.loads(rows[1]["settings"])["json"] is True
    ev = env.db.rows("SELECT data FROM events WHERE kind='settings'")
    assert len(ev) == 2


def test_json_roundtrip(env):
    run(env.press("s:json"))
    run(env.file("prog.cpp", CODE.encode("cp1251")))          # файл из Windows
    assert env.db.rows("SELECT encoding FROM requests")[0]["encoding"] == "cp1251"
    ir = json.loads(env.db.rows("SELECT ir FROM results WHERE function='main'")[0]["ir"])
    ir["body"][0]["text"] = "a = квадрат(2)"
    run(env.file("main.json", json.dumps(ir, ensure_ascii=False).encode("utf-8")))
    r = env.db.rows("SELECT status, functions FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert r["status"] == "ok" and json.loads(r["functions"]) == ["main"]


def test_rejections(tmp_path):
    e = Env(tmp_path, allowed=frozenset({"ilya"}), per_minute=1, max_file_kb=1)
    try:
        stranger = User(id=7, is_bot=False, first_name="Чужой")
        run(e.text(CODE, user=stranger))
        assert "только для своей группы" in e.session.texts()[-1]
        run(e.file("big.cpp", b"int main() {}" + b" " * 2000))
        assert "слишком большой" in e.session.texts()[-1]
        run(e.file("pic.png", b"\x89PNG"))
        assert "Пришлите файл с кодом" in e.session.texts()[-1]
        run(e.text(CODE))
        run(e.text(CODE))
        assert "Слишком много запросов" in e.session.texts()[-1]
        reasons = [json.loads(r["data"] or "{}").get("reason") for r in
                   e.db.rows("SELECT data FROM events WHERE kind='rejected' ORDER BY id")]
        assert reasons == [None, "size", "extension", "rate"]
        assert e.db.rows("SELECT COUNT(*) AS n FROM requests")[0]["n"] == 1
    finally:
        e.close()


def test_admin_commands(tmp_path):
    e = Env(tmp_path, admins=frozenset({"42"}))
    try:
        run(e.text(CODE))
        run(e.text("/stats"))
        assert "Пользователей: 1" in e.session.texts()[-1] and "задание 8: 1" in e.session.texts()[-1]
        run(e.text("/export"))
        assert e.session.sent_files()[-1] == "flowgen_bot.sqlite3"
    finally:
        e.close()


def test_timeout_restarts_workers(tmp_path):
    e = Env(tmp_path, timeout_s=0)
    try:
        run(e.text(CODE))
        assert "Не успел нарисовать" in e.session.texts()[-1]
        assert e.db.rows("SELECT status FROM requests")[0]["status"] == "timeout"
        object.__setattr__(e.config, "timeout_s", 60)      # пул пересоздан — следующий запрос рисуется
        run(e.text("/again"))
        assert e.db.rows("SELECT status FROM requests ORDER BY id DESC LIMIT 1")[0]["status"] == "ok"
    finally:
        e.close()
