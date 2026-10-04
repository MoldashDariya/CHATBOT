import asyncio
import logging

import trafilatura

from bot.errors import ExtractionError
from bot.extractors.base import ExtractedContent, RawSource, clean_meta, find_urls
from bot.pipeline.detector import SourceFormat

logger = logging.getLogger(__name__)

# Below this, a second pass with a more permissive extraction is tried before giving up.
_THIN_TEXT_CHARS = 500


class HtmlExtractor:
    """Main-content extraction with trafilatura: menus, cookie banners, ads and footers are
    dropped; headings, lists, tables (as `| a | b |` rows) and in-content links are kept."""

    async def extract(self, source: RawSource) -> ExtractedContent:
        return await asyncio.to_thread(self._extract, source)

    def _extract(self, source: RawSource) -> ExtractedContent:
        try:
            text = self._main_text(source)
            metadata = trafilatura.extract_metadata(source.body, default_url=source.url)
        except Exception as exc:
            logger.warning("HTML extraction failed: %s", type(exc).__name__, exc_info=True)
            raise ExtractionError("corrupt") from exc

        # Links come from the cleaned text (trafilatura writes them as `[label](url)`), so
        # navigation and footer links never get in.
        links = find_urls(text)
        canonical = metadata.url if metadata and metadata.url != source.url else None
        return ExtractedContent(
            text=text,
            title=(metadata.title.strip() if metadata and metadata.title else None) or None,
            source_url=source.url,
            source_format=SourceFormat.HTML,
            links=links,
            meta=clean_meta(
                # Trafilatura returns the page's declared canonical URL, else the one we gave it.
                canonical_url=canonical,
                published=metadata.date if metadata else None,
                site_name=metadata.sitename if metadata else None,
                author=metadata.author if metadata else None,
                description=metadata.description if metadata else None,
            ),
        )

    @staticmethod
    def _main_text(source: RawSource) -> str:
        def run(*, favor_recall: bool) -> str:
            return (
                trafilatura.extract(
                    source.body,
                    url=source.url,
                    include_links=True,
                    include_tables=True,
                    include_comments=False,
                    output_format="txt",
                    favor_recall=favor_recall,
                )
                or ""
            ).strip()

        text = run(favor_recall=False)
        if len(text) < _THIN_TEXT_CHARS:
            # Pages with unusual layouts: accept more candidate blocks, keep the longer result.
            text = max(text, run(favor_recall=True), key=len)
        return text
