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
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from bot.access import Access, AccessMiddleware, parse_username_arg
from bot.catalog import PAGE_SIZE, Book, Catalog

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

TELEGRAM_LIMIT = 49 * 1024 * 1024
CATALOG_BTN = "📚 Каталог"
STATS_BTN = "📊 Статистика"
last_query: dict[int, str] = {}
browse: dict[int, dict] = {}


def env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip().strip('"')
    return default


def collect_admins() -> list[str]:
    names: list[str] = []
    for key, value in os.environ.items():
        if not key.startswith("ADMIN") or "USERNAME" not in key:
            continue
        cleaned = (value or "").strip().strip('"')
        if cleaned:
            names.append(cleaned)
    return names


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CATALOG_BTN), KeyboardButton(text=STATS_BTN)]],
        resize_keyboard=True,
    )


def chunk_buttons(items: list[InlineKeyboardButton], per_row: int = 8) -> list[list[InlineKeyboardButton]]:
    return [items[i : i + per_row] for i in range(0, len(items), per_row)]


def catalog_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="По авторам", callback_data="cm:a")],
            [InlineKeyboardButton(text="По названиям", callback_data="cm:t")],
        ]
    )


def letter_kb(mode: str, letters: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=letter, callback_data=f"cl:{mode}:{letter}")
        for letter in letters
    ]
    rows = chunk_buttons(buttons, 8)
    rows.append([InlineKeyboardButton(text="⬅️ Меню каталога", callback_data="cm:")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def authors_kb(authors: list[tuple[str, int]], offset: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (name, count) in enumerate(authors):
        label = f"{name} ({count})"
        rows.append(
            [InlineKeyboardButton(text=label[:64], callback_data=f"ca:{idx}")]
        )
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"cp:a:{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"cp:a:{offset + PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="🔤 Буквы", callback_data="cm:a"),
            InlineKeyboardButton(text="⬅️ Меню", callback_data="cm:"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_books_kb(
    books: list[Book],
    catalog: Catalog,
    offset: int,
    total: int,
    page_prefix: str,
    back_data: str,
) -> InlineKeyboardMarkup:
    available = catalog.available_archives()
    rows: list[list[InlineKeyboardButton]] = []
    for book in books:
        rows.append(
            [
                InlineKeyboardButton(
                    text=book.button_label(book.archive in available),
                    callback_data=f"get:{book.file_id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}{offset - PAGE_SIZE}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}{offset + PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_menu_text(catalog: Catalog) -> str:
    books, authors = catalog.local_counts()
    if not books:
        return (
            "В скачанных архивах пока нет книг.\n"
            "Положите zip в <code>books/</code> и выполните <code>./scripts/sync-books.sh</code>."
        )
    return (
        "Каталог книг из скачанных архивов.\n"
        f"Доступно сейчас: {books} книг, {authors} авторов.\n"
        "Полный индекс Flibusta доступен через поиск по названию."
    )


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


def start_text(is_admin: bool) -> str:
    text = (
        "Пришлите название книги или фамилию автора — найду в индексе Flibusta и пришлю FB2.\n"
        "Команды: /start, /help, /catalog, /stats"
    )
    if is_admin:
        text += (
            "\n\nАдмин-команды:\n"
            "/add @username — выдать доступ\n"
            "/del @username — забрать доступ\n"
            "/users — список пользователей"
        )
    return text


async def require_admin(message: Message, access: Access) -> bool:
    if access.is_admin(message.from_user):
        return True
    await message.answer("Эта команда только для администраторов.")
    return False


async def close_session(session: AiohttpSession) -> None:
    try:
        await session.close()
    except Exception:
        pass


class FailoverSession(AiohttpSession):
    """Use a primary Telegram path and retry once via a fallback path."""

    def __init__(self, proxy: str | None = None, fallback_proxy: str | None = None):
        super().__init__(proxy=proxy)
        self._fallback = AiohttpSession(proxy=fallback_proxy)
        self._fallback_label = fallback_proxy or "direct"

    async def make_request(self, bot, method, timeout=None):
        try:
            return await super().make_request(bot, method, timeout=timeout)
        except TelegramNetworkError as exc:
            log.warning(
                "Telegram %s failed (%s), retrying via %s",
                getattr(method, "__api_method__", type(method).__name__),
                exc,
                self._fallback_label,
            )
            return await self._fallback.make_request(bot, method, timeout=timeout)

    async def close(self) -> None:
        await super().close()
        await close_session(self._fallback)


async def _telegram_ok(token: str, session: AiohttpSession) -> bool:
    probe = Bot(token=token, session=session)
    try:
        await asyncio.wait_for(probe.get_me(), timeout=15)
        return True
    except Exception as exc:
        log.warning("Telegram probe failed: %s", exc)
        return False


async def telegram_session(token: str, proxy: str | None) -> tuple[AiohttpSession, str]:
    direct = AiohttpSession()
    if await _telegram_ok(token, direct):
        log.info("Using direct Telegram connection")
        if proxy:
            await close_session(direct)
            return FailoverSession(proxy=None, fallback_proxy=proxy), "direct"
        return direct, "direct"
    await close_session(direct)

    if not proxy:
        raise SystemExit("Cannot reach Telegram directly, and VLESS_URL is empty")

    proxied = FailoverSession(proxy=proxy, fallback_proxy=None)
    if await _telegram_ok(token, proxied):
        log.info("Using SOCKS proxy with direct fallback")
        return proxied, proxy
    await close_session(proxied)
    raise SystemExit("Cannot reach Telegram via proxy or directly")


async def main() -> None:
    token = env("TELEGRAM_BOT_API", "TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_API is not set")

    library_dir = Path(env("LIBRARY_DIR", default="/books") or "/books")
    index_name = env("INDEX_FILE", default="flibusta_fb2_local.inpx") or "flibusta_fb2_local.inpx"
    index_path = library_dir / index_name
    data_dir = Path(env("DATA_DIR", default="/data") or "/data")
    db_path = data_dir / "catalog.sqlite"
    vpn_url = env("VLESS_URL", "VPN_URL")
    proxy = env("SOCKS_PROXY", default="socks5://proxy:1080") if vpn_url else None

    catalog = Catalog(library_dir=library_dir, db_path=db_path, index_path=index_path)
    access = Access(db_path=data_dir / "access.sqlite", admin_usernames=collect_admins())
    if not access.admins:
        log.warning("No admin usernames configured; nobody can grant access")

    session, transport = await telegram_session(token, proxy)
    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.message.middleware(AccessMiddleware(access))
    dp.callback_query.middleware(AccessMiddleware(access))

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            start_text(access.is_admin(message.from_user)),
            reply_markup=main_kb(),
        )

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            start_text(access.is_admin(message.from_user)),
            reply_markup=main_kb(),
        )

    async def send_stats(message: Message) -> None:
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
            f"Книг, которые можно выдать сейчас: {local}",
            reply_markup=main_kb(),
        )

    async def show_catalog_menu(message: Message | None, callback: CallbackQuery | None = None) -> None:
        text = catalog_menu_text(catalog)
        markup = catalog_menu_kb() if catalog.local_counts()[0] else None
        if callback:
            await callback.message.edit_text(text, reply_markup=markup)
            await callback.answer()
            return
        assert message is not None
        await message.answer(text, reply_markup=markup)

    async def show_letters(callback: CallbackQuery, mode: str) -> None:
        field = "author" if mode == "a" else "title"
        letters = catalog.local_letters(field)
        if not letters:
            await callback.answer("Нет доступных книг", show_alert=True)
            return
        title = "Выберите букву автора:" if mode == "a" else "Выберите букву названия:"
        await callback.message.edit_text(title, reply_markup=letter_kb(mode, letters))
        await callback.answer()

    async def show_authors(callback: CallbackQuery, offset: int) -> None:
        uid = callback.from_user.id
        state = browse.setdefault(uid, {})
        letter = state.get("letter")
        if not letter:
            await show_letters(callback, "a")
            return
        authors, total = catalog.local_authors(letter, offset, PAGE_SIZE)
        state["authors"] = authors
        state["a_off"] = offset
        if not authors:
            await callback.answer("На эту букву авторов нет", show_alert=True)
            return
        page = offset // PAGE_SIZE + 1
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        await callback.message.edit_text(
            f"Авторы на «{html.escape(letter)}», {page}/{pages}",
            reply_markup=authors_kb(authors, offset, total),
        )
        await callback.answer()

    async def show_author_books(callback: CallbackQuery, offset: int) -> None:
        uid = callback.from_user.id
        state = browse.setdefault(uid, {})
        author = state.get("author")
        if not author:
            await callback.answer("Сначала выберите автора", show_alert=True)
            return
        books, total = catalog.local_books_by_author(author, offset, PAGE_SIZE)
        state["b_off"] = offset
        back = f"cp:a:{state.get('a_off', 0)}"
        page = offset // PAGE_SIZE + 1
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        await callback.message.edit_text(
            f"{html.escape(author)}\nКниг: {total}, страница {page}/{pages}",
            reply_markup=catalog_books_kb(
                books, catalog, offset, total, "cb:", back
            ),
        )
        await callback.answer()

    async def show_title_books(callback: CallbackQuery, offset: int) -> None:
        uid = callback.from_user.id
        state = browse.setdefault(uid, {})
        letter = state.get("title_letter")
        if not letter:
            await show_letters(callback, "t")
            return
        books, total = catalog.local_books_by_title_letter(letter, offset, PAGE_SIZE)
        state["t_off"] = offset
        page = offset // PAGE_SIZE + 1
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        await callback.message.edit_text(
            f"Названия на «{html.escape(letter)}», {page}/{pages} ({total})",
            reply_markup=catalog_books_kb(
                books, catalog, offset, total, "ct:", "cm:t"
            ),
        )
        await callback.answer()

    @dp.message(Command("stats"))
    @dp.message(F.text == STATS_BTN)
    async def stats_cmd(message: Message) -> None:
        await send_stats(message)

    @dp.message(Command("catalog"))
    @dp.message(F.text == CATALOG_BTN)
    async def catalog_cmd(message: Message) -> None:
        await show_catalog_menu(message)

    @dp.message(Command("add", "adduser"))
    async def add_user(message: Message, command: CommandObject) -> None:
        if not await require_admin(message, access):
            return
        username = parse_username_arg(command.args)
        if not username:
            await message.answer("Использование: /add @username")
            return
        access.add(username, message.from_user.username or "")
        await message.answer(f"Доступ выдан: @{username}")

    @dp.message(Command("del", "deluser", "remove"))
    async def del_user(message: Message, command: CommandObject) -> None:
        if not await require_admin(message, access):
            return
        username = parse_username_arg(command.args)
        if not username:
            await message.answer("Использование: /del @username")
            return
        if username in access.admins:
            await message.answer("Нельзя забрать доступ у администратора из .env")
            return
        if access.remove(username):
            await message.answer(f"Доступ отозван: @{username}")
        else:
            await message.answer(f"Пользователь @{username} не найден в списке")

    @dp.message(Command("users"))
    async def list_users(message: Message) -> None:
        if not await require_admin(message, access):
            return
        admins = ", ".join(f"@{name}" for name in sorted(access.admins)) or "—"
        allowed = access.list_users()
        users = "\n".join(f"@{name}" for name in allowed) if allowed else "пока никого"
        await message.answer(f"Админы: {admins}\n\nДопущенные пользователи:\n{users}")

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

    @dp.callback_query(F.data.startswith("cm:"))
    async def catalog_mode(callback: CallbackQuery) -> None:
        mode = callback.data.split(":", 1)[1]
        if mode == "a":
            await show_letters(callback, "a")
            return
        if mode == "t":
            await show_letters(callback, "t")
            return
        await show_catalog_menu(None, callback)

    @dp.callback_query(F.data.startswith("cl:"))
    async def catalog_letter(callback: CallbackQuery) -> None:
        _, mode, letter = callback.data.split(":", 2)
        uid = callback.from_user.id
        state = browse.setdefault(uid, {})
        if mode == "a":
            state["letter"] = letter
            await show_authors(callback, 0)
            return
        state["title_letter"] = letter
        await show_title_books(callback, 0)

    @dp.callback_query(F.data.startswith("cp:a:"))
    async def catalog_authors_page(callback: CallbackQuery) -> None:
        offset = int(callback.data.split(":")[-1])
        await show_authors(callback, offset)

    @dp.callback_query(F.data.startswith("ca:"))
    async def catalog_pick_author(callback: CallbackQuery) -> None:
        idx = int(callback.data.split(":", 1)[1])
        state = browse.get(callback.from_user.id) or {}
        authors = state.get("authors") or []
        if idx < 0 or idx >= len(authors):
            await callback.answer("Список устарел, откройте каталог снова", show_alert=True)
            return
        state["author"] = authors[idx][0]
        await show_author_books(callback, 0)

    @dp.callback_query(F.data.startswith("cb:"))
    async def catalog_author_books_page(callback: CallbackQuery) -> None:
        offset = int(callback.data.split(":", 1)[1])
        await show_author_books(callback, offset)

    @dp.callback_query(F.data.startswith("ct:"))
    async def catalog_title_page(callback: CallbackQuery) -> None:
        offset = int(callback.data.split(":", 1)[1])
        await show_title_books(callback, offset)

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

    log.info(
        "Starting bot, library=%s proxy=%s admins=%s",
        library_dir,
        transport,
        ",".join(sorted(access.admins)) or "none",
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
