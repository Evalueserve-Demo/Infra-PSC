"""Tests for Stage 2 attachment processing and the record the consumer makes of it.

The files in fixtures/attachments are synthetic test attachments that were sent
to the group mailbox. Graph and S3 are replaced by small fakes, so nothing here
touches AWS.
"""

import hashlib
import pathlib
import uuid

import pytest

from payment_query_platform.ingestion import store_attachment
from payment_query_platform.ingestion.open_case import normalized_context
from payment_query_platform.storage.database import CaseRepository

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "attachments"


def fixture(name):
    return (FIXTURES / name).read_bytes()


class FakeMailbox:
    """Serves attachment bytes by attachment ID, as Graph would."""

    def __init__(self, files):
        self.files = files

    def get_attachment_bytes(self, message_id, attachment_id):
        return self.files[attachment_id]


class FakeStore:
    """Records what would have been written to S3."""

    def __init__(self):
        self.puts = []
        self.texts = {}

    def put(self, message_id, attachment_id, filename, content, content_type=None):
        self.puts.append(filename)
        return f"s3://test-bucket/messages/{message_id}/{filename}"

    def put_text(self, sha256, text):
        self.texts[sha256] = text
        return f"s3://test-bucket/text/{sha256}.txt"


class FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executed.append(params)

    def fetchone(self):
        return (uuid.uuid4(),)


class FakeDatabase:
    def __init__(self):
        self.cursor_ = FakeCursor()

    def cursor(self):
        return self.cursor_


def attachment(attachment_id, name, content_type="application/pdf"):
    """An attachment as Stage 1 describes it: metadata only, no content."""
    return {"attachment_id": attachment_id, "name": name, "content_type": content_type, "size_bytes": 1}


@pytest.fixture
def stage2(monkeypatch):
    """Run the attachment Lambda against fake Graph and S3."""
    store = FakeStore()

    def run(event, files):
        monkeypatch.setattr(store_attachment, "_build_mailbox", lambda: FakeMailbox(files))
        monkeypatch.setattr(store_attachment, "_get_store", lambda: store)
        return store_attachment.lambda_handler(event)

    run.store = store
    return run


class TestStoreAttachment:
    def test_each_attachment_carries_the_sha256_of_its_content(self, stage2):
        files = {"a1": fixture("INV-10234.pdf"), "a2": fixture("statement_sept.pdf")}
        event = {"message_id": "m1",
                 "attachments": [attachment("a1", "INV-10234.pdf"), attachment("a2", "statement_sept.pdf")]}

        stored = stage2(event, files)["attachments"]

        assert [a["sha256"] for a in stored] == [
            hashlib.sha256(files["a1"]).hexdigest(),
            hashlib.sha256(files["a2"]).hexdigest(),
        ]

    def test_the_actual_size_replaces_the_size_graph_reported(self, stage2):
        files = {"a1": fixture("scan_0045.jpg")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "scan_0045.jpg", "image/jpeg")]}

        stored = stage2(event, files)["attachments"]

        assert stored[0]["size_bytes"] == len(files["a1"])

    def test_the_same_file_under_another_name_has_the_same_sha256(self, stage2):
        """Suppliers re-attach the same invoice when they reply; this is what lets it be recorded once."""
        content = fixture("INV-10234.pdf")
        event = {"message_id": "m1",
                 "attachments": [attachment("a1", "INV-10234.pdf"), attachment("a2", "copy.pdf")]}

        stored = stage2(event, {"a1": content, "a2": content})["attachments"]

        assert stored[0]["sha256"] == stored[1]["sha256"]

    def test_every_attachment_is_stored_and_the_first_is_referenced(self, stage2):
        files = {"a1": fixture("INV-10234.pdf"), "a2": fixture("sample_payment_status_invoice.txt")}
        event = {"message_id": "m1",
                 "attachments": [attachment("a1", "INV-10234.pdf"),
                                 attachment("a2", "sample_payment_status_invoice.txt", "text/plain")]}

        result = stage2(event, files)

        assert stage2.store.puts == ["INV-10234.pdf", "sample_payment_status_invoice.txt"]
        assert result["attachment_s3_reference"] == result["attachments"][0]["s3_reference"]

    def test_a_message_without_attachments_passes_through_unchanged(self, stage2):
        event = {"message_id": "m1", "attachments": []}

        assert stage2(event, {}) == event


class TestTextExtraction:
    def test_the_text_of_a_readable_file_is_stored_beside_it(self, stage2):
        files = {"a1": fixture("INV-10234.pdf")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "INV-10234.pdf")]}

        stored = stage2(event, files)["attachments"][0]

        assert stored["extraction_status"] == "EXTRACTED"
        assert stored["extracted_text_uri"] == f"s3://test-bucket/text/{stored['sha256']}.txt"
        assert "INV-10234" in stage2.store.texts[stored["sha256"]]

    def test_the_text_never_travels_in_the_workflow_payload(self, stage2):
        """Step Functions and SQS cap a payload at 256 KB; the text stays in S3."""
        files = {"a1": fixture("statement_sept.pdf")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "statement_sept.pdf")]}

        result = stage2(event, files)

        assert "STATEMENT OF ACCOUNT" not in repr(result)

    def test_an_unreadable_file_is_recorded_and_nothing_is_stored_for_it(self, stage2):
        files = {"a1": fixture("scan_0045.jpg")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "scan_0045.jpg", "image/jpeg")]}

        stored = stage2(event, files)["attachments"][0]

        assert stored["extraction_status"] == "SKIPPED"
        assert stored["extracted_text_uri"] is None
        assert stored["extraction_note"]
        assert stage2.store.texts == {}

    def test_a_broken_file_does_not_stop_the_others(self, stage2):
        files = {"a1": b"%PDF-1.4 not really a pdf", "a2": fixture("INV-10234.pdf")}
        event = {"message_id": "m1",
                 "attachments": [attachment("a1", "broken.pdf"), attachment("a2", "INV-10234.pdf")]}

        stored = stage2(event, files)["attachments"]

        assert [a["extraction_status"] for a in stored] == ["FAILED", "EXTRACTED"]


class TestRecordingTheStoredAttachments:
    def test_the_consumer_can_record_what_stage2_returns(self, stage2):
        """The contract between the two Stage 2 Lambdas.

        ps.case_attachment requires a sha256. When Stage 2 did not supply one,
        every email with an attachment failed in the consumer with a KeyError.
        """
        files = {"a1": fixture("INV-10234.pdf")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "INV-10234.pdf")]}
        stored = stage2(event, files)["attachments"]
        database = FakeDatabase()

        recorded = CaseRepository(database).add_attachments(str(uuid.uuid4()), str(uuid.uuid4()), stored)

        assert recorded == 1
        params = database.cursor_.executed[0]
        assert params[5] == hashlib.sha256(files["a1"]).hexdigest()
        assert params[6] == stored[0]["s3_reference"]
        assert params[7] == "EXTRACTED"
        assert params[8] == stored[0]["extracted_text_uri"]

    def test_an_attachment_stored_before_extraction_existed_is_recorded_as_pending(self):
        """A message stuck from an earlier deployment is retried with the old shape."""
        old = {"name": "INV-10234.pdf", "size_bytes": 10, "sha256": "0" * 64, "s3_reference": "s3://b/k"}
        database = FakeDatabase()

        CaseRepository(database).add_attachments(str(uuid.uuid4()), str(uuid.uuid4()), [old])

        assert database.cursor_.executed[0][7:] == ("PENDING", None)


class TestHandingAttachmentsDownstream:
    def test_every_attachment_is_handed_on_not_only_the_first(self, stage2):
        files = {"a1": fixture("INV-10234.pdf"), "a2": fixture("statement_sept.pdf")}
        event = {"message_id": "m1",
                 "attachments": [attachment("a1", "INV-10234.pdf"), attachment("a2", "statement_sept.pdf")]}

        context = normalized_context(stage2(event, files), "case-1")

        assert [a["name"] for a in context["attachments"]] == ["INV-10234.pdf", "statement_sept.pdf"]
        assert all(a["extraction_status"] == "EXTRACTED" for a in context["attachments"])
        assert all(a["extracted_text_uri"] for a in context["attachments"])

    def test_only_references_are_handed_on(self, stage2):
        files = {"a1": fixture("INV-10234.pdf")}
        event = {"message_id": "m1", "attachments": [attachment("a1", "INV-10234.pdf")]}

        handed_on = normalized_context(stage2(event, files), "case-1")["attachments"][0]

        assert set(handed_on) == {"name", "content_type", "size_bytes", "sha256", "s3_reference",
                                  "extraction_status", "extracted_text_uri"}

    def test_a_message_without_attachments_hands_on_an_empty_list(self):
        assert normalized_context({"message_id": "m1"}, "case-1")["attachments"] == []
