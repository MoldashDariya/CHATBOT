"""Builds real DOCX files with python-docx for extractor tests."""

import io

import docx
from docx.document import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def add_hyperlink(paragraph: Paragraph, text: str, url: str) -> None:
    relation_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), relation_id)
    run = OxmlElement("w:r")
    run_text = OxmlElement("w:t")
    run_text.text = text
    run.append(run_text)
    link.append(run)
    paragraph._p.append(link)


def to_bytes(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def scholarship_docx(*, core_title: str | None = "Scholarship Guide 2027") -> bytes:
    document = docx.Document()
    if core_title is not None:
        document.core_properties.title = core_title
    document.add_heading("Scholarship Guide 2027", level=0)
    document.add_paragraph("Applications are open to all undergraduate students.")

    document.add_heading("Eligibility", level=1)
    document.add_paragraph("Citizen of the country", style="List Bullet")
    document.add_paragraph("GPA of at least 3.5", style="List Bullet")
    document.add_paragraph("Admission letter from a partner university", style="List Number")

    document.add_heading("Key dates", level=2)
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "Stage"
    table.cell(0, 1).text = "Deadline"
    table.cell(0, 2).text = "Notes"
    table.cell(1, 0).text = "Documents"
    table.cell(1, 1).text = "1 March 2027"
    table.cell(1, 2).text = "Online\nupload"
    merged = table.cell(2, 0).merge(table.cell(2, 1))
    merged.text = "Results announced 15 May 2027"
    table.cell(2, 2).text = "By email"

    paragraph = document.add_paragraph("Apply on ")
    add_hyperlink(paragraph, "the scholarship portal", "https://scholarships.example.gov/apply")
    paragraph.add_run(". Rules: https://scholarships.example.gov/rules.")

    document.add_paragraph("Mail us at ").add_run("help@example.gov")
    mail = document.add_paragraph("Contact: ")
    add_hyperlink(mail, "email us", "mailto:help@example.gov")
    inner = document.add_paragraph("Internal jump: ")
    add_hyperlink(inner, "see above", "#eligibility")

    document.add_paragraph("")  # empty paragraphs must not leave blank blocks
    document.add_paragraph("Good luck!")
    return to_bytes(document)
