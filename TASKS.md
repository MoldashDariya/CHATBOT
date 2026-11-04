# TASKS: Telegram bot for admissions summaries

Legend: `[ ]` not started · `[~]` in progress · `[x]` done. We work top to bottom; each phase has a completion criterion.

## Phase 0. Setup
- [ ] 0.1 Create the bot via @BotFather and get a token (done by the user)
- [ ] 0.2 Get an Anthropic API key (done by the user)
- [x] 0.3 Initialise the project: `pyproject.toml`, the `bot/`, `tests/`, `prompts/` structure
- [x] 0.4 `.gitignore`, `.env.example`, `README.md` with run instructions
- [x] 0.5 Set up ruff, mypy, pytest, pre-commit
- [x] 0.6 `config.py` (pydantic-settings) + logging
- [x] 0.7 `git init`, first commit

**Done when:** `uv sync && uv run pytest` passes on the empty skeleton.

## Phase 1. MVP: link → summary
### 1A. Bot skeleton
- [x] 1.1 `main.py`: aiogram startup, graceful shutdown
- [x] 1.2 `/start`, `/help` (what the bot can do, an example link, limitations)
- [x] 1.3 Message handler: find a URL in the text (several links → process the first and warn)
- [x] 1.4 "Reading…" status message, edited stage by stage

### 1B. Security and downloading
- [x] 1.5 `url_guard.py`: scheme validation, DNS resolution, private network blocking, checks on redirects
- [x] 1.6 SSRF tests: `localhost`, `127.0.0.1`, `169.254.169.254`, `[::1]`, DNS rebinding, redirect to an internal IP
- [x] 1.7 HTTP client: timeout, size limit (streaming with cut-off), redirect limit, User-Agent
- [x] 1.8 `limits.py`: per-user rate limit

### 1C. Type detection
- [x] 1.9 `detector.py`: YouTube / PDF / DOCX / HTML by URL + Content-Type + magic bytes
- [x] 1.10 Detector tests (including a PDF without an extension and a redirect to a file)

### 1D. Extraction
- [x] 1.11 `extractors/html.py` (httpx + trafilatura, metadata: title, date, canonical URL)
- [x] 1.12 `extractors/pdf.py` (text + tables, page limit, error on scans)
- [x] 1.13 `extractors/docx.py` (paragraphs + tables)
- [x] 1.14 `extractors/youtube.py` (video_id, RU/EN/KZ transcript, title and description)
- [x] 1.15 A single `ExtractedContent`; fixture-based tests for every extractor

### 1E. Cleaning
- [x] 1.16 `cleaner.py`: whitespace normalisation, block deduplication, noise removal, `MAX_CHARS` limit
- [x] 1.17 "Too little text" check → a clear error instead of an LLM call

### 1F. Summary
- [x] 1.18 `schema.py`: pydantic models `Summary`, `Deadline`, `CostItem`, `Link`
- [x] 1.19 Prompts: `system.md` + `extract_scholarship.md`, `extract_university.md`, `extract_article.md`, `extract_generic.md`
  - decision: a single `prompts/system.md` (rules, classes, language) instead of separate `extract_*.md` files:
    classification and extraction happen in one call, with per-class hints inside the shared prompt
- [x] 1.20 LLM classifier for the HTML page class (a cheap call, or combined with extraction)
  - decision: the classifier is combined with extraction (`source_type` in the model's answer); there is no separate call
- [x] 1.21 `summarizer.py`: Claude call, structured output, validation, one retry
  - structured output via `output_config.format`; no `temperature`/`thinking` (newer models reject them)
- [x] ~~1.22 Chunking long documents + a merge step~~ **cancelled, not needed for now**
  - `MAX_CHARS` (120,000 characters, ~30-50k tokens) is an order of magnitude below the model's window, so one pass sees
    the whole document. Revisit if `MAX_CHARS` is raised above ~300k characters or a model with a small window is used
- [x] 1.23 Post-checks: deadline quotes are present in the text; links come from the source; a warning about past dates
  - a link is allowed if it is among `links` OR among the URLs in the text (`find_urls`): the extractor
    caps `links` at a hundred, and cleaning may free up room; compare whole URLs
  - deadline quotes are compared with the text after collapsing whitespace and ignoring case
    (the cleaner has already normalised the text)
  - warnings from `meta`: `truncated` (text was cut), `pages_read < pages_total` (PDF)
- [x] 1.24 Prompt injection protection in the system prompt + a test with a "malicious" page
  - live test against the real API (`pytest -m live`): a page with two injections was resisted; the model described
    them in `warnings` and did not include the attacker's link; the code also strips URLs from `warnings`

### 1G. Output
- [x] 1.25 `render/telegram.py`: HTML card, escaping, emoji sections, empty fields → "not stated"
  - mark deadlines with `verified=False` ("not confirmed by a quote from the source"); `possibly_passed` → "may have passed"
  - localised notes from `Summary` fields: `dropped_links`, `truncated`, `pages_read < pages_total`
  - show the domain of every link: the link filter does not protect against URLs planted by the page itself
  - `costs[].amount_text` and `date_text` come in the source language (by design), everything else in `language`
- [x] 1.26 Splitting into messages ≤4096 without breaking tags or blocks
- [x] 1.27 Mandatory "🔎 Source: URL" line (test: always present)
- [x] 1.28 A single error handler with human-readable messages
  - messages by `reason`: `InvalidURLError`/`BlockedURLError` (one generic "link unavailable", no SSRF details),
    `FetchHTTPError` (401/403 → login/paywall/bot protection), `FetchTimeoutError`, `ContentTooLargeError`,
    `UnsupportedFormatError` (`legacy_office` → send a PDF/DOCX), `ExtractionError.reason`, `TooLittleTextError`
    (by format), `LLMError.reason` (`llm_config` shown to the user as "the service is temporarily unavailable", logged for the operator)

### 1H. Building and checking the MVP
- [x] 1.29 `orchestrator.py`: detect → fetch → extract → clean → summarize → render
- [x] 1.30 Pipeline integration test with a mocked LLM
- [ ] 1.31 Manual check in Telegram on 8–10 real links (set below)
  - done by the user: run the bot, send the links from the set below and check how the card looks in Telegram itself
- [~] 1.32 Golden set + evaluation script: share of correct deadlines, no invented data
  - done: `bot/evaluation.py` and 8 stored cases, all passing on the real model (100% recall, 0 invented)
  - live case `live-mygermanuniversity-msc-is` (a page sent by the user) added, run with `--live`
  - live cases `live-rwth-...` and `live-saarland-data-science-early-late` (pages from the user's cards); the model's answer on a live page varies between runs: in 1 of 6 Saarland runs there was 1 unconfirmed deadline (it would get ❓ in the card)
  - remaining: 4-7 more real links that matter to the user, with checked expectations

**Test link set (to collect):** a university admissions page; a scholarship page
(e.g. Bolashak / Chevening / DAAD); an admissions news item; a PDF admissions regulation;
a DOCX instruction; a YouTube video about admissions with subtitles; a JS-rendered page;
a page behind a paywall/login (a proper error is expected).

**Done when:** all 6 source types produce a card; every card contains the source;
no deadline without a confirming quote; errors are clear; SSRF tests are green.

## Phase 2. Quality and usability
- [x] 2.1 SQLite cache (URL normalisation, TTL) + "🔄 Обновить" (Refresh) button
  - done: `storage/cache.py`, `cache_key` in `bot/urls.py`, cache in the orchestrator, `handlers/refresh.py`, a "🕒 Showing a saved
    answer" line in the card; `CACHE_DB_PATH` setting; 37 mutations caught by tests; on the real stack: 26 s, repeat 0.00 s with no model call
  - manual check in Telegram (repeat a link, press Refresh) is done by the user together with 1.31
  - deliberately not done: a shared "in-flight" for identical simultaneous requests; caching files from the chat
- [x] 2.2 Accepting PDF/DOCX files directly in the chat (`document.py`), Telegram size limit
  - manual check in Telegram (send a PDF and a DOCX to the bot) is done by the user together with 1.31
- [ ] 2.3 Playwright fallback for JS pages (optional dependency, separate container/extra)
- [ ] 2.4 Choosing the answer language with `/lang` (RU/EN/KZ), stored in the DB
- [ ] 2.5 Buttons under the answer: "Full summary", "Deadlines only", "Copy as text"
- [ ] 2.6 Follow-up questions about the last link ("which documents do I need?"), context in the DB
- [x] 2.7 Following neighbouring pages on the same site (e.g. "Requirements", "Costs") via links from the main page, depth limit 1
  - built from a real user card (RWTH Data Science): the deadlines were on the application page; now the bot reads it itself,
    only if the page has no confirmed deadlines; live case `live-rwth-data-science-follows-apply-page`
- [ ] 2.8 Metrics/logs: stage timings, tokens, error rate, cost per request
- [ ] 2.9 Budget limits: daily token limit per user

## Phase 3. Deployment and operations
- [ ] 3.1 `Dockerfile` (+ a separate playwright image), `docker-compose.yml`
- [ ] 3.2 Deploy to a VPS/Fly.io/Railway; environment variables; long polling → webhook (optional)
- [ ] 3.3 CI: ruff + mypy + pytest on every push
- [ ] 3.4 Alerts when the bot goes down, log rotation, SQLite backups
- [ ] 3.5 Privacy policy in `/help` (what is stored and for how long)

## Phase 4. Ideas after the MVP
- [ ] 4.1 Deadline reminders ("remind me 7 days and 1 day before"), an `.ics` calendar
- [ ] 4.2 Comparing 2–3 programs/scholarships in one table
- [ ] 4.3 OCR for scanned PDFs
- [ ] 4.4 A list of the user's saved links (`/my`)
- [ ] 4.5 Export to Google Sheets / CSV

## Open questions
- [ ] Main interface language: RU only, or RU/EN/KZ from the start?
- [ ] LLM budget and expected number of users (affects limits and caching)
- [ ] Hosting: own server or cloud?
- [ ] Is OCR for scanned PDFs needed in the first version?
