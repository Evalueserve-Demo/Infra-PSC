"""Tests for how the mailbox reader recovers when Graph rejects the saved sync state."""

import pytest

from payment_query_platform.mailbox.graph_client import GraphApiError, GraphApiRepository, cutoff_from_env

SAVED = "https://graph.example/delta?$deltatoken=old"


class FakeToken:
    def token(self):
        return "t"


def start_url():
    """Where a fresh enumeration of the inbox begins."""
    return GraphApiRepository("https://graph.example", "box@example.com", FakeToken()).delta_url()


def repository(pages):
    """A reader whose Graph calls are answered from `pages`, keyed by URL."""
    repo = GraphApiRepository("https://graph.example", "box@example.com", FakeToken())
    calls = []

    def get(url):
        calls.append(url)
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    repo._get = get
    return repo, calls


def fresh_page(*ids, link="https://graph.example/delta?$deltatoken=new"):
    return {"value": [{"id": i} for i in ids], "@odata.deltaLink": link}


class TestPaging:
    def test_page_size_is_a_preference_not_a_cap_on_the_whole_read(self):
        repo = GraphApiRepository("https://graph.example", "box@example.com", FakeToken(), page_size=50)
        assert "$top" not in repo.delta_url()
        assert repo._request(repo.delta_url()).get_header("Prefer") == "odata.maxpagesize=50"


class TestSyncStateRecovery:
    def test_a_rejected_saved_state_restarts_from_the_beginning(self):
        fresh = start_url()
        repo, calls = repository({
            SAVED: GraphApiError("SyncStateNotFound", status=410),
            fresh: fresh_page("m1", "m2"),
        })

        ids, state = repo.changed_message_ids(SAVED)

        assert ids == ["m1", "m2"]
        assert state.endswith("new")
        assert calls == [SAVED, fresh]

    def test_a_valid_saved_state_is_used_as_it_is(self):
        repo, calls = repository({SAVED: fresh_page("m3")})
        assert repo.changed_message_ids(SAVED)[0] == ["m3"]
        assert calls == [SAVED]

    def test_a_first_run_with_no_saved_state_reads_from_the_beginning(self):
        start = start_url()
        repo, calls = repository({start: fresh_page("m1")})
        assert repo.changed_message_ids(None)[0] == ["m1"]
        assert calls == [start]

    def test_other_errors_are_not_treated_as_an_expired_state(self):
        repo, calls = repository({SAVED: GraphApiError("unreachable")})
        with pytest.raises(GraphApiError):
            repo.changed_message_ids(SAVED)
        assert calls == [SAVED]

    def test_a_throttled_or_forbidden_reply_is_not_retried_from_the_beginning(self):
        repo, calls = repository({SAVED: GraphApiError("forbidden", status=403)})
        with pytest.raises(GraphApiError):
            repo.changed_message_ids(SAVED)
        assert calls == [SAVED]

    def test_a_fresh_start_that_also_fails_is_reported_not_looped(self):
        start = start_url()
        repo, calls = repository({
            SAVED: GraphApiError("gone", status=410),
            start: GraphApiError("gone", status=410),
        })
        with pytest.raises(GraphApiError):
            repo.changed_message_ids(SAVED)
        assert calls == [SAVED, start]


class TestMessageIdsBefore:
    def test_it_lists_every_page_of_the_mails_received_before_the_moment(self):
        from datetime import datetime, timezone
        repo = GraphApiRepository("https://graph.example", "box@example.com", FakeToken())
        asked = []
        pages = {0: {"value": [{"id": "a"}, {"id": "b"}], "@odata.nextLink": "https://graph.example/next"},
                 1: {"value": [{"id": "c"}]}}

        def get(url):
            asked.append(url)
            return pages[len(asked) - 1]

        repo._get = get
        ids = repo.message_ids_before(datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc))

        assert ids == ["a", "b", "c"]
        assert "receivedDateTime%20lt%202026-11-02T13%3A00%3A00Z" in asked[0]
        assert asked[1] == "https://graph.example/next"

    def test_with_a_start_it_lists_only_the_mails_received_from_then_on(self):
        from datetime import datetime, timezone
        repo = GraphApiRepository("https://graph.example", "box@example.com", FakeToken())
        asked = []
        repo._get = lambda url: asked.append(url) or {"value": []}

        repo.message_ids_before(datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc),
                                since=datetime(2026, 11, 1, tzinfo=timezone.utc))

        assert "receivedDateTime%20ge%202026-11-01T00%3A00%3A00Z%20and%20receivedDateTime%20lt" in asked[0]


def dated_page(*mails):
    """A delta page of (id, receivedDateTime) pairs."""
    return {"value": [{"id": i, "receivedDateTime": received} for i, received in mails],
            "@odata.deltaLink": "https://graph.example/delta?$deltatoken=new"}


class TestIngestionCutoff:
    def test_mail_received_before_the_cutoff_is_left_out(self, monkeypatch):
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-12T22:30:00Z")
        repo, _ = repository({start_url(): dated_page(
            ("old", "2026-11-12T22:29:59Z"), ("exactly", "2026-11-12T22:30:00Z"), ("new", "2026-11-13T08:00:00Z"))})

        ids, state = repo.changed_message_ids(None)

        assert ids == ["exactly", "new"]
        assert state.endswith("new")  # the sync position still moves on past the old mail

    def test_without_a_cutoff_every_changed_mail_is_read(self, monkeypatch):
        monkeypatch.delenv("INGESTION_CUTOFF_UTC", raising=False)
        repo, _ = repository({start_url(): dated_page(("a", "2020-01-01T00:00:00Z"), ("b", "2026-11-13T08:00:00Z"))})

        assert repo.changed_message_ids(None)[0] == ["a", "b"]

    def test_times_in_other_offsets_are_compared_correctly(self, monkeypatch):
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-12T22:30:00Z")
        repo, _ = repository({start_url(): dated_page(
            ("before", "2026-11-13T03:59:00+05:30"), ("after", "2026-11-13T04:01:00+05:30"))})

        assert repo.changed_message_ids(None)[0] == ["after"]

    def test_a_mail_with_no_received_time_is_an_error_and_not_a_quiet_skip(self, monkeypatch):
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-12T22:30:00Z")
        repo, _ = repository({start_url(): {"value": [{"id": "x"}], "@odata.deltaLink": "d"}})

        with pytest.raises(GraphApiError):
            repo.changed_message_ids(None)

    def test_a_cutoff_without_a_time_zone_is_rejected(self, monkeypatch):
        monkeypatch.setenv("INGESTION_CUTOFF_UTC", "2026-11-12T22:30:00")

        with pytest.raises(ValueError):
            cutoff_from_env()
