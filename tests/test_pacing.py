"""Tests for how ingestion paces the workflows it starts."""

from payment_query_platform.ingestion import ingest_mail


class Context:
    def __init__(self, remaining_ms):
        self._remaining = list(remaining_ms)

    def get_remaining_time_in_millis(self):
        return self._remaining.pop(0) if len(self._remaining) > 1 else self._remaining[0]


def mails(n):
    return [{"message_id": f"m{i}"} for i in range(n)]


def run(monkeypatch, details, interval=1.0, context=None, fail=()):
    started, pauses = [], []

    def start(arn, detail):
        if detail["message_id"] in fail:
            raise RuntimeError("refused")
        started.append(detail["message_id"])

    monkeypatch.setattr(ingest_mail, "start_step_function", start)
    monkeypatch.setattr(ingest_mail.time, "sleep", pauses.append)
    result = ingest_mail.start_workflows("arn", details, interval, context)
    return result, started, pauses


def test_each_workflow_is_started_with_a_pause_after_it(monkeypatch):
    (count, failures), started, pauses = run(monkeypatch, mails(3), interval=1.5)
    assert (count, failures, started) == (3, [], ["m0", "m1", "m2"])
    assert pauses == [1.5, 1.5, 1.5]


def test_a_refused_start_is_reported_and_the_rest_carry_on(monkeypatch):
    (count, failures), started, _ = run(monkeypatch, mails(3), fail={"m1"})
    assert count == 2 and started == ["m0", "m2"]
    assert failures[0]["message_id"] == "m1"


def test_it_stops_before_the_lambda_runs_out_of_time(monkeypatch):
    """The mails not started stay RECEIVED, and the reconciler starts them."""
    (count, _), started, _ = run(monkeypatch, mails(5), context=Context([200_000, 100_000, 10_000]))
    assert started == ["m0", "m1"] and count == 2
