import io
import zipfile

import docx
import pytest

from bot.errors import ExtractionError
from bot.extractors.base import RawSource
from bot.extractors.docx import DocxExtractor
from bot.pipeline.detector import SourceFormat
from tests.docxgen import scholarship_docx, to_bytes

URL = "https://scholarships.example.gov/guide.docx"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def source(body: bytes) -> RawSource:
    return RawSource(url=URL, body=body, content_type=DOCX_TYPE)


async def extract(body: bytes | None = None, **kwargs: int):  # type: ignore[no-untyped-def]
    payload = scholarship_docx() if body is None else body
    return await DocxExtractor(**kwargs).extract(source(payload))


async def test_structure_is_rendered_in_document_order() -> None:
    result = await extract()

    lines = result.text.split("\n\n")
    assert lines[0] == "# Scholarship Guide 2027"
    assert lines[1] == "Applications are open to all undergraduate students."
    assert lines[2] == "## Eligibility"
    assert lines[3:6] == [
        "- Citizen of the country",
        "- GPA of at least 3.5",
        "- Admission letter from a partner university",
    ]
    assert lines[6] == "### Key dates"
    assert result.source_format is SourceFormat.DOCX
    assert result.source_url == URL


async def test_table_rows_stay_on_one_line_and_merged_cells_are_not_repeated() -> None:
    text = (await extract()).text

    assert "| Stage | Deadline | Notes |" in text
    assert "| Documents | 1 March 2027 | Online upload |" in text  # newline inside a cell
    assert "| Results announced 15 May 2027 | By email |" in text
    assert text.count("Results announced 15 May 2027") == 1


async def test_hyperlink_targets_become_visible_and_are_reported() -> None:
    result = await extract()

    assert (
        "Apply on the scholarship portal (https://scholarships.example.gov/apply). "
        "Rules: https://scholarships.example.gov/rules."
    ) in result.text
    assert result.links == (
        "https://scholarships.example.gov/apply",
        "https://scholarships.example.gov/rules",
    )


async def test_only_web_links_are_reported() -> None:
    result = await extract()

    assert not any("mailto" in link or link.startswith("#") for link in result.links)
    assert "mailto" not in result.text  # label only: "email us"
    assert "email us" in result.text
    assert "see above" in result.text


async def test_empty_paragraphs_leave_no_gaps() -> None:
    text = (await extract()).text

    assert "\n\n\n" not in text
    assert text.endswith("Good luck!")


async def test_title_prefers_document_properties_then_first_heading() -> None:
    assert (await extract()).title == "Scholarship Guide 2027"

    from_properties = await extract(scholarship_docx(core_title="Official Title"))
    assert from_properties.title == "Official Title"

    from_heading = await extract(scholarship_docx(core_title=""))
    assert from_heading.title == "Scholarship Guide 2027"


async def test_title_is_none_without_properties_or_headings() -> None:
    document = docx.Document()
    document.add_paragraph("Just some text with no heading at all.")

    result = await extract(to_bytes(document))

    assert result.title is None
    assert result.text == "Just some text with no heading at all."


async def test_numbered_paragraphs_without_a_list_style_are_list_items() -> None:
    document = docx.Document()
    paragraph = document.add_paragraph("Submit the application form")
    properties = paragraph._p.get_or_add_pPr()
    properties.get_or_add_numPr().get_or_add_numId().val = 1

    assert (await extract(to_bytes(document))).text == "- Submit the application form"


async def test_cyrillic_text_survives() -> None:
    document = docx.Document()
    document.add_heading("Правила приёма 2027", level=1)
    document.add_paragraph(
        "Приём документов до 15 июля 2027 года. Стоимость обучения: 1 200 000 тенге."
    )

    result = await extract(to_bytes(document))

    assert result.text.startswith("## Правила приёма 2027")
    assert "до 15 июля 2027 года" in result.text
    assert "1 200 000 тенге" in result.text


# --- hostile / broken input ---------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [b"not a zip at all", b"PK\x03\x04" + b"\x00" * 64, b""],
    ids=["text", "zip-magic-garbage", "empty"],
)
async def test_broken_files_are_a_clean_error(body: bytes) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await extract(body)
    assert exc_info.value.reason == "corrupt"


async def test_zip_that_is_not_a_docx_package_is_a_clean_error() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")

    with pytest.raises(ExtractionError) as exc_info:
        await extract(buffer.getvalue())
    assert exc_info.value.reason == "corrupt"


def docx_with_part(name: str, payload: bytes) -> bytes:
    """A valid DOCX whose `name` part is replaced by (or, if absent, extended with) `payload`."""
    source_zip = zipfile.ZipFile(io.BytesIO(scholarship_docx()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source_zip.infolist():
            data = payload if info.filename == name else source_zip.read(info.filename)
            target.writestr(info.filename, data)
        if name not in source_zip.namelist():
            target.writestr(name, payload)
    return buffer.getvalue()


async def test_oversized_document_xml_is_refused_before_parsing() -> None:
    bomb = docx_with_part("word/document.xml", b"<w:document>" + b" " * 3_000_000)
    assert len(bomb) < 100_000  # tiny on the wire

    with pytest.raises(ExtractionError) as exc_info:
        await extract(bomb, max_document_xml_bytes=1_000_000)
    assert exc_info.value.reason == "unsafe_archive"


async def test_total_uncompressed_size_is_limited() -> None:
    padded = docx_with_part("word/media/padding.bin", b"\x00" * 3_000_000)

    with pytest.raises(ExtractionError) as exc_info:
        await extract(padded, max_total_bytes=2_000_000)
    assert exc_info.value.reason == "unsafe_archive"


async def test_too_many_archive_entries_are_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number in range(5001):
            archive.writestr(f"word/media/{number}.bin", b"")

    with pytest.raises(ExtractionError) as exc_info:
        await extract(buffer.getvalue())
    assert exc_info.value.reason == "unsafe_archive"


async def test_normal_documents_are_within_the_default_limits() -> None:
    assert (await DocxExtractor().extract(source(scholarship_docx()))).text


async def test_heading_levels_keep_the_title_above_heading_one() -> None:
    document = docx.Document()
    document.add_heading("Document title", level=0)
    for level in (1, 2, 3, 4, 5):
        document.add_heading(f"Heading {level}", level=level)

    lines = (await extract(to_bytes(document))).text.split("\n\n")

    assert lines == [
        "# Document title",
        "## Heading 1",
        "### Heading 2",
        "#### Heading 3",
        "#### Heading 4",  # deeper levels are capped
        "#### Heading 5",
    ]
