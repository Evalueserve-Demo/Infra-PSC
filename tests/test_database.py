from uuid import uuid4
from datetime import datetime, timezone
import hashlib

import pytest

from payment_query_platform.storage.database import (
    AgentCallLogRepository,
    CaseRepository,
    IdentifierRepository,
    MessageRepository,
    SyncStateRepository,
)


class FakeCursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))

    def fetchone(self):
        return self.responses.pop(0)


class FakeDatabase:
    def __init__(self, responses):
        self.fake_cursor = FakeCursor(responses)

    def cursor(self):
        return self.fake_cursor


def test_open_case_returns_database_generated_uuid():
    case_id = uuid4()
    database = FakeDatabase([(case_id,)])

    result = CaseRepository(database).open_case("graph-conversation-1")

    assert result == str(case_id)
    assert "insert into ps.case_header" in database.fake_cursor.executed[0][0]


def test_add_message_returns_existing_message_on_retry():
    case_id = uuid4()
    message_id = uuid4()
    database = FakeDatabase([None, (message_id, case_id)])
    message = {
        "internet_message_id": "<message@example.com>",
        "message_id": "graph-message-1",
        "in_reply_to_message_id": "<parent@example.com>",
        "sender": "supplier@example.com",
        "recipient": "helpdesk@example.com",
        "subject": "Invoice status",
        "body": "Invoice INV-12345",
        "body_hash": "a" * 64,
        "received_at": "2026-10-12T09:00:00Z",
    }

    result = CaseRepository(database).add_message(str(case_id), message)

    assert result == str(message_id)
    assert len(database.fake_cursor.executed) == 2
    assert "ps.case_message" in database.fake_cursor.executed[0][0]


def test_add_message_rejects_internet_id_already_linked_to_another_case():
    case_id = uuid4()
    other_case_id = uuid4()
    database = FakeDatabase([None, (uuid4(), other_case_id)])
    message = {
        "internet_message_id": "<message@example.com>",
        "body_hash": "a" * 64,
        "received_at": "2026-10-12T09:00:00Z",
    }

    with pytest.raises(RuntimeError, match="another payment case"):
        CaseRepository(database).add_message(str(case_id), message)


def test_save_candidates_maps_email_sources_and_invoice_format_status():
    database = FakeDatabase([(uuid4(),), (uuid4(),)])
    repository = IdentifierRepository(database)

    inserted = repository.save_candidates(
        str(uuid4()),
        [
            {
                "invoice_number": "INV-12345",
                "po_number": "4500123456",
                "found_in": "EMAIL_SUBJECT",
            }
        ],
    )

    assert inserted == 2
    invoice_parameters = database.fake_cursor.executed[0][1]
    po_parameters = database.fake_cursor.executed[1][1]
    assert invoice_parameters[1:] == ("INVOICE", "INV-12345", "INV-12345", "SUBJECT", "VALID")
    assert po_parameters[1:] == ("PO", "4500123456", "4500123456", "SUBJECT", "PENDING")


def test_save_candidates_rejects_unknown_provenance():
    repository = IdentifierRepository(FakeDatabase([]))

    with pytest.raises(ValueError, match="recognized found_in source"):
        repository.save_candidates(
            str(uuid4()), [{"invoice_number": "INV-12345", "found_in": "UNKNOWN"}]
        )


def test_agent_call_log_records_prompt_hash_and_json_payloads():
    case_id = uuid4()
    database = FakeDatabase([(73,)])

    call_id = AgentCallLogRepository(database).record(
        case_id=str(case_id),
        component="intake",
        invoked_by="email-intake-agents",
        operation="classification",
        model_id="model-test",
        system_prompt="Classify this email.",
        user_input="Subject: status\n\nINV-12345",
        structured_output_instruction="Return JSON.",
        output_schema="Classification",
        outcome="SUCCESS",
        latency_ms=125,
        response_payload={"classification": {"request_type": "PAYMENT_STATUS_REQUEST"}},
    )

    assert call_id == 73
    statement, parameters = database.fake_cursor.executed[0]
    assert "insert into ps.integration_call_log" in statement
    assert parameters[1:5] == ("email-intake-agents", "classification", "SUCCESS", 125)
    assert len(parameters[6]) == 64
    assert parameters[7].obj["user_input"] == "Subject: status\n\nINV-12345"
    assert parameters[7].obj["output_schema"] == "Classification"
    assert parameters[8].obj["classification"]["request_type"] == "PAYMENT_STATUS_REQUEST"
    assert parameters[-1] == case_id


def test_get_staged_message_restores_full_body_and_hash():
    database = FakeDatabase(
        [
            (
                "<internet@example.com>",
                "<parent@example.com>",
                "conversation-1",
                "supplier@example.com",
                "helpdesk@example.com",
                "Invoice status",
                "Full normalized body",
                False,
                datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc),
            )
        ]
    )

    message = MessageRepository(database).get_staged_message("graph-id")

    assert message["internet_message_id"] == "<internet@example.com>"
    assert message["in_reply_to_message_id"] == "<parent@example.com>"
    assert message["body"] == "Full normalized body"
    assert message["body_hash"] == hashlib.sha256(b"Full normalized body").hexdigest()
    assert message["received_at"] == "2026-10-12T09:00:00+00:00"


def test_mark_ready_does_not_write_legacy_case_foreign_key():
    database = FakeDatabase([None])

    MessageRepository(database).mark_ready("graph-id")

    statement, parameters = database.fake_cursor.executed[0]
    assert "case_id" not in statement
    assert parameters == ("READY_FOR_DOWNSTREAM", "graph-id")


def test_staging_queries_use_the_ps_schema_and_no_case_id():
    database = FakeDatabase([("RECEIVED",)])
    repository = MessageRepository(database)

    assert repository.get("graph-id") == {"status": "RECEIVED"}

    statements = [
        repository.SELECT,
        repository.INSERT,
        repository.STAGED_MESSAGE,
        SyncStateRepository.SELECT,
        SyncStateRepository.UPSERT,
        database.fake_cursor.executed[0][0],
    ]
    for statement in statements:
        assert "integration." not in statement
        assert "case_id" not in statement
    assert "ps.messages" in repository.INSERT
    assert "ps.graph_sync_state" in SyncStateRepository.UPSERT


def test_add_attachments_uses_ps_message_uuid_and_sha256():
    case_id = uuid4()
    message_id = uuid4()
    database = FakeDatabase([(uuid4(),)])

    inserted = CaseRepository(database).add_attachments(
        str(case_id),
        str(message_id),
        [
            {
                "name": "invoice.pdf",
                "content_type": "application/pdf",
                "size_bytes": 128,
                "sha256": "b" * 64,
                "s3_reference": "s3://bucket/invoice.pdf",
            }
        ],
    )

    parameters = database.fake_cursor.executed[0][1]
    assert inserted == 1
    assert parameters[:2] == (case_id, message_id)
    assert parameters[5:7] == ("b" * 64, "s3://bucket/invoice.pdf")
    assert parameters[7:] == ("PENDING", None)