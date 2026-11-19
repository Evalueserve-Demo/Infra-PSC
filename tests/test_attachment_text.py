"""Tests for reading the text out of an attachment.

The fixtures are synthetic test attachments that were sent to the group
mailbox. Whatever the file, extraction must return a status and never raise:
a file that cannot be read must not stop the email it came with.
"""

import io
import pathlib

import pytest
from pypdf import PdfWriter

from payment_query_platform import attachment_text
from payment_query_platform.attachment_text import EXTRACTED, FAILED, SKIPPED, extract_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "attachments"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def pdf(pages=1, password=None):
    """A PDF of blank pages built in memory: a stand-in for a scan."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class TestPdf:
    def test_an_invoice_yields_its_invoice_and_purchase_order(self):
        result = extract_text(fixture("INV-10234.pdf"), "application/pdf", "INV-10234.pdf")

        assert result.status == EXTRACTED
        assert "INV-10234" in result.text
        assert "4500123456" in result.text

    def test_a_statement_yields_every_invoice_on_it(self):
        result = extract_text(fixture("statement_sept.pdf"), "application/pdf", "statement_sept.pdf")

        assert result.status == EXTRACTED
        for invoice in ("INV-55678", "INV-10234", "INV-12"):
            assert invoice in result.text

    def test_a_pdf_sent_as_octet_stream_is_recognised_by_its_name(self):
        """Mail clients often do not say what a file is."""
        result = extract_text(fixture("INV-10234.pdf"), "application/octet-stream", "INV-10234.PDF")

        assert result.status == EXTRACTED

    def test_a_pdf_with_no_text_layer_is_skipped_as_needing_ocr(self):
        result = extract_text(pdf(pages=1), "application/pdf", "scan.pdf")

        assert result.status == SKIPPED
        assert "OCR" in result.reason

    def test_a_password_protected_pdf_is_skipped(self):
        result = extract_text(pdf(password="secret"), "application/pdf", "locked.pdf")

        assert result.status == SKIPPED
        assert "password" in result.reason

    @pytest.mark.parametrize("content", [
        b"%PDF-1.4 this is not really a pdf",
        fixture("INV-10234.pdf")[:700],
    ], ids=["garbage", "truncated"])
    def test_a_broken_pdf_fails_without_raising(self, content):
        result = extract_text(content, "application/pdf", "broken.pdf")

        assert result.status == FAILED
        assert result.reason

    def test_only_the_first_pages_of_a_long_pdf_are_read(self, monkeypatch):
        monkeypatch.setattr(attachment_text, "MAX_PDF_PAGES", 1)
        two_pages = PdfWriter()
        for name in ("INV-10234.pdf", "statement_sept.pdf"):
            two_pages.append(io.BytesIO(fixture(name)))
        buffer = io.BytesIO()
        two_pages.write(buffer)

        result = extract_text(buffer.getvalue(), "application/pdf", "both.pdf")

        assert result.status == EXTRACTED
        assert "ACME SUPPLIES" in result.text
        assert "STATEMENT OF ACCOUNT" not in result.text
        assert "first 1 of 2 pages" in result.reason


class TestText:
    def test_a_text_file_is_decoded(self):
        result = extract_text(fixture("sample_payment_status_invoice.txt"), "text/plain", "sample.txt")

        assert result.status == EXTRACTED
        assert "INV-55678" in result.text

    def test_invalid_utf8_is_replaced_rather_than_failing(self):
        result = extract_text(b"Invoice INV-10234 \xff\xfe", "text/plain", "note.txt")

        assert result.status == EXTRACTED
        assert "INV-10234" in result.text

    def test_an_empty_text_file_is_skipped(self):
        assert extract_text(b"  \n ", "text/plain", "empty.txt").status == SKIPPED


class TestEverythingElse:
    def test_an_image_is_skipped_as_unsupported(self):
        result = extract_text(fixture("scan_0045.jpg"), "image/jpeg", "scan_0045.jpg")

        assert result.status == SKIPPED
        assert "image/jpeg" in result.reason

    def test_an_office_file_is_skipped_as_unsupported(self):
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        assert extract_text(b"PK\x03\x04", content_type, "letter.docx").status == SKIPPED

    def test_a_file_over_the_size_limit_is_not_read(self, monkeypatch):
        monkeypatch.setattr(attachment_text, "MAX_FILE_BYTES", 100)

        result = extract_text(fixture("INV-10234.pdf"), "application/pdf", "INV-10234.pdf")

        assert result.status == SKIPPED
        assert "larger than" in result.reason


class TestLength:
    def test_long_text_is_cut_and_marked(self, monkeypatch):
        monkeypatch.setattr(attachment_text, "MAX_TEXT_CHARS", 20)

        result = extract_text(b"INV-10234 " * 10, "text/plain", "long.txt")

        assert result.text.endswith("[truncated]")
        assert len(result.text) <= 20 + len(" [truncated]")
