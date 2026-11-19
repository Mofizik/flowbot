"""Бот против локального поддельного сервера Bot API по HTTP: настоящий long polling,
настоящая отправка файлов multipart-запросами — то же, что происходит с api.telegram.org."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("aiogram")
from aiohttp import web                                       # noqa: E402

from flowgen_bot.bot import run                               # noqa: E402
from flowgen_bot.config import Config                         # noqa: E402
from flowgen_bot.db import Database                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = (ROOT / "examples" / "demo.cpp").read_bytes()
TOKEN = "123456:TEST-token-for-tests_0123456789abcdef"
USER = {"id": 42, "is_bot": False, "first_name": "Илья", "username": "ilya", "language_code": "ru"}
CHAT = {"id": 42, "type": "private"}


class FakeTelegram:
    def __init__(self):
        self.updates = [
            {"update_id": 1, "message": {"message_id": 1, "date": 1763580000, "chat": CHAT, "from": USER,
                                         "text": "/start", "entities": [{"type": "bot_command", "offset": 0,
                                                                         "length": 6}]}},
            {"update_id": 2, "message": {"message_id": 2, "date": 1763580001, "chat": CHAT, "from": USER,
                                         "document": {"file_id": "F1", "file_unique_id": "U1",
                                                      "file_name": "demo.cpp", "file_size": len(DEMO)}}},
        ]
        self.calls = []          # (метод, поля, файлы {имя: размер})
        self.done = asyncio.Event()
        self.mid = 100

    def msg(self, **kw):
        self.mid += 1
        return {"message_id": self.mid, "date": 1763580100, "chat": CHAT, **kw}

    async def api(self, request: web.Request):
        method = request.match_info["method"]
        fields, files = {}, {}
        if request.content_type == "multipart/form-data":
            reader = await request.multipart()
            async for part in reader:
                data = await part.read()
                if part.filename:
                    files[part.filename] = data
                else:
                    fields[part.name] = data.decode("utf-8")
        elif request.can_read_body:
            fields = dict(await request.post()) if request.content_type != "application/json" \
                else await request.json()
        self.calls.append((method, fields, {k: len(v) for k, v in files.items()}))
        if method == "getUpdates":
            offset = int(fields.get("offset") or 0)
            pending = [u for u in self.updates if u["update_id"] >= offset]
            if not pending:
                await asyncio.sleep(0.2)
            return web.json_response({"ok": True, "result": pending})
        if method == "getMe":
            return web.json_response({"ok": True, "result": {"id": 1, "is_bot": True, "first_name": "flowgen",
                                                             "username": "flowgen_test_bot"}})
        if method == "getFile":
            return web.json_response({"ok": True, "result": {"file_id": "F1", "file_unique_id": "U1",
                                                             "file_path": "documents/demo.cpp"}})
        if method in ("sendMessage", "editMessageText"):
            if "Готово" in fields.get("text", ""):
                self.done.set()
            return web.json_response({"ok": True, "result": self.msg(text=fields.get("text", ""))})
        if method == "sendMediaGroup":
            media = json.loads(fields["media"])
            return web.json_response({"ok": True, "result": [self.msg(document={"file_id": f"d{i}",
                                                                                "file_unique_id": f"d{i}"})
                                                             for i in range(len(media))]})
        if method == "sendDocument":
            return web.json_response({"ok": True, "result": self.msg(document={"file_id": "d", "file_unique_id": "d"})})
        return web.json_response({"ok": True, "result": True})

    async def file(self, request: web.Request):
        return web.Response(body=DEMO)


def test_polling_end_to_end(tmp_path):
    async def main():
        tg = FakeTelegram()
        app = web.Application()
        app.router.add_post("/bot{token}/{method}", tg.api)
        app.router.add_get("/file/bot{token}/{path:.*}", tg.file)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        cfg = Config(token=TOKEN, db_path=tmp_path / "bot.sqlite3", api_url=f"http://127.0.0.1:{port}", workers=1)
        task = asyncio.create_task(run(cfg))
        try:
            await asyncio.wait_for(tg.done.wait(), 120)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            await runner.cleanup()
        return tg

    tg = asyncio.new_event_loop().run_until_complete(main())
    methods = [c[0] for c in tg.calls]
    assert "setMyCommands" in methods and "getMe" in methods
    groups = [c for c in tg.calls if c[0] == "sendMediaGroup"]
    assert len(groups) == 1
    files = groups[0][2]
    assert sorted(files) == sorted(["read_numbers.png", "index_of_max.png", "grade.png", "print_report.png",
                                    "main.png"])
    assert all(size > 10_000 for size in files.values())         # настоящие PNG дошли целиком
    texts = [c[1].get("text", "") for c in tg.calls if c[0] == "sendMessage"]
    assert any("Привет" in t for t in texts) and any("Готово: 5 функций" in t for t in texts)
    db = Database(tmp_path / "bot.sqlite3")
    r = db.rows("SELECT status, file_name, raw FROM requests")[0]
    assert r["status"] == "ok" and r["file_name"] == "demo.cpp" and r["raw"] == DEMO
    db.close()
