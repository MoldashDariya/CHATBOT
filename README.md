# Admissions Link Summarizer Bot

Telegram-бот: присылаешь ссылку (сайт, статья, страница университета или стипендии, PDF, DOCX,
YouTube), а бот достаёт основной текст и возвращает структурированное саммари для поступления:
дедлайны, основные пункты, кто может подаваться, стоимость, требования, полезные ссылки и
обязательно ссылку на источник.

Правила проекта и архитектура: [ARCHITECTURE.md](ARCHITECTURE.md). План работ: [TASKS.md](TASKS.md).

## Быстрый старт

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # заполни TELEGRAM_BOT_TOKEN и ANTHROPIC_API_KEY
uv run python -m bot.main
```

Токен бота выдаёт @BotFather в Telegram, ключ API — консоль Anthropic. Файл `.env` в git не попадает.

## Кэш ответов

Повторная ссылка отдаётся из локального SQLite-файла (`CACHE_DB_PATH`, по умолчанию
`data/cache.sqlite3`, в git не попадает) без вызова модели; под карточкой кнопка «🔄 Обновить»
читает страницу заново. Время жизни: `CACHE_TTL_HOURS` (по умолчанию 24, `0` выключает кэш).
Хранятся только ответы о публичных страницах, без данных пользователей. Файл можно удалять в любой
момент: бот пересоздаст его. Подробности: раздел «Кэширование» в [ARCHITECTURE.md](ARCHITECTURE.md).

## Разработка

```bash
uv run pytest                                   # тесты
uv run ruff check . && uv run ruff format --check .
uv run mypy bot
uv run pre-commit install                       # хуки перед коммитом
uv run pytest -m live -s                        # тесты на настоящем API (тратят немного токенов)
uv run python -m bot.evaluation                  # качество на эталонном наборе (тратит токены)
```

Дополнительные зависимости (по необходимости):

```bash
uv sync --extra browser            # Playwright для JS-страниц
uv sync --extra youtube-fallback   # yt-dlp как запасной вариант для субтитров
```
