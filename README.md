# Book Shelter Bot

Telegram-бот на [aiogram](https://docs.aiogram.dev/) ищет книги по индексу MyHomeLib (`.inpx`) и присылает FB2 из zip-архивов Flibusta.

## Что нужно

- Docker и Docker Compose
- файл `.env` с токеном бота
- индекс `flibusta_fb2_local.inpx` и архивы книг в каталоге `books/`

## Настройка

1. Скопируйте пример окружения и заполните секреты:

```bash
cp .env.example .env
```

2. В `.env` укажите:

| Переменная | Назначение |
|---|---|
| `TELEGRAM_BOT_API` | токен бота от [@BotFather](https://t.me/BotFather) |
| `VLESS_URL` | ссылка VLESS для доступа к Telegram. **Оставьте пустой**, если VPN не нужен |
| `ADMIN_USERNAME` | Telegram-username первого админа, например `@Fliutch` |
| `ADMIN2_USERNAME` | второй админ, например `@benifactor` |

Можно добавить ещё админов переменными вида `ADMIN3_USERNAME`, `ADMIN4_USERNAME` и т.д.

3. Положите в `books/` индекс и нужные zip-архивы, например:

```
books/flibusta_fb2_local.inpx
books/d.fb2-009373-367300.zip
```

4. Скопируйте книги в Docker volume (бот читает библиотеку из тома, не из папки напрямую):

```bash
./scripts/sync-books.sh
```

Неполные загрузки `*.part` скрипт пропускает.

## Запуск

```bash
docker compose up --build -d
```

Логи:

```bash
docker compose logs -f bot
```

Остановка:

```bash
docker compose down
```

Индекс при старте разбирается в SQLite (`catalog-data`). После смены `.inpx` перезапустите бота:

```bash
docker compose restart bot
```

После добавления новых zip в `books/` снова выполните `./scripts/sync-books.sh` и перезапустите бота.

## VPN

Если `VLESS_URL` заполнен, контейнер `proxy` поднимает Xray и бот ходит в Telegram через SOCKS5.

Если `VLESS_URL` пустой (`VLESS_URL=`), Xray не запускается, бот подключается к Telegram напрямую.

## Доступ к боту

Пока пользователя нет в списке, на любое сообщение бот отвечает: `извините бот не работает`.

Админы из `.env` имеют доступ сразу. Они могут выдавать доступ другим:

- `/add @username` — добавить пользователя
- `/del @username` — убрать пользователя
- `/users` — список админов и допущенных

Username в Telegram обязателен: без него пользователя нельзя добавить и он не получит доступ.

## Поиск книг

Напишите название, автора или серию. В выдаче:

- 📗 — архив есть в `books/`, файл можно получить
- 📦 — книга есть в индексе, но zip ещё не скачан
