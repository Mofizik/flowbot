"""Telegram-бот: присылаете код на C++ (файлом или текстом) — получаете блок-схемы файлами.

Меню Telegram (кнопка «Меню» слева от поля ввода) содержит команды:
  /settings — настройки кнопками (форматы, столбцы, подписи, стрелки, качество);
  /again    — перерисовать последний код с новыми настройками;
  /help     — как пользоваться.
Для администраторов (ADMIN_IDS): /stats — статистика, /export — копия базы.
"""
from __future__ import annotations

import asyncio
import html
import logging
import multiprocessing
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, ErrorEvent, InputMediaDocument, Message,
                           TelegramObject, User)

import flowgen
from flowgen.cpp_parser import decode_source

from .config import Config, load_config
from .db import Database
from .service import CODE_EXT, JSON_EXT, JobOut, looks_like_code, render_job, sha256
from .settings import apply, describe, keyboard, normalize
from .taskinfo import task_info

BOT_VERSION = "1.0.0"
log = logging.getLogger("flowgen_bot")

START_TEXT = (
    "Привет! Я рисую блок-схемы по ГОСТ 19.701 для программ на C++.\n\n"
    "Пришлите код <b>файлом</b> (.cpp, .h) или <b>текстом</b> — я пришлю схему каждой функции файлами "
    "(без сжатия, сразу в отчёт).\n\n"
    "Формат, число столбцов, подписи и стрелки меняются в /settings, а /again перерисует последний код "
    "с новыми настройками.\n\n"
    "<i>Присланный код и сведения о запросе сохраняются, чтобы улучшать бота.</i>"
)
HELP_TEXT = (
    "<b>Как пользоваться</b>\n"
    "1. Пришлите файл .cpp или вставьте код сообщением (длинный код лучше файлом — Telegram режет "
    "сообщения длиннее 4096 символов).\n"
    "2. Получите схемы: по файлу на функцию. Форматы: PNG (для Word), SVG, PDF, draw.io (можно править).\n"
    "3. /settings — настройки кнопками, /again — перерисовать последний код.\n\n"
    "Можно прислать и JSON схемы (его даёт настройка «JSON функций»): поправьте тексты блоков "
    "и пришлите файл обратно — схема перерисуется."
)
NOT_CODE_TEXT = ("Это не похоже на код на C++. Пришлите программу файлом .cpp или текстом "
                 "(с функцией, например <code>int main() { … }</code>). Подсказка — /help.")


# ====================================================================== выполнение в отдельных процессах

class Runner:
    """Пул процессов для рисования, с ограничением времени и «один запрос за раз от человека»."""

    def __init__(self, workers: int):
        self.workers = max(1, workers)
        self.pool = self._new_pool()
        self.busy: set = set()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=self.workers, mp_context=multiprocessing.get_context("spawn"))

    async def run(self, code: str, filename: str, settings: dict, max_functions: int, timeout: float) -> JobOut:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self.pool, render_job, code, filename, settings, max_functions)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._restart()
            raise

    def _restart(self) -> None:
        """Зависшую задачу не отменить — останавливаем процессы и создаём пул заново."""
        old = self.pool
        self.pool = self._new_pool()
        for p in list(getattr(old, "_processes", {}).values()):
            try:
                p.terminate()
            except Exception:
                pass
        old.shutdown(wait=False, cancel_futures=True)

    def warm_up(self) -> None:
        for _ in range(self.workers):
            self.pool.submit(render_job, "int main() { return 0; }", "warmup.cpp", {"formats": ["png"]}, 1)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


# ====================================================================== доступ и учёт пользователей

class UserMiddleware(BaseMiddleware):
    """Записывает пользователя в базу и не пускает тех, кого нет в ALLOWED_USERS."""

    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: Dict[str, Any]) -> Any:
        user: Optional[User] = data.get("event_from_user")
        cfg: Config = data["config"]
        db: Database = data["db"]
        if user is not None:
            db.touch_user(user)
            if not cfg.is_allowed(user.id, user.username):
                chat = data.get("event_chat")
                db.event("rejected", user.id, chat.id if chat else None)
                text = "Этот бот только для своей группы. Попросите владельца добавить вас."
                if isinstance(event, Message):
                    await event.answer(text)
                elif isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                return None
        return await handler(event, data)


# ====================================================================== команды

async def cmd_start(message: Message, db: Database) -> None:
    db.event("start", message.from_user.id, message.chat.id, payload=message.text)
    await message.answer(START_TEXT)


async def cmd_help(message: Message, db: Database) -> None:
    db.event("command", message.from_user.id, message.chat.id, command="help")
    await message.answer(HELP_TEXT)


async def cmd_settings(message: Message, db: Database) -> None:
    db.event("command", message.from_user.id, message.chat.id, command="settings")
    s = normalize(db.get_settings(message.from_user.id))
    await message.answer(describe(s), reply_markup=keyboard(s))


async def cmd_again(message: Message, bot: Bot, db: Database, config: Config, runner: Runner) -> None:
    db.event("command", message.from_user.id, message.chat.id, command="again")
    await redraw_last(bot, message.chat.id, message.chat.type, message.from_user, db, config, runner)


async def cmd_stats(message: Message, db: Database, config: Config) -> None:
    u = message.from_user
    if not config.is_admin(u.id, u.username):
        await message.answer(HELP_TEXT)
        return
    st = db.stats()
    tasks = "\n".join(f"  ЛР {lab}, задание {task}: {n}" for lab, task, n in st["top_tasks"]) or "  —"
    await message.answer(
        f"<b>Статистика</b>\nПользователей: {st['users']} (за 7 дней: {st['active_7d']})\n"
        f"Запросов: {st['requests']} (сегодня: {st['requests_today']})\n"
        f"Успешно: {st['ok']}, с ошибкой: {st['errors']}\nНарисовано функций: {st['functions']}\n"
        f"Частые задания:\n{tasks}")


async def cmd_export(message: Message, db: Database, config: Config) -> None:
    u = message.from_user
    if not config.is_admin(u.id, u.username):
        await message.answer(HELP_TEXT)
        return
    db.event("command", u.id, message.chat.id, command="export")
    with tempfile.TemporaryDirectory() as tmp:
        path = db.backup(Path(tmp) / "flowgen_bot.sqlite3")
        await message.answer_document(BufferedInputFile(path.read_bytes(), filename="flowgen_bot.sqlite3"),
                                      caption="Копия базы данных бота")


# ====================================================================== кнопки настроек

async def on_settings_button(cb: CallbackQuery, bot: Bot, db: Database, config: Config, runner: Runner) -> None:
    user = cb.from_user
    db.event("button", user.id, cb.message.chat.id if cb.message else None, data=cb.data)
    old = normalize(db.get_settings(user.id))
    if cb.data == "s:close":
        await cb.answer("Сохранено")
        if cb.message:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        return
    if cb.data == "s:again":
        await cb.answer("Перерисовываю…")
        if cb.message:
            await redraw_last(bot, cb.message.chat.id, cb.message.chat.type, user, db, config, runner)
        return
    new, alert = apply(old, cb.data)
    if alert:
        await cb.answer(alert, show_alert=True)
        return
    if new != old:
        db.set_settings(user.id, new)
        db.event("settings", user.id, cb.message.chat.id if cb.message else None, before=old, after=new)
    await cb.answer()
    if cb.message and new != old:
        try:
            await cb.message.edit_text(describe(new), reply_markup=keyboard(new))
        except TelegramBadRequest:          # «message is not modified» и т. п.
            pass


# ====================================================================== код

async def on_document(message: Message, bot: Bot, db: Database, config: Config, runner: Runner) -> None:
    doc = message.document
    name = doc.file_name or "code.cpp"
    ext = Path(name).suffix.lower()
    if ext not in CODE_EXT | JSON_EXT:
        db.event("rejected", message.from_user.id, message.chat.id, reason="extension", file_name=name)
        await message.answer("Пришлите файл с кодом: .cpp, .h, .hpp, .cc, .txt — или JSON схемы.")
        return
    if (doc.file_size or 0) > config.max_file_kb * 1024:
        db.event("rejected", message.from_user.id, message.chat.id, reason="size", file_name=name,
                 file_size=doc.file_size)
        await message.answer(f"Файл слишком большой (больше {config.max_file_kb} КБ).")
        return
    buf = await bot.download(doc)
    raw = buf.read()
    code = decode_source(raw)
    meta = {"file_name": name, "mime_type": doc.mime_type, "file_size": doc.file_size,
            "file_id": doc.file_id, "file_unique_id": doc.file_unique_id, "raw": raw}
    await process(bot, message.chat.id, message.chat.type, message.from_user, db, config, runner,
                  code=code, filename=name, source="file", message=message, meta=meta)


async def on_text(message: Message, bot: Bot, db: Database, config: Config, runner: Runner) -> None:
    text = message.text or ""
    if not looks_like_code(text):
        db.event("not_code", message.from_user.id, message.chat.id, text=text[:500])
        await message.answer(NOT_CODE_TEXT)
        return
    raw = text.encode("utf-8")
    meta = {"file_name": None, "mime_type": "text/plain", "file_size": len(raw), "raw": raw}
    await process(bot, message.chat.id, message.chat.type, message.from_user, db, config, runner,
                  code=text, filename="code.cpp", source="text", message=message, meta=meta)


async def on_other(message: Message, db: Database) -> None:
    db.event("other", message.from_user.id if message.from_user else None, message.chat.id,
             content_type=str(message.content_type))
    await message.answer(HELP_TEXT)


async def on_error(event: ErrorEvent, db: Database) -> bool:
    log.exception("Ошибка при обработке обновления", exc_info=event.exception)
    upd = event.update
    user = None
    chat = None
    for obj in (upd.message, upd.callback_query):
        if obj is not None:
            user = obj.from_user.id if obj.from_user else None
            msg = obj if isinstance(obj, Message) else obj.message
            chat = msg.chat.id if msg else None
    db.event("error", user, chat, error=f"{type(event.exception).__name__}: {event.exception}",
             traceback="".join(traceback.format_exception(type(event.exception), event.exception,
                                                          event.exception.__traceback__))[-4000:])
    return True


def make_router() -> Router:
    """Обработчики в порядке проверки: команды, кнопки, файлы, текст, всё остальное."""
    r = Router()
    r.message.register(cmd_start, CommandStart())
    r.message.register(cmd_help, Command("help"))
    r.message.register(cmd_settings, Command("settings"))
    r.message.register(cmd_again, Command("again"))
    r.message.register(cmd_stats, Command("stats"))
    r.message.register(cmd_export, Command("export"))
    r.callback_query.register(on_settings_button, F.data.startswith("s:"))
    r.message.register(on_document, F.document)
    r.message.register(on_text, F.text & ~F.text.regexp(r"^/[A-Za-z]"))   # «// комментарий» — это код
    r.message.register(on_other)
    r.errors.register(on_error)
    return r


# ====================================================================== общая часть

async def redraw_last(bot: Bot, chat_id: int, chat_type: str, user: User, db: Database, config: Config,
                      runner: Runner) -> None:
    row = db.last_code(user.id)
    if row is None:
        await bot.send_message(chat_id, "Сначала пришлите код — потом его можно перерисовать.")
        return
    meta = {"file_name": row["file_name"], "mime_type": row["mime_type"], "file_size": row["file_size"],
            "file_id": row["file_id"], "file_unique_id": row["file_unique_id"]}
    await process(bot, chat_id, chat_type, user, db, config, runner, code=row["code"],
                  filename=row["file_name"] or "code.cpp", source="again", message=None, meta=meta,
                  again_of=row["id"])


async def process(bot: Bot, chat_id: int, chat_type: str, user: User, db: Database, config: Config,
                  runner: Runner, code: str, filename: str, source: str, message: Optional[Message],
                  meta: dict, again_of: Optional[int] = None) -> None:
    if user.id in runner.busy:
        db.event("rejected", user.id, chat_id, reason="busy")
        await bot.send_message(chat_id, "Ещё рисую предыдущий код — подождите немного.")
        return
    if db.recent_requests(user.id, 60) >= config.per_minute:
        db.event("rejected", user.id, chat_id, reason="rate")
        await bot.send_message(chat_id, "Слишком много запросов подряд — попробуйте через минуту.")
        return

    settings = normalize(db.get_settings(user.id))
    info = task_info(code)
    raw = meta.get("raw")
    data = code.encode("utf-8")
    rid = db.add_request(
        user_id=user.id, chat_id=chat_id, chat_type=str(chat_type),
        message_id=message.message_id if message else None,
        message_date=message.date.isoformat() if message and message.date else None,
        source=source, file_name=meta.get("file_name"), mime_type=meta.get("mime_type"),
        file_size=meta.get("file_size"), file_id=meta.get("file_id"), file_unique_id=meta.get("file_unique_id"),
        raw=raw, encoding=_encoding_of(raw) if raw is not None else None, code=code, code_sha256=sha256(data),
        code_lines=code.count("\n") + 1, task=info["task"], subpoint=info["subpoint"], variant=info["variant"],
        lab=info["lab"], student_group=info["group"], author=info["author"], settings=settings,
        flowgen_version=flowgen.__version__, bot_version=BOT_VERSION,
        error=f"повтор запроса {again_of}" if again_of else None)
    db.touch_user(user, count_request=True)

    runner.busy.add(user.id)
    status = await bot.send_message(chat_id, "Рисую схемы…")
    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        job = await runner.run(code, filename, settings, config.max_functions, config.timeout_s)
    except asyncio.TimeoutError:
        db.finish_request(rid, status="timeout", error=f"дольше {config.timeout_s} с")
        await _edit_or_send(bot, status, chat_id, "Не успел нарисовать за отведённое время. "
                                                  "Попробуйте прислать функции по отдельности.")
        return
    except Exception as e:
        db.finish_request(rid, status="error", error=f"{type(e).__name__}: {e}")
        await _edit_or_send(bot, status, chat_id, "Не получилось нарисовать — ошибка записана, разберусь.")
        raise
    finally:
        runner.busy.discard(user.id)

    for fo in job.functions:
        db.add_result(rid, fo.function, fo.ok, fo.width, fo.height, fo.columns, fo.warnings, fo.problems,
                      fo.error, fo.files, fo.ir)
    warnings = list(job.notes) + [w for fo in job.functions for w in fo.warnings]
    problems = [p for fo in job.functions for p in fo.problems]
    base = dict(duration_ms=job.duration_ms, functions_count=job.total_functions,
                functions=[fo.function for fo in job.functions], warnings=warnings, problems=problems)
    if job.error:
        db.finish_request(rid, status="error", error=job.error, **base)
        await _edit_or_send(bot, status, chat_id, f"Не получилось разобрать файл:\n<code>{html.escape(job.error)}</code>")
        return
    if not job.functions:
        db.finish_request(rid, status="no_functions", **base)
        await _edit_or_send(bot, status, chat_id, "Не нашёл ни одной функции. Пришлите программу целиком — "
                                                  "с <code>int main() { … }</code> или другими функциями.")
        return

    sent = await send_files(bot, chat_id, job.files)
    failed = [fo for fo in job.functions if not fo.ok]
    db.finish_request(rid, status="ok" if not failed else "error", files_sent=sent,
                      error="; ".join(f"{fo.function}: {fo.error}" for fo in failed) or None, **base)
    try:
        await bot.delete_message(chat_id, status.message_id)
    except TelegramBadRequest:
        pass
    await bot.send_message(chat_id, summary(job, info))


async def send_files(bot: Bot, chat_id: int, files: List[Tuple[str, bytes]]) -> List[str]:
    """Файлы документами (Telegram их не сжимает), альбомами до 10 штук."""
    sent: List[str] = []
    for i in range(0, len(files), 10):
        chunk = files[i:i + 10]
        if len(chunk) == 1:
            name, data = chunk[0]
            await bot.send_document(chat_id, BufferedInputFile(data, filename=name))
        else:
            await bot.send_media_group(chat_id, [InputMediaDocument(media=BufferedInputFile(d, filename=n))
                                                 for n, d in chunk])
        sent += [n for n, _ in chunk]
    return sent


def summary(job: JobOut, info: dict) -> str:
    n = len(job.functions)
    lines = [f"Готово: {_plural(n, 'функция', 'функции', 'функций')}, {_plural(len(job.files), 'файл', 'файла', 'файлов')}."]
    found = [f"ЛР {info['lab']}" if info.get("lab") else None,
             f"задание {info['task']}" + (f" пункт {info['subpoint']}" if info.get("subpoint") else "")
             if info.get("task") else None,
             f"вариант {info['variant']}" if info.get("variant") else None]
    found = [x for x in found if x]
    if found:
        lines.append("В коде указано: " + ", ".join(found) + ".")
    for fo in job.functions:
        if not fo.ok:
            lines.append(f"• {html.escape(fo.function)}: не нарисована ({html.escape(fo.error or '')})")
        elif fo.columns > 1:
            lines.append(f"• {html.escape(fo.function)}: {fo.columns} столбца")
    notes = list(dict.fromkeys(list(job.notes) + [w for fo in job.functions for w in fo.warnings]))
    if notes:
        lines.append("\nЗамечания:")
        lines += [f"• {html.escape(w)}" for w in notes[:8]]
        if len(notes) > 8:
            lines.append(f"• …и ещё {len(notes) - 8}")
    if any(fo.problems for fo in job.functions):
        lines.append("\n⚠ Автопроверка нашла наложения на схеме — посмотрите её внимательно.")
    lines.append("\nНастройки — /settings, перерисовать — /again.")
    text = "\n".join(lines)
    return text if len(text) < 4000 else text[:3990] + "…"


def _plural(n: int, one: str, few: str, many: str) -> str:
    k = n % 100
    word = many if 11 <= k <= 14 else one if k % 10 == 1 else few if 2 <= k % 10 <= 4 else many
    return f"{n} {word}"


def _encoding_of(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


async def _edit_or_send(bot: Bot, status: Message, chat_id: int, text: str) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=status.message_id)
    except TelegramBadRequest:
        await bot.send_message(chat_id, text)


# ====================================================================== запуск

COMMANDS = [
    BotCommand(command="start", description="Что умеет бот"),
    BotCommand(command="settings", description="Настройки: форматы, столбцы, подписи"),
    BotCommand(command="again", description="Перерисовать последний код"),
    BotCommand(command="help", description="Как пользоваться"),
]


def build_dispatcher(config: Config, db: Database, runner: Runner) -> Dispatcher:
    dp = Dispatcher(config=config, db=db, runner=runner)
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())
    dp.include_router(make_router())
    return dp


async def run(config: Config) -> None:
    db = Database(config.db_path)
    runner = Runner(config.workers)
    runner.warm_up()
    session = None
    if config.api_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.api_url))
    bot = Bot(config.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(config, db, runner)
    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.get_me()
        log.info("Бот @%s запущен, база: %s", me.username, config.db_path)
        await dp.start_polling(bot)
    finally:
        runner.close()
        db.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(load_config()))
