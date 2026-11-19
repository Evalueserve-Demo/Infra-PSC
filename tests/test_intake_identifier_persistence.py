from payment_query_platform.intake import invoke_agents


class FakeIdentifierRepository:
    def __init__(self, database):
        self.database = database
        self.saved = None

    def save_candidates(self, case_id, candidates):
        self.saved = (case_id, candidates)
        return len(candidates)


def test_extraction_persists_candidates_and_returns_existing_payload(monkeypatch):
    repository = FakeIdentifierRepository(object())
    monkeypatch.setattr(invoke_agents, "_get_database", lambda: repository.database)
    monkeypatch.setattr(invoke_agents, "IdentifierRepository", lambda _db: repository)
    monkeypatch.setattr(
        invoke_agents,
        "invoke_runtime",
        lambda *_args: {
            "extraction": {
                "payment_info": [
                    {
                        "invoice_number": "INV-12345",
                        "po_number": "4500123456",
                        "found_in": "EMAIL_SUBJECT",
                    }
                ]
            }
        },
    )

    result = invoke_agents.lambda_handler(
        {
            "role": "extraction",
            "case": {
                "case_id": "9de3f28a-324a-4b25-b1c6-bcf046db6232",
                "message_id": "graph-id",
                "subject": "INV-12345",
                "body": "Why is it unpaid?",
            },
        }
    )

    assert repository.saved[0] == "9de3f28a-324a-4b25-b1c6-bcf046db6232"
    assert repository.saved[1][0]["invoice_number"] == "INV-12345"
    assert result["identifiers"]["invoice_number"] == "INV-12345"
