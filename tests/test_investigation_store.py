from uuid import uuid4

from payment_query_platform.investigation import invoke_agent


class FakeCursor:
    def __init__(self):
        self.statement = None
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchone(self):
        return (1,)


class FakeDatabase:
    def __init__(self):
        self.fake_cursor = FakeCursor()

    def cursor(self):
        return self.fake_cursor


def _overall(**changes):
    overall = {
        "escalated": False,
        "itar_controlled": False,
        "helpdesk": "TCS Helpdesk",
        "summary": "Invoice paid.",
        "draft_reply": "Your invoice was paid.",
        "round": 1,
    }
    overall.update(changes)
    return overall


def _store(monkeypatch, overall, per_invoice):
    database = FakeDatabase()
    monkeypatch.setattr(invoke_agent, "_get_database", lambda: database)
    case_id = str(uuid4())
    invoke_agent.store({"case_id": case_id}, overall, per_invoice)
    return case_id, database.fake_cursor.statement, database.fake_cursor.parameters


def test_store_sets_case_outcome_for_a_single_invoice(monkeypatch):
    per_invoice = [{"invoice": "INV-1", "result": {"outcome": "PAID"}}]

    case_id, statement, parameters = _store(monkeypatch, _overall(), per_invoice)

    assert "update ps.case_header" in statement
    assert "insert into ps.case_event" in statement
    assert parameters[:6] == ("PENDING_REVIEW", "PAID", False, "TCS_HELPDESK", "Invoice paid.", case_id)
    assert parameters[6].obj["per_invoice"] == per_invoice


def test_store_keeps_outcomes_per_invoice_when_a_case_has_several(monkeypatch):
    per_invoice = [
        {"invoice": "INV-1", "result": {"outcome": "PAID"}},
        {"invoice": "INV-2", "result": {"outcome": "OVERDUE"}},
    ]

    _, _, parameters = _store(monkeypatch, _overall(), per_invoice)

    assert parameters[1] is None
    assert [i["result"]["outcome"] for i in parameters[6].obj["per_invoice"]] == ["PAID", "OVERDUE"]


def test_store_escalates_and_routes_export_controlled_cases_to_us_helpdesk(monkeypatch):
    overall = _overall(escalated=True, itar_controlled=True, helpdesk="US Helpdesk")

    _, _, parameters = _store(monkeypatch, overall, [{"invoice": "INV-1", "result": {"outcome": "PAID"}}])

    assert parameters[0] == "ESCALATED"
    assert parameters[2] is True
    assert parameters[3] == "US_HELPDESK"
