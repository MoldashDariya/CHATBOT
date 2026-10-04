from bot.config import Settings
from bot.extractors.base import ExtractedContent, Extractor, RawSource
from bot.extractors.docx import DocxExtractor
from bot.extractors.html import HtmlExtractor
from bot.extractors.pdf import PdfExtractor
from bot.extractors.youtube import DEFAULT_LANGUAGES, YouTubeExtractor
from bot.pipeline.detector import SourceFormat
from bot.security.safe_fetch import SafeFetcher

__all__ = ["ExtractedContent", "Extractor", "RawSource", "build_extractors"]


def build_extractors(settings: Settings, fetcher: SafeFetcher) -> dict[SourceFormat, Extractor]:
    """One extractor per detected format; the orchestrator looks them up by `SourceFormat`."""
    caption_languages = tuple(dict.fromkeys([settings.default_lang, *DEFAULT_LANGUAGES]))
    return {
        SourceFormat.HTML: HtmlExtractor(),
        SourceFormat.PDF: PdfExtractor(max_pages=settings.max_pdf_pages),
        SourceFormat.DOCX: DocxExtractor(),
        SourceFormat.YOUTUBE: YouTubeExtractor(fetcher, languages=caption_languages),
    }
