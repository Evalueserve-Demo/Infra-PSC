"""Tests for how the reconciler decides where a mail stopped and what to do about it."""

import json

import pytest

from payment_query_platform.mailbox.graph_client import GraphApiError
from payment_query_platform.reconciliation import reconcile
from payment_query_platform.reconciliation.reconcile import (
    MAX_ATTEMPTS, NO_CASE, UNCLASSIFIED, UNINVESTIGATED, plan, stage, with_attachments,
)


def mail(case_id="c1", classified=True, payment=False, investigated=False, attempts=0):
    return {"message_id": "m", "attempts": attempts, "case_id": case_id,
            "classified": classified, "payment": payment, "investigated": investigated}


class TestStage:
    def test_a_mail_with_no_case_stopped_before_workflow_1_finished(self):
        assert stage(mail(case_id=None, classified=False)) == NO_CASE

    def test_a_case_that_was_never_classified_stopped_in_workflow_2(self):
        assert stage(mail(classified=False)) == UNCLASSIFIED

    def test_a_payment_mail_that_was_never_investigated_stopped_later(self):
        assert stage(mail(payment=True)) == UNINVESTIGATED

    def test_a_classified_mail_that_is_not_for_the_desk_is_finished(self):
        assert stage(mail(payment=False)) is None

    def test_an_investigated_payment_mail_is_finished(self):
        assert stage(mail(payment=True, investigated=True)) is None


class TestPlan:
    def test_finished_mails_are_left_alone(self):
        gaps, retry, escalate = plan([mail(), mail(payment=True, investigated=True)], limit=10)
        assert (gaps, retry, escalate) == ([], [], [])

    def test_unfinished_mails_are_retried_oldest_first_up_to_the_limit(self):
        rows = [{**mail(classified=False), "message_id": str(i)} for i in range(5)]
        _, retry, _ = plan(rows, limit=3)
        assert [row["message_id"] for row, _ in retry] == ["0", "1", "2"]

    def test_a_mail_that_used_all_its_attempts_is_escalated_not_retried(self):
        gaps, retry, escalate = plan([mail(classified=False, attempts=MAX_ATTEMPTS)], limit=10)
        assert len(gaps) == 1 and retry == [] and len(escalate) == 1

    def test_escalations_do_not_use_up_the_retry_limit(self):
        rows = [mail(classified=False, attempts=MAX_ATTEMPTS), mail(classified=False)]
        _, retry, escalate = plan(rows, limit=1)
        assert len(retry) == 1 and len(escalate) == 1


class FakeMailbox:
    """Answers get_message as Graph would, with the attachment list expanded."""

    def __init__(self, failing=False):
        self.failing = failing
        self.asked = []

    def get_message(self, message_id):
        self.asked.append(message_id)
        if self.failing:
            raise GraphApiError("unreachable")
        return {"conversationId": "c1", "attachments": [
            {"id": "a1", "name": "INV-10234.pdf", "contentType": "application/pdf", "size": 10, "isInline": False}]}


class TestWithAttachments:
    def test_a_mail_restarting_workflow_1_gets_its_attachment_list_back(self):
        """Its Aurora row says only that there are attachments, not which."""
        detail = with_attachments(FakeMailbox(), {"message_id": "m1", "has_attachment": True})

        assert [a["name"] for a in detail["attachments"]] == ["INV-10234.pdf"]

    def test_a_mail_without_attachments_is_restarted_without_asking_graph(self):
        mailbox = FakeMailbox()

        assert with_attachments(mailbox, {"message_id": "m1", "has_attachment": False}) == \
            {"message_id": "m1", "has_attachment": False}
        assert mailbox.asked == []

    def test_a_mail_whose_attachments_cannot_be_listed_is_not_restarted(self):
        """Restarting it without them would lose the attachments for good."""
        assert with_attachments(FakeMailbox(failing=True), {"message_id": "m1", "has_attachment": True}) is None


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        pass


class FakeDatabase:
    def cursor(self):
        return FakeCursor()


@pytest.fixture
def retry(monkeypatch):
    """Run _retry with the database, Workflow 1 and Queue 1 faked; returns what was sent where."""
    sent = {"workflow": [], "queue": []}
    stored = [{"name": "INV-10234.pdf", "s3_reference": "s3://b/messages/x/INV-10234.pdf",
               "extraction_status": "EXTRACTED", "extracted_text_uri": "s3://b/text/abc.txt"}]
    monkeypatch.setattr(reconcile, "_get_database", lambda: FakeDatabase())
    monkeypatch.setattr(reconcile.MessageRepository, "get_staged_message",
                        lambda self, message_id: {"message_id": message_id, "has_attachment": True,
                                                  "subject": "Payment query", "body": "See attached"})
    monkeypatch.setattr(reconcile.CaseRepository, "attachments_of", lambda self, message_id: stored)
    monkeypatch.setattr(reconcile, "start_step_function", lambda arn, detail: sent["workflow"].append(detail))

    class FakeSqs:
        def send_message(self, QueueUrl, MessageBody):
            sent["queue"].append(json.loads(MessageBody))

    monkeypatch.setattr(reconcile.boto3, "client", lambda *a, **k: FakeSqs())

    def run(where, mailbox=None):
        reconcile._retry(mail(), where, "arn:sf1", "https://queue", mailbox or FakeMailbox())
        return sent

    return run


class TestRetryKeepsAttachments:
    def test_a_mail_with_no_case_restarts_workflow_1_with_its_attachments(self, retry):
        sent = retry(NO_CASE)

        assert [a["name"] for a in sent["workflow"][0]["attachments"]] == ["INV-10234.pdf"]

    @pytest.mark.parametrize("where", [UNCLASSIFIED, UNINVESTIGATED])
    def test_a_mail_sent_back_to_queue_1_carries_its_stored_attachments(self, retry, where):
        """Stage 2 already read them; the agents must see their text again."""
        sent = retry(where)

        handed_on = sent["queue"][0]["attachments"]
        assert handed_on[0]["extracted_text_uri"] == "s3://b/text/abc.txt"
        assert handed_on[0]["extraction_status"] == "EXTRACTED"

    def test_a_mail_whose_attachments_cannot_be_listed_is_not_restarted(self, retry):
        sent = retry(NO_CASE, FakeMailbox(failing=True))

        assert sent["workflow"] == []


class TestMissed:
    class Mailbox:
        def __init__(self, ids):
            self.ids, self.asked = ids, []

        def message_ids_before(self, moment, since=None):
            self.asked.append(moment)
            self.since = since
            return self.ids

    def test_a_mail_the_last_read_should_have_saved_but_did_not_is_missed(self):
        from datetime import datetime, timedelta, timezone
        read = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
        mailbox = self.Mailbox(["saved", "lost"])

        assert reconcile.missed(mailbox, {"saved"}, read) == {"lost"}
        assert mailbox.asked == [read - reconcile.READ_MARGIN]

    def test_before_any_read_nothing_is_missed(self):
        mailbox = self.Mailbox(["a"])
        assert reconcile.missed(mailbox, set(), None) == set()
        assert mailbox.asked == []

    def test_mail_older_than_the_ingestion_cutoff_is_not_looked_for(self):
        from datetime import datetime, timezone
        read = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
        cutoff = datetime(2026, 11, 1, tzinfo=timezone.utc)
        mailbox = self.Mailbox([])

        reconcile.missed(mailbox, set(), read, cutoff)

        assert mailbox.since == cutoff

    def test_the_cutoff_comes_from_the_environment(self, monkeypatch):
        from datetime import datetime, timezone
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-12T22:30:00Z")
        assert reconcile.cutoff_from_env() == datetime(2026, 11, 12, 22, 30, tzinfo=timezone.utc)

        monkeypatch.delenv("INGESTION_CUTOFF_UTC")
        assert reconcile.cutoff_from_env() is None
