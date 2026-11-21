"""Tests for reading mail from CSV files in S3, and for the switch that chooses it."""

import importlib.util
import pathlib
from datetime import datetime, timezone

import pytest

from payment_query_platform.mailbox.categories import labels_enabled
from payment_query_platform.mailbox.graph_client import GraphApiError, normalize, thread_context
from payment_query_platform.mailbox.s3_mail import S3MailRepository, uses_s3

HEADER = "message_id,received_at,sender,subject,body,recipients,conversation_id,attachment_names\n"
LAMBDAS = pathlib.Path(__file__).resolve().parent.parent / "lambda"


def moment(hour, minute=0):
    return datetime(2026, 11, 17, hour, minute, tzinfo=timezone.utc)


class NoSuchKey(Exception):
    pass


class FakeS3:
    """Just enough of the S3 client: listing one folder level, and reading an object."""

    class exceptions:
        NoSuchKey = NoSuchKey

    def __init__(self, files):
        self.files = files  # key -> (put at, bytes)

    def get_paginator(self, _name):
        outer = self

        class Pages:
            def paginate(self, Bucket, Prefix, Delimiter):
                direct = [k for k in outer.files if k.startswith(Prefix) and Delimiter not in k[len(Prefix):]]
                yield {"Contents": [{"Key": k, "LastModified": outer.files[k][0]} for k in direct]}

        return Pages()

    def get_object(self, Bucket, Key):
        if Key not in self.files:
            raise NoSuchKey(Key)
        data = self.files[Key][1]
        return {"Body": type("Body", (), {"read": lambda self_: data})()}


def csv_file(*rows):
    return (HEADER + "".join(rows)).encode("utf-8")


def repo(files):
    return S3MailRepository("mail-bucket", "inbox/", client=FakeS3(files))


STATUS = 'm1,2026-11-17T09:00:00Z,vendor@example.com,Payment status,"Where is INV-12345?\nThanks",desk@example.com;ops@example.com,,\n'
WITH_PDF = "m2,2026-11-17T09:05:00Z,vendor@example.com,Invoice attached,See attached,,thread-7,invoice.pdf;note.txt\n"


@pytest.fixture(autouse=True)
def no_cutoff(monkeypatch):
    monkeypatch.delenv("INGESTION_CUTOFF_UTC", raising=False)
    monkeypatch.delenv("MAIL_SOURCE", raising=False)


class TestRowsReadAsGraphMail:
    def test_a_row_becomes_the_mail_the_pipeline_already_understands(self):
        source = repo({"inbox/a.csv": (moment(10), csv_file(STATUS))})
        ids, _ = source.changed_message_ids()
        raw = source.get_message(ids[0])
        message = normalize(raw)

        assert ids == ["m1"]
        assert (message.message_id, message.sender, message.subject) == ("m1", "vendor@example.com", "Payment status")
        assert message.recipient == "desk@example.com, ops@example.com"
        assert "INV-12345" in message.body
        assert thread_context(raw)["conversation_id"] == "m1"  # no thread given: the mail is its own case
        assert thread_context(raw)["has_attachment"] is False

    def test_attachment_names_become_the_attachment_list(self):
        source = repo({"inbox/a.csv": (moment(10), csv_file(WITH_PDF))})
        raw = source.get_message(source.changed_message_ids()[0][0])
        context = thread_context(raw)

        assert context["conversation_id"] == "thread-7"
        assert context["has_attachment"] is True
        assert [(a["attachment_id"], a["content_type"]) for a in context["attachments"]] == [
            ("invoice.pdf", "application/pdf"), ("note.txt", "text/plain")]

    def test_a_row_missing_a_required_column_is_left_out_and_the_rest_are_read(self):
        bad = "m3,2026-11-17T09:10:00Z,,No sender,Body,,,\n"
        source = repo({"inbox/a.csv": (moment(10), csv_file(bad, STATUS))})

        assert source.changed_message_ids()[0] == ["m1"]

    def test_a_row_with_an_unreadable_time_is_left_out(self):
        bad = "m3,yesterday,vendor@example.com,Subject,Body,,,\n"
        source = repo({"inbox/a.csv": (moment(10), csv_file(bad, STATUS))})

        assert source.changed_message_ids()[0] == ["m1"]


class TestOnlyNewFilesAreRead:
    def test_each_run_reads_only_the_files_newer_than_the_saved_position(self):
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS))}
        first_ids, position = repo(files).changed_message_ids()

        files["inbox/b.csv"] = (moment(11), csv_file(WITH_PDF))
        second_ids, newer = repo(files).changed_message_ids(position)
        third_ids, same = repo(files).changed_message_ids(newer)

        assert (first_ids, second_ids, third_ids) == (["m1"], ["m2"], [])
        assert same == newer and newer != position

    def test_the_first_run_with_no_files_keeps_a_position_that_reads_everything_later(self):
        ids, position = repo({}).changed_message_ids()
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS))}

        assert ids == []
        assert repo(files).changed_message_ids(position)[0] == ["m1"]

    def test_two_files_put_at_the_same_second_are_both_read(self):
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS)), "inbox/b.csv": (moment(10), csv_file(WITH_PDF))}
        ids, position = repo(files).changed_message_ids()

        assert sorted(ids) == ["m1", "m2"]
        assert repo(files).changed_message_ids(position)[0] == []

    def test_a_file_in_a_subfolder_or_that_is_not_csv_is_not_mail(self):
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS)),
                 "inbox/attachments/m1/x.csv": (moment(10), csv_file(WITH_PDF)),
                 "inbox/readme.txt": (moment(10), b"hello")}

        assert repo(files).changed_message_ids()[0] == ["m1"]

    def test_mail_before_the_ingestion_cutoff_is_not_read_but_the_position_still_moves(self, monkeypatch):
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-17T09:03:00Z")
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS, WITH_PDF))}
        ids, position = repo(files).changed_message_ids()

        assert ids == ["m2"]
        assert repo(files).changed_message_ids(position)[0] == []


class TestLookups:
    def test_a_mail_is_found_by_its_id_even_when_this_run_did_not_read_its_file(self):
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS))}

        assert repo(files).get_message("m1")["subject"] == "Payment status"

    def test_an_unknown_mail_is_a_permanent_not_found(self):
        with pytest.raises(GraphApiError) as error:
            repo({}).get_message("nope")

        assert error.value.status == 404 and error.value.is_permanent

    def test_an_attachment_is_read_from_the_mails_folder(self):
        files = {"inbox/attachments/m2/invoice.pdf": (moment(10), b"%PDF-1.4")}

        assert repo(files).get_attachment_bytes("m2", "invoice.pdf") == b"%PDF-1.4"

    def test_a_missing_attachment_is_a_permanent_not_found(self):
        with pytest.raises(GraphApiError) as error:
            repo({}).get_attachment_bytes("m2", "invoice.pdf")

        assert error.value.status == 404

    def test_mail_before_a_moment_is_listed_for_the_reconciler(self):
        files = {"inbox/a.csv": (moment(10), csv_file(STATUS, WITH_PDF))}
        source = repo(files)

        assert source.message_ids_before(moment(9, 3)) == ["m1"]
        assert source.message_ids_before(moment(10)) == ["m1", "m2"]
        assert source.message_ids_before(moment(10), since=moment(9, 3)) == ["m2"]


class TestTheSwitch:
    def test_outlook_through_graph_is_the_default(self):
        assert uses_s3() is False and labels_enabled() is True

    def test_s3_is_chosen_by_the_setting_and_turns_the_labels_off(self, monkeypatch):
        monkeypatch.setenv("MAIL_SOURCE", "S3")

        assert uses_s3() is True and labels_enabled() is False

    def test_an_unknown_value_is_an_error_not_a_silent_default(self, monkeypatch):
        monkeypatch.setenv("MAIL_SOURCE", "pop3")

        with pytest.raises(ValueError, match="graph or s3"):
            uses_s3()

    def test_the_reconciler_builds_the_s3_source_with_no_graph_settings(self, monkeypatch):
        from payment_query_platform.ingestion import ingest_mail

        monkeypatch.setenv("MAIL_SOURCE", "s3")
        monkeypatch.setenv("MAIL_S3_BUCKET", "mail-bucket")
        monkeypatch.delenv("GRAPH_SECRET_ID", raising=False)
        monkeypatch.delenv("GRAPH_API_BASE_URL", raising=False)
        source = ingest_mail.build_mailbox()

        assert isinstance(source, S3MailRepository)
        assert source.mailbox == "s3://mail-bucket/inbox/"

    def test_no_label_is_attempted_when_there_is_no_mailbox(self, monkeypatch):
        from payment_query_platform.intake import invoke_agents

        monkeypatch.setenv("MAIL_SOURCE", "s3")
        monkeypatch.setattr(invoke_agents, "load_credentials",
                            lambda *_: pytest.fail("Graph credentials were read with no mailbox"))

        assert invoke_agents._mark_in_outlook("m1", "AI Picked Up") is None


def load_lambda(folder):
    path = LAMBDAS / folder / "lambda_function.py"
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_") + "_lambda", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheDeployedLambdas:
    def test_email_ingestion_reads_s3_when_asked_and_needs_no_graph_settings(self, monkeypatch):
        from payment_query_platform.ingestion import ingest_to_sqs as module
        monkeypatch.setenv("MAIL_SOURCE", "s3")
        monkeypatch.setenv("MAIL_S3_BUCKET", "mail-bucket")
        monkeypatch.delenv("GRAPH_SECRET_ID", raising=False)

        assert module.build_mailbox().mailbox == "s3://mail-bucket/inbox/"

    def test_email_ingestion_still_needs_graph_settings_by_default(self, monkeypatch):
        from payment_query_platform.ingestion import ingest_to_sqs as module
        monkeypatch.delenv("GRAPH_SECRET_ID", raising=False)

        with pytest.raises(KeyError):
            module.build_mailbox("https://graph.microsoft.com/v1.0")

    def test_attachment_processing_reads_from_the_mails_s3_folder(self, monkeypatch):
        module = load_lambda("attachment-processing")
        monkeypatch.setenv("MAIL_SOURCE", "s3")
        monkeypatch.setenv("MAIL_S3_BUCKET", "mail-bucket")
        fake = FakeS3({"inbox/attachments/m2/invoice.pdf": (moment(10), b"%PDF-1.4")})
        monkeypatch.setattr(module.boto3, "client", lambda *_a, **_k: fake)

        assert module._build_mailbox().get_attachment_bytes("m2", "invoice.pdf") == b"%PDF-1.4"

    def test_attachment_processing_rejects_an_unknown_source(self, monkeypatch):
        module = load_lambda("attachment-processing")
        monkeypatch.setenv("MAIL_SOURCE", "pop3")

        with pytest.raises(ValueError, match="graph or s3"):
            module._build_mailbox()
