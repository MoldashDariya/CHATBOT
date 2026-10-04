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

## Разработка

```bash
uv run pytest                                   # тесты
uv run ruff check . && uv run ruff format --check .
uv run mypy bot
uv run pre-commit install                       # хуки перед коммитом
uv run pytest -m live -s                        # тесты на настоящем API (тратят немного токенов)
```

Дополнительные зависимости (по необходимости):

```bash
uv sync --extra browser            # Playwright для JS-страниц
uv sync --extra youtube-fallback   # yt-dlp как запасной вариант для субтитров
```

> `python -m bot.main` появится в задаче 1.1; пока в проекте только каркас.
