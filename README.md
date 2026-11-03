# Admissions Link Summarizer Bot

Telegram-бот: присылаешь ссылку (сайт, статья, страница университета или стипендии, PDF, DOCX,
YouTube), а бот достаёт основной текст и возвращает структурированное саммари для поступления:
дедлайны, основные пункты, кто может подаваться, стоимость, требования, полезные ссылки и
обязательно ссылку на источник.

Правила проекта и архитектура: [ARCHITECTURE.md](ARCHITECTURE.md). План работ: [TASKS.md](TASKS.md).

## About: AI-Powered University Admission Assistant

Developed an AI-powered chatbot designed to simplify the university admission process by helping
prospective students organize and understand essential information about master's degree programs
worldwide.

The chatbot provides detailed information about universities and academic programs, including
admission requirements, application deadlines, language proficiency requirements, required
documents, tuition fees, scholarship opportunities, and application procedures. Instead of manually
reading through long university websites, students send a link to a program page (or a PDF/DOCX
brochure) and receive a structured, easy-to-understand summary.

For example, given the page of the M.Sc. in Data Science and Artificial Intelligence at Saarland
University, the chatbot can provide a comprehensive overview of the program, including its
curriculum, eligibility criteria, English language requirements, application deadlines for
different semesters, tuition policies, and links to official university resources. It also
highlights important restrictions, such as whether a university accepts a Medium of Instruction
certificate instead of an English proficiency test.

The chatbot presents information in clearly organized sections, allowing students to quickly
identify relevant details, compare admission requirements, and plan their applications more
efficiently. Every deadline is backed by a verbatim quote from the page, and every answer links to
the official source so users can verify the information.

The project was motivated by my own experience of applying to international master's programs and
the challenges of finding accurate admission information across different university websites. The
chatbot was actively used by me and my friends during our application process to research
universities, check eligibility requirements, identify important deadlines, and explore
educational opportunities.

The main goal of the project is to make information about international higher education more
accessible, organized, and understandable, particularly for students who are unfamiliar with
complex university admission procedures.

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
