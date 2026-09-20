from __future__ import annotations

import asyncio
import html
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from bot.catalog import PAGE_SIZE, Book, Catalog

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

TELEGRAM_LIMIT = 49 * 1024 * 1024
last_query: dict[int, str] = {}


def env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip().strip('"')
    return default


def build_results_kb(books: list[Book], catalog: Catalog, offset: int) -> InlineKeyboardMarkup:
    available = catalog.available_archives()
    page = books[offset : offset + PAGE_SIZE]
    buttons: list[list[InlineKeyboardButton]] = []
    for book in page:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=book.button_label(book.archive in available),
                    callback_data=f"get:{book.file_id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"pg:{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < len(books):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pg:{offset + PAGE_SIZE}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def results_text(query: str, books: list[Book], catalog: Catalog, offset: int) -> str:
    if not books:
        return f"Ничего не нашёл по запросу: {html.escape(query)}"
    available = catalog.available_archives()
    have = sum(1 for b in books if b.archive in available)
    shown = min(PAGE_SIZE, len(books) - offset)
    return (
        f"Запрос: {html.escape(query)}\n"
        f"Найдено: {len(books)} (в скачанных архивах: {have})\n"
        f"Страница {offset // PAGE_SIZE + 1}, показано {shown}.\n"
        "📗 — файл есть, 📦 — архив ещё не скачан."
    )


async def main() -> None:
    token = env("TELEGRAM_BOT_API", "TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_API is not set")

    library_dir = Path(env("LIBRARY_DIR", default="/books") or "/books")
    index_name = env("INDEX_FILE", default="flibusta_fb2_local.inpx") or "flibusta_fb2_local.inpx"
    index_path = library_dir / index_name
    db_path = Path(env("DATA_DIR", default="/data") or "/data") / "catalog.sqlite"
    proxy = env("SOCKS_PROXY", default="socks5://proxy:1080")

    catalog = Catalog(library_dir=library_dir, db_path=db_path, index_path=index_path)

    session = AiohttpSession(proxy=proxy)
    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Пришлите название книги или фамилию автора — найду в индексе Flibusta и пришлю FB2.\n"
            "Команды: /start, /help, /stats"
        )

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "Поиск по названию, автору и серии.\n"
            "Нажмите на книгу в списке, чтобы получить файл из zip-архива."
        )

    @dp.message(Command("stats"))
    async def stats_cmd(message: Message) -> None:
        total = catalog.conn.execute(
            "SELECT COUNT(*) FROM books WHERE deleted = 0"
        ).fetchone()[0]
        archives = catalog.available_archives()
        local = catalog.conn.execute(
            f"SELECT COUNT(*) FROM books WHERE deleted = 0 AND archive IN ({','.join('?' * len(archives))})"
            if archives
            else "SELECT 0",
            tuple(archives),
        ).fetchone()[0]
        await message.answer(
            f"Книг в индексе: {total}\n"
            f"Скачанных архивов: {len(archives)}\n"
            f"Книг, которые можно выдать сейчас: {local}"
        )

    @dp.message(F.text)
    async def search_msg(message: Message) -> None:
        query = (message.text or "").strip()
        if not query:
            return
        books = catalog.search(query)
        last_query[message.from_user.id] = query
        await message.answer(
            results_text(query, books, catalog, 0),
            reply_markup=build_results_kb(books, catalog, 0) if books else None,
        )

    @dp.callback_query(F.data.startswith("pg:"))
    async def paginate(callback: CallbackQuery) -> None:
        query = last_query.get(callback.from_user.id)
        if not query:
            await callback.answer("Сначала сделайте поиск", show_alert=True)
            return
        offset = int(callback.data.split(":", 1)[1])
        books = catalog.search(query)
        await callback.message.edit_text(
            results_text(query, books, catalog, offset),
            reply_markup=build_results_kb(books, catalog, offset),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("get:"))
    async def send_book(callback: CallbackQuery) -> None:
        file_id = callback.data.split(":", 1)[1]
        book = catalog.get(file_id)
        if not book:
            await callback.answer("Книга не найдена в индексе", show_alert=True)
            return
        if not catalog.archive_exists(book.archive):
            await callback.answer()
            await callback.message.answer(
                f"Книга есть в индексе, но архив ещё не скачан:\n"
                f"<code>{book.archive}</code>\n"
                f"{book.caption()}"
            )
            return
        await callback.answer("Собираю файл…")
        try:
            payload = catalog.extract(book)
        except FileNotFoundError:
            await callback.message.answer(
                f"В архиве {book.archive} нет файла {book.filename}"
            )
            return
        if len(payload) > TELEGRAM_LIMIT:
            await callback.message.answer(
                f"Файл слишком большой для Telegram Bot API ({len(payload)} байт)."
            )
            return
        safe_title = "".join(
            ch if ch.isalnum() or ch in " ._-" else "_" for ch in book.title
        )[:80] or book.file_id
        document = BufferedInputFile(payload, filename=f"{safe_title}.fb2")
        await callback.message.answer_document(
            document, caption=html.escape(book.caption())[:1024]
        )

    log.info("Starting bot, library=%s proxy=%s", library_dir, proxy)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
