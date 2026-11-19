from uuid import uuid4

import pytest

from payment_query_platform.ingestion import open_case


class FakeMessages:
    def __init__(self, _database, staged_message):
        self.staged_message = staged_message
        self.marked_ready = None

    def get(self, message_id):
        return {"status": "RECEIVED", "case_id": None}

    def get_staged_message(self, message_id):
        return self.staged_message

    def mark_ready(self, message_id):
        self.marked_ready = message_id


class FakeCases:
    def __init__(self, _database, case_id, message_id):
        self.case_id = case_id
        self.message_id = message_id
        self.added_message = None
        self.attachment_args = None

    def find_by_conversation(self, _conversation_id):
        return self.case_id

    def add_message(self, case_id, message):
        self.added_message = (case_id, message)
        return self.message_id

    def add_attachments(self, case_id, message_id, attachments):
        self.attachment_args = (case_id, message_id, attachments)
        return len(attachments)


class FakeSqs:
    def __init__(self):
        self.sent = None

    def send_message(self, **kwargs):
        self.sent = kwargs


@pytest.fixture
def stage2(monkeypatch):
    case_id = str(uuid4())
    ps_message_id = str(uuid4())
    staged = {
        "message_id": "graph-message-id",
        "internet_message_id": "<internet@example.com>",
        "in_reply_to_message_id": None,
        "conversation_id": "thread-1",
        "sender": "supplier@example.com",
        "recipient": "helpdesk@example.com",
        "subject": "Invoice status",
        "body": "The complete source message",
        "body_hash": "a" * 64,
        "has_attachment": True,
        "received_at": "2026-10-12T09:00:00+00:00",
    }
    messages = FakeMessages(None, staged)
    cases = FakeCases(None, case_id, ps_message_id)
    sqs = FakeSqs()
    monkeypatch.setattr(open_case, "_get_database", lambda: object())
    monkeypatch.setattr(open_case, "MessageRepository", lambda _db: messages)
    monkeypatch.setattr(open_case, "CaseRepository", lambda _db: cases)
    monkeypatch.setattr(open_case.boto3, "client", lambda *_args, **_kwargs: sqs)
    monkeypatch.setenv("SQS1_QUEUE_URL", "https://sqs.example/queue")
    return case_id, ps_message_id, staged, messages, cases, sqs


def test_stage2_persists_staged_source_and_publishes_uuid_case(stage2):
    case_id, ps_message_id, staged, messages, cases, sqs = stage2
    event = {
        "message_id": "graph-message-id",
        "conversation_id": "thread-1",
        "subject": "Invoice status",
        "body": "Capped event payload",
        "attachments": [{"s3_reference": "s3://bucket/file", "sha256": "b" * 64}],
    }

    result = open_case.lambda_handler(event)

    assert cases.added_message == (case_id, staged)
    assert cases.attachment_args == (case_id, ps_message_id, event["attachments"])
    assert messages.marked_ready == "graph-message-id"
    assert result["case_id"] == case_id
    assert result["body"] == "Capped event payload"
    assert sqs.sent["QueueUrl"] == "https://sqs.example/queue"


def test_stage2_stops_for_legacy_retry_without_internet_message_id(stage2):
    _, _, staged, _, _, sqs = stage2
    staged["internet_message_id"] = None
    event = {"message_id": "graph-message-id", "conversation_id": "thread-1"}

    with pytest.raises(ValueError, match="internet message ID"):
        open_case.lambda_handler(event)

    assert sqs.sent is None
