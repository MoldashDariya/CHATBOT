"""Texts of the summary card and of error messages, in the three interface languages.

The Kazakh strings are kept short and simple on purpose; have a native speaker review them.
Every key must exist in every language with the same `{placeholders}` (a test enforces it).
"""

DEFAULT_LANGUAGE = "ru"

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        # card sections
        "deadlines": "Дедлайны",
        "key_points": "Основные пункты",
        "eligibility": "Кто может подаваться",
        "costs": "Стоимость / финансирование",
        "requirements": "Требования",
        "links": "Полезные ссылки",
        "notes": "Важно",
        "source": "Источник",
        "not_stated": "не указано в источнике",
        "no_links": "в источнике нет подходящих ссылок",
        "untitled": "Без названия",
        # what the page is
        "type_university_page": "страница университета",
        "type_scholarship": "стипендия",
        "type_news_article": "новость",
        "type_publication": "публикация",
        "type_general_site": "сайт",
        "type_other": "другое",
        # confidence
        "confidence": "Надёжность разбора",
        "confidence_high": "высокая",
        "confidence_medium": "средняя",
        "confidence_low": "низкая, всё проверьте по источнику",
        # notes derived from facts the code knows
        "note_unverified": "❓ Даты с этой пометкой не удалось сопоставить с текстом источника.",
        "note_passed": "⏳ Дедлайн с этой пометкой, возможно, уже прошёл: уточните на сайте.",
        "note_dropped_links": "Скрыто ссылок, которых нет в источнике: {n}.",
        "note_truncated": "Текст был слишком длинным и обрезан: часть источника не прочитана.",
        "note_pages": "Прочитано страниц: {read} из {total}.",
        "note_not_admission": "Похоже, страница не про поступление, учёбу или стипендии.",
        # errors
        "err_bad_url": (
            "Не могу открыть эту ссылку. Проверьте адрес: он должен начинаться "
            "с http:// или https://."
        ),
        "err_host": "Не нашёл сайт по этому адресу. Проверьте, нет ли опечатки в ссылке.",
        "err_http_auth": (
            "Сайт не пускает меня на страницу: нужен вход, доступ платный или стоит защита "
            "от ботов. Попробуйте прислать PDF или другую страницу."
        ),
        "err_http_gone": "Страница не найдена (ошибка {code}). Проверьте ссылку.",
        "err_http_busy": "Сайт временно ограничил запросы. Попробуйте позже.",
        "err_http_server": "Сайт сейчас не отвечает (ошибка сервера {code}). Попробуйте позже.",
        "err_http_other": "Сайт отказал в доступе (код {code}).",
        "err_timeout": "Сайт слишком долго отвечает. Попробуйте позже.",
        "err_connect": "Не удалось соединиться с сайтом. Попробуйте позже.",
        "err_redirects": "У этой ссылки слишком много перенаправлений.",
        "err_too_large": "Файл слишком большой: я читаю материалы до {mb} МБ.",
        "err_legacy_office": (
            "Старый формат Word (.doc) не поддерживается. Сохраните файл как PDF или DOCX "
            "и пришлите снова."
        ),
        "err_not_a_video": "Это не ссылка на видео. Пришлите ссылку на конкретное видео YouTube.",
        "err_empty": "Страница пустая: читать нечего.",
        "err_archive": (
            "Архивы и файлы Excel или PowerPoint не поддерживаются. Пришлите PDF, DOCX "
            "или ссылку на страницу."
        ),
        "err_unsupported": (
            "Этот тип содержимого не поддерживается. Я читаю страницы сайтов, PDF, DOCX "
            "и видео YouTube с субтитрами."
        ),
        "err_scanned_pdf": (
            "В этом PDF нет текстового слоя, похоже на скан. Распознавание сканов пока "
            "не поддерживается."
        ),
        "err_encrypted_pdf": "PDF защищён паролем, открыть его я не могу.",
        "err_corrupt": "Не удалось прочитать файл: он повреждён или нестандартный.",
        "err_unsafe_archive": "Файл подозрительно велик после распаковки, поэтому я его не открываю.",
        "err_extract_timeout": "Обработка заняла слишком много времени. Попробуйте файл поменьше.",
        "err_no_transcript": "У этого видео нет субтитров, а без них я не узнаю, о чём оно.",
        "err_video_unavailable": "Видео недоступно: оно закрыто, удалено или с ограничением по возрасту.",
        "err_youtube_blocked": "YouTube временно блокирует мои запросы. Попробуйте позже.",
        "err_youtube_failed": "Не удалось получить субтитры с YouTube. Попробуйте позже.",
        "err_thin_html": (
            "На странице почти нет текста. Возможно, нужен вход или доступ платный, либо "
            "содержимое подгружается скриптами (JavaScript) и мне недоступно. "
            "Попробуйте прислать PDF или другую страницу."
        ),
        "err_thin_file": "В файле почти нет текста, разбирать нечего.",
        "err_thin_video": "В субтитрах слишком мало текста, чтобы сделать выводы.",
        "err_llm_busy": "Сервис анализа сейчас перегружен. Попробуйте через минуту.",
        "err_llm_unreachable": "Не удалось связаться с сервисом анализа. Попробуйте позже.",
        "err_llm_refused": "Этот материал я обработать не могу.",
        "err_llm_too_large": "Материал слишком большой для разбора.",
        "err_llm_retry": "Не получилось собрать аккуратное саммари. Попробуйте ещё раз.",
        "err_service": "Сервис анализа временно недоступен. Мы уже разбираемся.",
        # chat flow
        "status_fetch": "⏳ Открываю страницу…",
        "status_read": "📄 Читаю текст…",
        "status_analyze": "🧠 Анализирую…",
        "no_url": "Не нашёл ссылку в сообщении. Пришли адрес, начинающийся с http:// или https://.",
        "multiple_urls": "Нашёл несколько ссылок, обработаю первую. Остальные пришли отдельными сообщениями.",
        "rate_limited": "Слишком много запросов за последний час. Попробуй снова через {minutes} мин.",
        "err_processing_timeout": "Обработка заняла слишком много времени. Попробуйте ещё раз или пришлите страницу поменьше.",
        "status_download": "⏳ Скачиваю файл…",
        "unsupported_message": "Я читаю ссылки на страницы, PDF и DOCX (ссылкой или файлом) и видео YouTube с субтитрами. Пришли что-то из этого.",
        "err_download": "Не удалось скачать файл из Telegram. Попробуйте отправить его ещё раз.",
        "status_follow": "🔎 Проверяю связанные страницы…",
        "also_read": "Дополнительно прочитано (страницы, на которые ссылается источник)",
        "err_generic": "Что-то пошло не так. Попробуйте позже.",
    },
    "en": {
        "deadlines": "Deadlines",
        "key_points": "Key points",
        "eligibility": "Who can apply",
        "costs": "Costs / funding",
        "requirements": "Requirements",
        "links": "Useful links",
        "notes": "Please note",
        "source": "Source",
        "not_stated": "not stated in the source",
        "no_links": "no suitable links in the source",
        "untitled": "Untitled",
        "type_university_page": "university page",
        "type_scholarship": "scholarship",
        "type_news_article": "news",
        "type_publication": "publication",
        "type_general_site": "website",
        "type_other": "other",
        "confidence": "Reliability of this summary",
        "confidence_high": "high",
        "confidence_medium": "medium",
        "confidence_low": "low, check everything against the source",
        "note_unverified": "❓ Dates with this mark could not be matched to the source text.",
        "note_passed": "⏳ A deadline with this mark may already have passed: check the website.",
        "note_dropped_links": "Links hidden because they are not in the source: {n}.",
        "note_truncated": "The text was too long and was cut: part of the source was not read.",
        "note_pages": "Pages read: {read} of {total}.",
        "note_not_admission": "This page does not seem to be about admission, study or scholarships.",
        "err_bad_url": "I can't open this link. Check the address: it must start with http:// or https://.",
        "err_host": "I couldn't find a website at this address. Check the link for typos.",
        "err_http_auth": (
            "The site won't let me in: it needs a login, is paywalled or has bot protection. "
            "Try sending a PDF or a different page."
        ),
        "err_http_gone": "Page not found (error {code}). Check the link.",
        "err_http_busy": "The site is temporarily limiting requests. Try again later.",
        "err_http_server": "The site is not responding (server error {code}). Try again later.",
        "err_http_other": "The site refused access (code {code}).",
        "err_timeout": "The site takes too long to respond. Try again later.",
        "err_connect": "I couldn't connect to the site. Try again later.",
        "err_redirects": "This link redirects too many times.",
        "err_too_large": "The file is too large: I read material up to {mb} MB.",
        "err_legacy_office": (
            "The old Word format (.doc) is not supported. Save the file as PDF or DOCX "
            "and send it again."
        ),
        "err_not_a_video": "This is not a link to a video. Send a link to a specific YouTube video.",
        "err_empty": "The page is empty: there is nothing to read.",
        "err_archive": (
            "Archives and Excel or PowerPoint files are not supported. Send a PDF, a DOCX "
            "or a link to a page."
        ),
        "err_unsupported": (
            "This type of content is not supported. I read web pages, PDF, DOCX and YouTube "
            "videos with subtitles."
        ),
        "err_scanned_pdf": (
            "This PDF has no text layer, it looks like a scan. Reading scans is not supported yet."
        ),
        "err_encrypted_pdf": "The PDF is password-protected, I can't open it.",
        "err_corrupt": "I couldn't read the file: it is damaged or non-standard.",
        "err_unsafe_archive": "The file is suspiciously large once unpacked, so I won't open it.",
        "err_extract_timeout": "Processing took too long. Try a smaller file.",
        "err_no_transcript": "This video has no subtitles, and without them I can't tell what it is about.",
        "err_video_unavailable": "The video is unavailable: it is private, removed or age-restricted.",
        "err_youtube_blocked": "YouTube is temporarily blocking my requests. Try again later.",
        "err_youtube_failed": "I couldn't get the subtitles from YouTube. Try again later.",
        "err_thin_html": (
            "There is almost no text on the page. It may need a login or be paywalled, or "
            "its content is loaded by scripts (JavaScript) that I can't run. "
            "Try sending a PDF or a different page."
        ),
        "err_thin_file": "The file has almost no text, nothing to analyse.",
        "err_thin_video": "The subtitles have too little text to draw conclusions from.",
        "err_llm_busy": "The analysis service is overloaded right now. Try again in a minute.",
        "err_llm_unreachable": "I couldn't reach the analysis service. Try again later.",
        "err_llm_refused": "I can't process this material.",
        "err_llm_too_large": "The material is too large to analyse.",
        "err_llm_retry": "I couldn't produce a clean summary. Please try again.",
        "err_service": "The analysis service is temporarily unavailable. We are looking into it.",
        "status_fetch": "⏳ Opening the page…",
        "status_read": "📄 Reading the text…",
        "status_analyze": "🧠 Analysing…",
        "no_url": "I couldn't find a link in your message. Send an address starting with http:// or https://.",
        "multiple_urls": "I found several links and will process the first one. Send the others as separate messages.",
        "rate_limited": "Too many requests in the last hour. Try again in {minutes} min.",
        "err_processing_timeout": "Processing took too long. Try again or send a smaller page.",
        "status_download": "⏳ Downloading the file…",
        "unsupported_message": "I read links to pages, PDF and DOCX (as a link or a file) and YouTube videos with subtitles. Please send one of those.",
        "err_download": "I couldn't download the file from Telegram. Try sending it again.",
        "status_follow": "🔎 Checking linked pages…",
        "also_read": "Also read (pages the source links to)",
        "err_generic": "Something went wrong. Try again later.",
    },
    "kk": {
        "deadlines": "Мерзімдер",
        "key_points": "Негізгі тармақтар",
        "eligibility": "Кім өтініш бере алады",
        "costs": "Құны / қаржыландыру",
        "requirements": "Талаптар",
        "links": "Пайдалы сілтемелер",
        "notes": "Назар аударыңыз",
        "source": "Дереккөз",
        "not_stated": "дереккөзде көрсетілмеген",
        "no_links": "дереккөзде қолайлы сілтемелер жоқ",
        "untitled": "Атауы жоқ",
        "type_university_page": "университет беті",
        "type_scholarship": "шәкіртақы",
        "type_news_article": "жаңалық",
        "type_publication": "жарияланым",
        "type_general_site": "сайт",
        "type_other": "басқа",
        "confidence": "Талдау сенімділігі",
        "confidence_high": "жоғары",
        "confidence_medium": "орташа",
        "confidence_low": "төмен, бәрін дереккөзден тексеріңіз",
        "note_unverified": "❓ Бұл белгісі бар күндерді дереккөз мәтінімен салыстыру мүмкін болмады.",
        "note_passed": "⏳ Бұл белгісі бар мерзім өтіп кеткен болуы мүмкін: сайттан тексеріңіз.",
        "note_dropped_links": "Дереккөзде жоқ сілтемелер жасырылды: {n}.",
        "note_truncated": "Мәтін тым ұзын болғандықтан қысқартылды: дереккөздің бір бөлігі оқылмады.",
        "note_pages": "Оқылған беттер: {read} / {total}.",
        "note_not_admission": "Бұл бет оқуға түсуге, оқуға немесе шәкіртақыға қатысты емес сияқты.",
        "err_bad_url": "Бұл сілтемені аша алмаймын. Мекенжай http:// немесе https:// деп басталуы керек.",
        "err_host": "Бұл мекенжайда сайт табылмады. Сілтемеде қате жоқ па, тексеріңіз.",
        "err_http_auth": (
            "Сайт мені кіргізбейді: кіру керек, қолжетімділік ақылы немесе боттардан қорғаныс бар. "
            "PDF немесе басқа бетті жіберіп көріңіз."
        ),
        "err_http_gone": "Бет табылмады (қате {code}). Сілтемені тексеріңіз.",
        "err_http_busy": "Сайт сұрауларды уақытша шектеді. Кейінірек қайталаңыз.",
        "err_http_server": "Сайт жауап бермейді (сервер қатесі {code}). Кейінірек қайталаңыз.",
        "err_http_other": "Сайт қолжетімділікті қабылдамады (код {code}).",
        "err_timeout": "Сайт тым ұзақ жауап береді. Кейінірек қайталаңыз.",
        "err_connect": "Сайтқа қосыла алмадым. Кейінірек қайталаңыз.",
        "err_redirects": "Бұл сілтеме тым көп бағыттайды.",
        "err_too_large": "Файл тым үлкен: мен {mb} МБ дейінгі материалды оқимын.",
        "err_legacy_office": (
            "Word-тың ескі форматы (.doc) қолдау таппайды. Файлды PDF немесе DOCX ретінде "
            "сақтап, қайта жіберіңіз."
        ),
        "err_not_a_video": "Бұл бейнеге сілтеме емес. YouTube-тағы нақты бейнеге сілтеме жіберіңіз.",
        "err_empty": "Бет бос: оқитын ештеңе жоқ.",
        "err_archive": (
            "Архивтер мен Excel немесе PowerPoint файлдары қолдау таппайды. PDF, DOCX немесе "
            "бетке сілтеме жіберіңіз."
        ),
        "err_unsupported": (
            "Мазмұнның бұл түрі қолдау таппайды. Мен сайт беттерін, PDF, DOCX және субтитрлері "
            "бар YouTube бейнелерін оқимын."
        ),
        "err_scanned_pdf": "Бұл PDF-те мәтін қабаты жоқ, скан сияқты. Скандарды оқу әзірге жоқ.",
        "err_encrypted_pdf": "PDF құпиясөзбен қорғалған, мен оны аша алмаймын.",
        "err_corrupt": "Файлды оқи алмадым: ол бүлінген немесе стандартты емес.",
        "err_unsafe_archive": "Файл ашылғаннан кейін күдікті түрде үлкен, сондықтан оны ашпаймын.",
        "err_extract_timeout": "Өңдеу тым ұзаққа созылды. Кішірек файл жіберіп көріңіз.",
        "err_no_transcript": "Бұл бейнеде субтитр жоқ, ал онсыз оның не туралы екенін біле алмаймын.",
        "err_video_unavailable": "Бейне қолжетімсіз: жабық, жойылған немесе жасына шектеу бар.",
        "err_youtube_blocked": "YouTube менің сұрауларымды уақытша бұғаттап тұр. Кейінірек қайталаңыз.",
        "err_youtube_failed": "YouTube-тан субтитрді ала алмадым. Кейінірек қайталаңыз.",
        "err_thin_html": (
            "Бетте мәтін дерлік жоқ. Кіру керек немесе қолжетімділік ақылы болуы мүмкін, не "
            "мазмұн скрипттер (JavaScript) арқылы жүктеледі, ал мен оны көре алмаймын. "
            "PDF немесе басқа бетті жіберіп көріңіз."
        ),
        "err_thin_file": "Файлда мәтін дерлік жоқ, талдайтын ештеңе жоқ.",
        "err_thin_video": "Субтитрлерде қорытынды жасауға мәтін тым аз.",
        "err_llm_busy": "Талдау қызметі қазір шамадан тыс жүктелген. Бір минуттан кейін қайталаңыз.",
        "err_llm_unreachable": "Талдау қызметіне қосыла алмадым. Кейінірек қайталаңыз.",
        "err_llm_refused": "Бұл материалды өңдей алмаймын.",
        "err_llm_too_large": "Материал талдау үшін тым үлкен.",
        "err_llm_retry": "Таза қорытынды жасай алмадым. Қайталап көріңіз.",
        "err_service": "Талдау қызметі уақытша қолжетімсіз. Біз мәселемен айналысудамыз.",
        "status_fetch": "⏳ Бетті ашудамын…",
        "status_read": "📄 Мәтінді оқудамын…",
        "status_analyze": "🧠 Талдаудамын…",
        "no_url": "Хабарламада сілтеме таппадым. http:// немесе https:// деп басталатын мекенжайды жіберіңіз.",
        "multiple_urls": "Бірнеше сілтеме таптым, біріншісін өңдеймін. Қалғандарын бөлек хабарламамен жіберіңіз.",
        "rate_limited": "Соңғы сағатта сұраулар тым көп. {minutes} минуттан кейін қайталаңыз.",
        "err_processing_timeout": "Өңдеу тым ұзаққа созылды. Қайталап көріңіз немесе кішірек бет жіберіңіз.",
        "status_download": "⏳ Файлды жүктеп жатырмын…",
        "unsupported_message": "Мен беттерге сілтемелерді, PDF және DOCX (сілтеме немесе файл ретінде) және субтитрлері бар YouTube бейнелерін оқимын. Осылардың бірін жіберіңіз.",
        "err_download": "Файлды Telegram-нан жүктей алмадым. Қайта жіберіп көріңіз.",
        "status_follow": "🔎 Байланысты беттерді тексеріп жатырмын…",
        "also_read": "Қосымша оқылды (дереккөз сілтеме жасаған беттер)",
        "err_generic": "Бірдеңе дұрыс болмады. Кейінірек қайталаңыз.",
    },
}


def text(language: str, key: str, **values: object) -> str:
    """`key` in `language` (Russian if the language is unknown), with `{placeholders}` filled."""
    table = TEXTS.get(language, TEXTS[DEFAULT_LANGUAGE])
    template = table.get(key) or TEXTS[DEFAULT_LANGUAGE][key]
    return template.format(**values) if values else template
