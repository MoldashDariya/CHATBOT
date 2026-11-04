# Admissions Link Summarizer Bot

An AI-powered Telegram bot. The user sends a link or a file. The bot detects the source type,
extracts the main text, cleans it of noise and returns a structured summary for applicants:
deadlines, requirements, who can apply, costs, useful links and, always, a link to the source.

## Project working rules
- Read `TASKS.md` before changing code. Work on one task at a time.
- When a task is finished, mark it `[x]` in `TASKS.md`. Do not mark it until the tests pass.
- Do not add dependencies that are not described in this file without agreeing on it first.
- Never invent data in a summary (see "Anti-hallucination"). This is the main quality criterion.
- Secrets live only in `.env` and are never committed. Keep `.env.example` up to date.
- Replies and user-facing texts are written in the user's language.
  Code, names, logs and code comments are written in English.

## User flow
1. The user sends a URL (or a PDF/DOCX file).
2. The bot replies "Reading the page…" (a status message that is edited later).
3. The bot detects the source type (see below).
4. It extracts the text and cleans it.
5. The LLM extracts structured fields.
6. The bot sends a summary card plus a link to the source.

## Linked pages (depth 1)
`pipeline/followups.py` + `Orchestrator.process(follow=True)`; enabled by `FOLLOW_LINKED_PAGES`
(2 by default, `0` disables it). A program page often says "see the application page for deadlines
and requirements"; without this feature the bot honestly answered "no deadlines stated" even though
the link to the right page was there.
- **When**: only for HTML from a user's link (not for files, videos, PDFs or stored evaluation
  inputs), and only if the first answer has no deadline confirmed by the text and the page is not
  `other`. If deadlines were already found, no extra requests are made.
- **Which pages**: only on the same site (`registrable_domain`: `sc.informatik.rwth-aachen.de` and
  `www.rwth-aachen.de` count as one site), links with "admission words" in the link text (weight 2)
  or in the address (apply, application, admission, deadline, requirements, bewerb…, поступл…,
  подач…, заявк…, срок…; matched at the **start of a word**, otherwise "up**dates**" and
  "can**dates**" would match), best first, at most `FOLLOW_LINKED_PAGES`, no images/archives, not the
  page itself. We never go deeper than 1: links on linked pages are not read.
- **Security**: the same `SafeFetcher`, SSRF protection, limits and overall timeout; a host name that
  points inside the network is never requested. Linked pages are untrusted data just like the
  original page.
- **How**: the texts are joined (`combine`): the user's page, then each linked page under a
  `=== Linked page: URL ===` line (the system prompt explains how to treat them), and the model is
  called a second time. The new answer is used only if it has **more** confirmed deadlines;
  otherwise the first answer stays. Any failure (page unavailable/empty, second call failed) does
  not spoil the answer: linked pages are a bonus, not a reason for an error.
- **Card**: an "📎 Also read" line with links to these pages comes before the source
  (`Summary.extra_sources`); "Source" is still the user's link and the last line. Status message:
  "🔎 Checking linked pages…".
- Cost: up to two requests and a second model call, only on pages where no deadlines were found.

## Files sent in the chat (PDF, DOCX)
`handlers/document.py` + `Orchestrator.run_file`. The same path as a link, minus the download: the
same hourly request limit, the same overall timeout and queue; the format is decided by the
**bytes**, not by the file name or the declared mime type (an HTML file named `.pdf` is read as
HTML; `.doc` is rejected by name before reading, other non-PDF/DOCX/HTML files by content).
- Size: if Telegram reports more than `MAX_DOWNLOAD_MB`, the file is not even downloaded; if the size
  is not reported or turns out larger after the download, the orchestrator checks the actual size.
  Telegram only lets bots download files up to 20 MB.
- Download: 60 s timeout; "file is too big" → a message about the size, other Telegram failures →
  a neutral "could not download the file" (`DownloadFailedError`).
- **The file name is chosen by the sender, so it never goes into the prompt.** The model and the
  extractors see a fixed address `telegram://uploaded-file`; the card's "Source" line shows the
  sanitised name (printable characters only, one line, up to 120 characters), inserted as `<code>`
  and escaped.
- Photos, voice messages, videos, stickers: a hint about what the bot can read.

## Source types and detection
Detection order (cheap → expensive):
1. **YouTube**: domains `youtube.com`, `youtu.be`, `m.youtube.com`, `/shorts/`. We use the subtitles.
2. **PDF**: `.pdf` extension or `Content-Type: application/pdf` (check HEAD / first bytes `%PDF`).
3. **DOCX**: `.docx` extension, `wordprocessingml` Content-Type, or a file sent in Telegram.
   `.doc` (the old format) is not supported: politely decline and ask for PDF/DOCX.
4. **HTML**: everything else. Then an LLM classifier on the cleaned text:
   `university_page | scholarship | news_article | publication | general_site | other`.

Two different concepts, not to be confused: the **format** (`SourceFormat`: youtube/pdf/docx/html,
decided by `pipeline/detector.py`, needed by the extractors) and the **class**
(`Summary.source_type`: university_page, scholarship... decided later from the text of the HTML page).

Detector rules:
- `detect_from_url`: only what needs no download: YouTube videos (an 11-character id is required;
  channels/playlists/search are rejected) and the old `.doc` (rejected before download).
- The result is decided by the **response bytes** (`detect_from_content`), not by the URL extension
  or the `Content-Type`: `%PDF-` at the start (junk in the first KiB is allowed only if the server
  itself said `application/pdf`), a zip with `word/document.xml` = DOCX, an OLE signature = old
  Office (rejected), otherwise HTML by Content-Type or by markup at the start. Everything else
  (images, JSON, xlsx, archives) = not supported.
- A link to a `.pdf` that returned HTML (an error/login page) is processed as HTML.
- After the download, look at the **final** URL (after redirects): `detect_response`.

The source class affects the extraction prompt (for a scholarship the focus is eligibility/amount,
for a university it is programs and costs, for news it is the publication date and the original
source).

## Architecture
```
bot/
  main.py                # aiogram startup, router registration
  config.py              # pydantic-settings, reads .env (the token format is validated)
  log.py                 # logging setup
  errors.py              # domain exceptions (BotError and subclasses)
  messages.py            # user-facing texts (RU)
  urls.py                # finding URLs in message text
  handlers/
    start.py             # /start, /help, /lang
    link.py              # handling URLs in text, rate limit
    status.py            # status message edited stage by stage
    flow.py              # shared by links and files: limit, progress, card or error
    document.py          # handling uploaded PDF/DOCX
    refresh.py           # the "🔄 Обновить" (Refresh) button under a card
  pipeline/
    orchestrator.py      # detect → fetch → extract → clean → summarize; stages, concurrency limit, overall timeout
    detector.py          # source type detection
    cleaner.py           # text cleaning, deduplication, trimming to the token budget
    followups.py         # which linked pages to read (same site, about applying) and how to join the texts
    summarizer.py        # Claude call, structured output, retry, building the Summary
    schema.py            # LLMSummary (model answer) and Summary (after checks)
    verify.py            # checking quotes/dates/links against the text, stripping URLs from warnings
  extractors/
    base.py              # Extractor interface -> ExtractedContent
    html.py              # trafilatura + link preparation; fallback: main_area, then playwright (2.3)
    main_area.py         # text of the page's main area straight from the DOM (for link-heavy pages)
    pdf.py               # pdfplumber/pypdf
    docx.py              # python-docx
    youtube.py           # youtube-transcript-api / yt-dlp
  render/
    telegram.py          # card → HTML for Telegram, splitting >4096 characters
    texts.py             # card and error texts: ru, en, kk
    errors.py            # exception → user message + log level
  security/
    url_guard.py         # SSRF protection, URL validation
    safe_fetch.py        # HTTP GET: guard on every redirect, size limit, overall timeout
    limits.py            # per-user rate limit (sliding window, in memory)
  storage/
    cache.py             # SQLite: finished answers by normalised URL, language and content hash
  prompts/
    system.md            # single system prompt (rules, source classes, language)
evaluation.py            # quality evaluation on the golden set: python -m bot.evaluation
evals/
  golden.json            # reference cases: input + expected facts
  fixtures/              # stored inputs (PDF, DOCX, HTML)
tests/
  fixtures/              # stored HTML/PDF/DOCX and transcripts
```

### Key contract
`async Extractor.extract(RawSource(url, body, content_type)) -> ExtractedContent(text, title,
source_url, source_format, links, meta)` (`extractors/base.py`). All extractors return the same
format; the orchestrator gets them from `build_extractors(settings, fetcher)` by `SourceFormat`.
- `RawSource.url` is the **final** URL after redirects (the base for relative links); for YouTube
  `body` is empty and the extractor fetches the data itself. Parsing runs in `asyncio.to_thread`.
- `links` are absolute http(s) links **actually present** in the source (only from the main content,
  not from menus/footers). The summary may only suggest these (contract test: every link appears in
  `text`).
- `meta`: non-empty strings only: `canonical_url` (if it differs from the downloaded one),
  `published`, `site_name`, `author`, `description` (HTML), `pages_total`/`pages_read` (PDF),
  `kind=video`, `channel`, `transcript_language`, `transcript_kind` (YouTube). If
  `pages_read < pages_total`, the orchestrator must add a warning about partial reading.
- Exceptions: `ExtractionError(reason)`, where reason is one of `scanned_pdf`, `encrypted_pdf`,
  `corrupt`, `unsafe_archive`, `timeout`, `no_transcript`, `video_unavailable`, `youtube_blocked`,
  `youtube_failed`. The user-facing error text is built from the reason (task 1.28).

Extractor details:
- **HTML**: the DOM is prepared before trafilatura: all links are made absolute, taking `<base>` and
  the page address into account (trafilatura itself resolves from the site root, turns `//cdn` into
  http and so on), in-page links (`#frag`, footnotes) are unwrapped into text, bare `&` in the markup
  are escaped, and after extraction URLs with parameters like `&section=`/`&region=` are restored
  (otherwise `html.unescape` in trafilatura turns them into `§ion=`/`®ion=`).
  trafilatura treats blocks made almost entirely of links as navigation and drops them, but on pages
  such as a funding guide or an admissions hub that *is* the content (on GOV.UK 58 of 60 list items
  were lost). So if the page declares a main area (`<main>`, `role=main`, `<article>`),
  `extractors/main_area.py` reads it straight from the DOM (order, headings, list items, tables,
  links; no nav/header/footer/aside/forms/hidden elements/cookie widgets), and that text is used
  when it is 1.5 times longer than trafilatura's (`meta.text_source=main_area`). Ordinary articles
  stay on trafilatura.
  Adjacent elements with no whitespace between them in the markup (`<strong>15 July</strong><span
  class="badge">in 9 months</span>`: the browser separates them with styles, but the extractor read
  "15 Julyin 9 months") are separated by a space (`separate_glued_elements`): only at an
  element-element or text-`span` boundary with letters/digits on both sides, never `Sys<b>tem</b>`,
  `<pre>`, `<code>`, superscripts and subscripts (`15<sup>th</sup>`, `H<sub>2</sub>O`, including when
  wrapped as `<b><sup>st</sup></b>`) or the ordinal suffixes `st/nd/rd/th` after a digit: otherwise
  "March 1st" would become "March 1 st" and the deadline would stop matching the source.
  Tables as `| a | b |`, links as `[text](url)`; if the result is <500 characters, a second pass with
  `favor_recall`. A short result is NOT an error here: the "too little text" decision is made by the
  cleaner (1.17). JS-rendered pages (DAAD and Stanford gave 300-700 characters) will be handled by
  Playwright (2.3).
- **PDF**: pdfplumber; ruled tables separately from the text; headers, footers and page numbers are
  removed at page edges (the first occurrence of a repeated line is kept; a page counter is
  normalised only at the edge of a line, numbered headings are left alone). The first
  `MAX_PDF_PAGES` pages are read, within a 30 s time budget. A parser running in a thread cannot be
  interrupted mid-page: the page and download size limits protect against hangs.
- **DOCX**: python-docx; headings `#` (Title) / `##`.. (Heading 1..), lists `-`, tables, hyperlink
  URLs appended to the text as `(url)`. Before parsing, the archive's declared sizes are checked
  (document.xml ≤20 MB, total ≤100 MB, ≤5000 files) against zip bombs.
- **YouTube**: subtitles via youtube-transcript-api (in a thread, with timeouts through
  `requests.Session`); preference order: manual in the preferred language, automatic in it, any
  manual, any automatic (ru, kk, en; `de-DE` counts as `de`); `[Music]`/`[♪♪♪]` markers are removed.
  The title/channel (oEmbed) and the description from the video page go through `SafeFetcher` and
  never break extraction. The library itself connects directly, but only to youtube.com and with a
  validated 11-character id.

### Result schema (`pipeline/schema.py`)
Two layers. `LLMSummary` is exactly what we ask the model to return (its JSON schema is sent with
the request): `source_type` (class: university_page | scholarship | news_article | publication |
general_site | other), `title`, `organization`, `overview`,
`deadlines[{label, date_iso|null, date_text, quote}]`, `key_points[]`, `eligibility[]`,
`costs[{item, amount_text}]`, `requirements[]`, `useful_links[{title, url}]`, `warnings[]`,
`confidence` (high|medium|low). There is no `source_url` and no check results here: those are not
the model's business.

`Summary` is what the bot works with: the model's answer after the code's checks, plus `source_url`
(set by the code), `source_format`, `language`, `verified` and `possibly_passed` on each deadline
(`date_iso` is already a `date`), and the facts for localised notes in the card: `dropped_links`,
`truncated`, `pages_read`/`pages_total` (`partial_coverage`).
Empty fields are shown as "not stated in the source" rather than silently skipped.

## Stack and dependencies
Python 3.12; aiogram 3; httpx; trafilatura; playwright (optional, extra);
pdfplumber; pypdf; python-docx; youtube-transcript-api; yt-dlp (fallback);
anthropic; pydantic v2 + pydantic-settings; aiosqlite; pytest, pytest-asyncio, respx; ruff; mypy.
Package manager: `uv` (or pip + `requirements.txt` if uv is not available).

## Working with the LLM
Implementation: `pipeline/summarizer.py`, prompt `prompts/system.md`, checks `pipeline/verify.py`.
- Calls go through the `anthropic` SDK (`AsyncAnthropic`); the model comes from `LLM_MODEL` and is
  never hardcoded. The API reference recommends `claude-opus-5-5` for new projects;
  `claude-sonnet-5-5` is cheaper with the same interface. Changing the model only requires editing
  `.env`.
- **Structured output via `output_config.format` (a JSON schema from `LLMSummary`)**, not forced
  tool use: on newer models `tool_choice: any/tool` returns 400. The answer is validated with
  pydantic; on error there is one retry with the error text (field paths and messages only, no
  values, so the page text is not echoed back).
- **Do not pass `temperature`/`top_p`/`top_k` or `thinking`**: newer models return 400 for sampling
  parameters, and thinking is on by default. Depth is set by `LLM_EFFORT` (`medium` by default;
  empty/None removes the parameter for models that do not accept it, e.g. Haiku 4.5).
- **One call, no chunking.** `MAX_CHARS` (120,000 characters, ~30-50k tokens) is an order of
  magnitude below the model's context window (1M; 200K for Haiku), and a single pass sees the whole
  document: "chunks + merge" would only add ways to lose a deadline. Revisit chunking if `MAX_CHARS`
  is raised above ~300k characters or a model with a small window is used.
- **Classification is combined with extraction**: the model picks `source_type` in the same answer,
  and the per-class hints live in the shared system prompt (not 4 files and not 2 calls).
- The source is data: the page goes into the **user** message inside `<source_document>`,
  `<source_document>`/`<task>` tags inside the text are neutralised, and the URL from a redirect is
  escaped. The system prompt forbids following instructions in the page text.
- `max_tokens` is 8000; the `refusal` and `max_tokens` stop reasons are not retried and produce an
  `LLMError`.
- API errors are mapped to `LLMError(reason)`: `llm_busy`, `llm_unreachable`, `llm_refused`,
  `llm_truncated`, `llm_invalid`, `llm_too_large`, `llm_config` (key/model: an operator error,
  logged at ERROR), `llm_failed`. The SDK itself already retries 429/5xx (twice).
- Model refusals are unlikely for public admissions pages and are treated as errors. The
  server-side `fallbacks` option (an API option for some models) is not enabled; add it if refusals
  start to appear.
- One `Summarizer` serves all users: no mutable state on `self`.
- Log: model, tokens, time, source class. Do not log page text or summaries.

## Anti-hallucination (mandatory)
- Extract ONLY what is in the text. No data → `null`/"not stated".
- Every deadline comes with a verbatim `quote`. The code (`verify.py`) checks the quote and
  `date_text` against the cleaned text the model saw (compared after stripping table/link markup,
  case-insensitive, with a single kind of quotes and dashes; `...` is allowed between pieces in
  order; the quote must be at least 8 characters). Not confirmed → `verified=False`, flagged in the
  card.
- `date_iso` is kept only if the year appears in `date_text` or the quote. A guessed year is erased
  (otherwise "the deadline may have passed" would depend on it).
- `possibly_passed`: `date_iso` is earlier than today.
- `useful_links` are filtered: only URLs that appear in the text or the source's `links`, compared
  as whole URLs (`https://x.edu` is not equal to `https://x.edu/apply`); non-http(s) links are
  dropped; at most 8; the number dropped goes into `dropped_links`.
  This protects against invented links, **not** against links planted by the page itself: a
  malicious URL in the page text will pass the filter. That is handled by the prompt and by always
  showing the link's domain in the card (1.25).
- All URLs are stripped from `warnings` (Telegram makes them clickable, and a model describing an
  injection attempt may quote the attacker's address): links belong only in `useful_links`.
- `source_url` is set by the code and never generated by the model.
- Confidence is calibrated by the code: partial reading (truncation, `pages_read < pages_total`) is
  capped at `medium`; if no deadline is confirmed by the text, it is `low`.
- If the extracted text is shorter than the threshold (<300 characters), the LLM is not called
  (`TooLittleTextError`).

## Content cleaning
Two layers. The extractor removes noise at the markup level (trafilatura: menus, cookie banners,
ads, footers; tables as `| a | b |`; PDF: headers/footers/page numbers; YouTube: `[Music]`). Then
`pipeline/cleaner.py` cleans the text regardless of format:
- `clean_content(content, max_chars)` returns a copy; `ensure_enough_text(content)` raises
  `TooLittleTextError(source_format, chars, minimum)` at `<300` characters, and the LLM is not called.
  The hint depends on the format: HTML — login/paywall/page built by JavaScript (Playwright 2.3 will
  cover some cases), PDF/DOCX — an almost empty file, YouTube — subtitles too short.
- For all formats: invisible characters (ZWSP, soft hyphen, BOM, bidi) and control characters are
  removed, NFC is applied, unusual spaces and tabs are collapsed, at most one blank line in a row,
  consecutive identical lines (≥10 characters, ignoring blank lines between them) are collapsed,
  and the text is trimmed to `MAX_CHARS` at a line boundary with a marker at the end and
  `meta.truncated`/`original_chars`.
- HTML only: indented lines following a line of prose are joined to it (this is how layouts wrap
  paragraphs; non-indented lines are never joined "by guess"); page noise (`Skip to content`,
  `Share`, `Read more`, cookie and copyright lines, `[edit]`, lines consisting of a single `[`);
  footnotes `[1]` glued to a word; repeats of long (≥200 characters) lines.
  None of this is done for PDF/DOCX: "Menu" can be real content there, and a repeated contract
  clause can be legitimate.
- **Homoglyphs** (`fold_look_alikes`): some sites mix Cyrillic look-alikes of Latin letters into
  words (`Sуstеms`, `signaturе`) or the other way round. A mixed-script word is repaired if one
  script dominates it (on a tie, the script of the whole text decides) and every "foreign" letter
  has a visual twin; otherwise the word is left alone (`PDFфайл`, `Mиxed`). Normal Russian, Kazakh
  or German text is not changed. The title is repaired too; the counter is
  `meta.lookalike_words_repaired`. This runs before the noise rules, otherwise "Skір to content"
  would not be recognised.
- Not done: de-hyphenation (`при-\nём`), joining lines by the "next line starts lowercase" heuristic
  (it breaks table rows with deadlines), removing non-consecutive repeated short lines.
- After cleaning, `links` is narrowed to URLs still present in the text (compared as whole URLs:
  `https://x.edu` is not considered present inside `https://x.edu/apply`).
- Keep tables as text (cells separated by ` | `): deadlines are often in tables.
- If trafilatura returns too little, fall back to Playwright (JS rendering), then retry (2.3).
- YouTube: transcript + title + description; mark that the source is a video, not a document.

## Telegram answer format
`render/telegram.py`: `render_summary(Summary) -> list[str]` (HTML parse mode, one or more
messages). Telegram has no tables, so the "table" is a card of emoji sections, always in the same
order: header (title, organisation · type), overview, ⏰ Deadlines, ✅ Key points, 👤 Who can apply,
💰 Costs / funding, 📋 Requirements, 🔗 Useful links, ⚠️ Please note (if any),
🟢/🟡/🔴 Reliability of this summary, 🔎 Source.
- The card language is `summary.language` (ru/en/kk); texts are in `render/texts.py` (all
  languages have the same keys and placeholders, a test checks this; the Kazakh texts should be
  reviewed by a native speaker). `date_text`, `amount_text` and quotes stay in the source language,
  by design.
- An empty section does not disappear; it shows "not stated in the source".
- Deadline: label, the date as written, and a short quote below. `❓` not confirmed by a quote,
  `⏳` may have passed; explanations go in the "Please note" block. That block lists, by
  importance: unverified dates, past dates, truncation/partial reading, hidden links, then the
  model's warnings.
- Link: `<a href>title</a> — <code>domain</code>` (the domain is always shown: the link filter does
  not protect against URLs planted by the page itself; it is in `<code>` because Telegram turns
  plain text into a second link). Only http(s) in `href`; duplicates of one page under
  `http`/`https`/`www` are merged, keeping https.
- **The source is always the last line of the last message**, even for an empty card; the link
  points to the exact URL, and the visible text of a long URL is shortened.
- Markup: only `<b> <i> <a href> <code>`; all strings from the model and the page are trimmed and
  escaped with exactly Telegram's four entities (`& < > "`), and tags are never left open across
  lines, so splitting text between lines is safe.
- Length is counted the way Telegram does: without tags and `href` addresses, emoji count as two
  (UTF-16), threshold 4000 against the 4096 limit. A block is moved to the next message whole, and
  a block that is too long is split between lines.
- Buttons under the answer: "🔄 Обновить" (Refresh) (2.1, done); "📄 Full summary",
  "🌐 Change language" (Phase 2).

## Errors and UX
`render/errors.py`: `describe_error(exc, language)` (a pure function) and `handle_error(...)` (which
also logs). The user never sees exception names, tracebacks, server statuses (other than a 4xx/5xx
code in a plain sentence), reasons an address was blocked, or API key problems.
- Messages by type and `reason`: invalid/forbidden link (one generic "can't open this", with no SSRF
  details), site not found, 401/403 ("login, paywall or bot protection"), 404, 429, 5xx, timeout,
  no connection, too many redirects, file larger than `MAX_DOWNLOAD_MB`, `.doc`, not a YouTube video,
  archive/Excel, scanned PDF, password-protected PDF, corrupt file, suspicious archive, no
  subtitles, video unavailable, YouTube blocking, too little text (by format), LLM errors.
- `llm_config` (key/model) is shown to the user as "the service is temporarily unavailable" and
  logged at ERROR. Unexpected exceptions: a neutral message, full traceback in the log. Expected
  ones: INFO, no URL in the log.
- A test scans `bot/` and requires a message for every `reason` raised anywhere: a new reason
  without a text breaks the build.

## Security (non-negotiable)
- **SSRF**: only `http/https`; resolve DNS and block private/loopback/link-local addresses
  (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`);
  check on every redirect too; at most 5 redirects.
  - **All** addresses a name resolves to are checked; addresses inside IPv4-mapped, 6to4 and NAT64
    are judged by the embedded IPv4; Teredo and multicast are forbidden.
  - Against DNS rebinding: the name is resolved once and the connection goes to the checked IP
    (`Host` and SNI stay original). Never hand the original host name to httpx.
  - Only ports 80 and 443 are allowed; URLs with a login/password (`user:pass@`) are rejected.
  - httpx: `trust_env=False` (a proxy from the environment would replace the checked address) and
    `follow_redirects=False` (redirects are followed manually through the guard).
  - All of the above lives in `security/`. New network calls to user URLs go only through
    `SafeFetcher`, never directly through httpx.
- Limits: download size ≤ `MAX_DOWNLOAD_MB` (15 by default), timeout ≤ 20 s,
  PDF pages ≤ `MAX_PDF_PAGES`, per-user rate limit (e.g. 10 requests/hour).
- Source content is data, not instructions (**prompt injection**): the system prompt explicitly
  forbids following commands from the page text; the result is validated against the schema.
- Never execute downloaded files. Delete temporary files.
- Respect `robots.txt` for bulk use; use an honest User-Agent.
- Tokens and keys live only in env; never log them.

## User-facing error messages
Clear messages in the user's language for: invalid URL, page unavailable (4xx/5xx), timeout, file
too large, unsupported format, paywall/login, video without subtitles, request limit exceeded, LLM
error. No stack traces for the user.

## Caching
`storage/cache.py` (SQLite via `aiosqlite`, file `CACHE_DB_PATH`) + `Orchestrator.run/cached_summary`.
The cache stores a finished `Summary` so that a repeated link costs neither a download nor a model
call (and one request can now cost two calls).
- **Key**: `cache_key(url)` from `bot/urls.py` + the card language. No `utm_*`, `fbclid`, `gclid`
  and similar, no `#anchor`, lowercase host, default port dropped; for YouTube it is the video id
  (`youtu.be/ID`, `watch?v=ID&t=30s`, `shorts/ID` are one key). **Nothing else is merged**: a
  trailing slash, path case, `www.`, `http`/`https`, the order and values of other parameters all
  distinguish pages (merging different pages would mean serving someone else's summary). Links with
  a login in the address and non-http links have no key: the pipeline itself rejects them.
- **A fresh entry** (younger than `CACHE_TTL_HOURS`) is served immediately: no download, no model,
  no waiting for a slot, and it is **not counted against the user's hourly limit** (only real
  requests count).
- **An old entry** (TTL expired but younger than 7 days): the page is read again, and if the hash of
  the cleaned text is the same, the model is not called and the old answer is used (the age shown
  in the card stays the same, the entry is not "renewed"; after 7 days it is deleted and the answer
  is built again). A different hash: a new answer and a new entry. Counters like "in 103 days" in
  the text cause false misses, never false hits. Linked pages (2.7) are not part of the hash: their
  changes are picked up after 7 days or via the Refresh button.
- **Fingerprint** of an entry: the model, `LLM_EFFORT`, the JSON schema, the text of `system.md`,
  `FOLLOW_LINKED_PAGES`, `MAX_CHARS` and `PIPELINE_VERSION` (a constant in `orchestrator.py`: bump it
  when extraction, cleaning, checks or the card change). An entry with a different fingerprint is
  not served. Editing the prompt or changing the model leaves no stale answers.
- When serving from the cache, the code sets `source_url` to **the link this user sent** (the entry
  stores the first user's link), recalculates `possibly_passed` against today's date and sets
  `cached_minutes_ago`: the card shows a line "🕒 Showing a saved answer: the page was read N min
  ago…" before the source.
- **Not cached**: errors, pages without text, files from the chat (the address
  `telegram://uploaded-file` is the same for everyone: no key). Only answers about public pages are
  stored, **with no user data**.
- **The cache can never break a request**: file unavailable/not a database/corrupt row/full disk =
  a miss, one warning in the log without the URL, and the pipeline runs as usual. The schema is
  versioned via `PRAGMA user_version` (recreated on change: there is nothing to migrate in a cache).
  Rows older than 7 days are deleted, at most 5000 in total (the newest are kept).
- **The "🔄 Обновить" (Refresh) button** (`handlers/refresh.py`) sits under the last message of the
  card. `callback_data` is `refresh:<16 hex>` (a hash of the key and language; no address in it,
  Telegram's limit is 64 bytes); it is used to look up the link (as it was sent) and the language in
  the table. Pressing it ignores the cache (`run(..., refresh=True)`: both the fresh entry and reuse
  by hash), **counts against the hourly limit**, and the same `button_id` never runs twice at once
  ("already refreshing"). Success: a new card with its own button, and the old card's button is
  removed. Failure: a clear message, and the old card keeps its button. The button is not shown if
  the answer was not saved (cache disabled/broken); an expired button replies "this button has
  expired". `CACHE_TTL_HOURS=0` disables both the cache and the button.
- Not done: two simultaneous first requests for the same new link both pay for the model (there is
  no shared "in-flight" request).

## Configuration (.env)
```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
LLM_MODEL=
LLM_EFFORT=medium
DEFAULT_LANG=ru
MAX_DOWNLOAD_MB=15
MAX_CHARS=120000
MAX_PDF_PAGES=80
REQUEST_TIMEOUT_S=20
RATE_LIMIT_PER_HOUR=10
MAX_CONCURRENT_JOBS=4
FOLLOW_LINKED_PAGES=2
CACHE_TTL_HOURS=24
CACHE_DB_PATH=data/cache.sqlite3
LOG_LEVEL=INFO
```

## Commands
```
uv sync                      # install
uv run python -m bot.main    # run the bot
uv run pytest                # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy bot
uv run python -m bot.evaluation   # quality on the golden set (uses API tokens)
uv run pytest -m live -s        # tests against the real API
docker build -t admissions-bot . && docker run --env-file .env admissions-bot
```

## Testing
- Unit tests: type detector, url_guard (SSRF cases), cleaner, rendering, deadline quote checks.
- Extractors are tested on stored fixtures (`tests/fixtures`), with no network (respx).
- A pipeline test with a mocked LLM. A separate optional e2e suite against the real API
  (marker `@pytest.mark.live`), not run by default.
- **Golden set** (`evals/golden.json`, run with `uv run python -m bot.evaluation [--only ID] [--json]`).
  A case is a stored input (file) or a live URL plus expected facts: source class, deadline dates
  (ISO, or text for dates without a year), substrings in costs, links, forbidden text (checked
  everywhere except `warnings`), or an expected error. Metrics: deadline recall, **deadlines not
  confirmed by the text** (a measure of invention that does not depend on what the file lists),
  forbidden text. The exit code is non-zero if recall < 90% or there are unconfirmed deadlines.
  Cases without a file (URL only) are live: someone else's site may go down or change, so they run
  only with `--live`. The set currently has 8 stored cases (HTML in two languages, PDF, DOCX, a
  link-heavy page, an injection, a JS shell with an expected error). Real pages are added as an
  entry with `"source": {"url": "..."}` and no `file`; their expectations must be checked against
  the page itself, and they go stale quickly (dates move into the past).
- The measuring tool is covered by tests itself (`tests/test_evaluation.py`): mutating a metric must
  fail a test.

## Definition of Done for a task
Code + tests pass, `ruff` and `mypy` report no errors, `TASKS.md` is updated, no new secrets in the
code, behaviour checked by hand in a real Telegram chat (for user-facing features).

## Out of MVP scope (do not build until asked)
OCR of scans, the old `.doc` format, pages behind login, multi-link comparisons, a web admin panel,
payments, deadline reminders (Phase 3).
