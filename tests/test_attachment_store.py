import hashlib

from payment_query_platform.ingestion import store_attachment


class FakeMailbox:
    def __init__(self, content):
        self.content = content

    def get_attachment_bytes(self, message_id, attachment_id):
        return self.content


class FakeStore:
    def put(self, **kwargs):
        return "s3://attachments/message/attachment"


def test_attachment_handler_returns_sha256_for_stored_bytes(monkeypatch):
    content = b"invoice attachment contents"
    monkeypatch.setattr(store_attachment, "_build_mailbox", lambda: FakeMailbox(content))
    monkeypatch.setattr(store_attachment, "_get_store", FakeStore)

    result = store_attachment.lambda_handler(
        {
            "message_id": "graph-message",
            "attachments": [{"attachment_id": "attachment-1", "name": "invoice.pdf"}],
        }
    )

    attachment = result["attachments"][0]
    assert attachment["size_bytes"] == len(content)
    assert attachment["sha256"] == hashlib.sha256(content).hexdigest()
    assert attachment["s3_reference"].startswith("s3://")


class FakeMailboxByAttachment:
    def __init__(self, files):
        self.files = files

    def get_attachment_bytes(self, message_id, attachment_id):
        return self.files[attachment_id]


def run(monkeypatch, files):
    monkeypatch.setattr(store_attachment, "_build_mailbox", lambda: FakeMailboxByAttachment(files))
    monkeypatch.setattr(store_attachment, "_get_store", FakeStore)
    return store_attachment.lambda_handler(
        {"message_id": "graph-message",
         "attachments": [{"attachment_id": a, "name": f"{a}.pdf"} for a in files]}
    )


def test_an_empty_attachment_is_skipped_and_the_others_are_kept(monkeypatch):
    result = run(monkeypatch, {"empty": b"", "real": b"invoice attachment contents"})

    assert [a["attachment_id"] for a in result["attachments"]] == ["real"]
    assert result["attachment_s3_reference"] == result["attachments"][0]["s3_reference"]


def test_a_mail_whose_only_attachment_is_empty_goes_on_without_attachments(monkeypatch):
    result = run(monkeypatch, {"empty": b""})

    assert result["attachments"] == []
    assert result["attachment_s3_reference"] is None
