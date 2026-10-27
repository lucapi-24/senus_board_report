import asyncio
import json

from app.services.llm_extract import (
    StatementLine,
    extract_structured,
    ground_items,
    normalize_payload,
    normalize_payload_with_discarded,
    parse_value,
)


def test_parse_value_handles_report_formats():
    assert parse_value("669,550") == 669550.0
    assert parse_value("€64,861") == 64861.0
    assert parse_value("(483,753)") == -483753.0
    assert parse_value("-1,036") == -1036.0
    assert parse_value(836991) == 836991.0
    assert parse_value("") is None
    assert parse_value(None) is None


def test_normalize_payload_reads_the_three_statements():
    payload = {
        "statements": {
            "pnl": [{"label": "Revenue", "amount": "354,813", "page": 5, "source_text": "Revenue 354,813"}],
            "balance_sheet": [{"line_item": "Goodwill", "value": 669550, "page": 5}],
            "cash_flow": [],
        }
    }
    lines = normalize_payload(payload)
    by_key = {(line.statement, line.line_item): line for line in lines}
    assert by_key[("pnl", "Revenue")].value == 354813.0
    assert by_key[("pnl", "Revenue")].page == 5
    assert by_key[("balance_sheet", "Goodwill")].value == 669550.0


def test_normalization_records_discarded_entries_with_reasons():
    lines, discarded = normalize_payload_with_discarded(
        {
            "pnl": [
                {"line_item": "Revenue", "value": "not a number", "page": 5},
                {"line_item": "Unpaged", "value": 20},
                {"value": 30, "page": 5},
                "invalid entry",
            ]
        }
    )

    assert lines == []
    assert [entry["reason"] for entry in discarded] == [
        "invalid_value",
        "invalid_page",
        "missing_label",
        "entry_not_object",
    ]


def test_ground_items_accepts_values_present_on_their_page():
    pages = [(5, "Revenue 354,813\nGoodwill 669,550")]
    lines = [
        StatementLine("pnl", "Revenue", 354813.0, 5),
        StatementLine("balance_sheet", "Goodwill", 669550.0, 5),
    ]
    grounded, ungrounded = ground_items(pages, lines)
    assert len(grounded) == 2
    assert ungrounded == []


def test_ground_items_ignores_sign_and_separators():
    pages = [(6, "Operating loss ......... (483,753)")]
    (line,) = [StatementLine("pnl", "Group operating loss", -483753.0, 6)]
    grounded, ungrounded = ground_items(pages, [line])
    assert grounded and not ungrounded


def test_ground_items_rejects_hallucinated_figures():
    pages = [(5, "Revenue 354,813")]
    lines = [
        StatementLine("pnl", "Revenue", 354813.0, 5),
        StatementLine("pnl", "Invented", 999999.0, 5),
        StatementLine("pnl", "Wrong page", 354813.0, 6),
    ]
    grounded, ungrounded = ground_items(pages, lines)
    assert [l.line_item for l in grounded] == ["Revenue"]
    assert {x.line_item for x in ungrounded} == {"Invented", "Wrong page"}


def _fake_client(content: str, errors=None):
    """Stub returning a fixed payload, so no network is touched."""

    class _Message:
        def __init__(self):
            self.content = content

    class _Choice:
        def __init__(self):
            self.message = _Message()

    class _Response:
        def __init__(self):
            self.choices = [_Choice()]

    class _Completions:
        calls = 0

        async def create(self, *args, **kwargs):
            self.calls += 1
            if errors:
                error = errors.pop(0)
                raise error
            return _Response()

    class _Chat:
        completions = _Completions()

    client = type("Client", (), {"chat": _Chat()})()
    client.calls = lambda: client.chat.completions.calls
    return client


def test_extract_structured_returns_only_grounded_lines():
    content = json.dumps(
        {
            "pnl": [
                {"line_item": "Revenue", "value": "354,813", "page": 5, "source_text": "Revenue 354,813"},
                {"line_item": "Made up", "value": "999,999", "page": 5, "source_text": ""},
            ]
        }
    )
    pages = [(5, "Revenue 354,813")]
    result = asyncio.run(extract_structured(pages, "HY2026", client=_fake_client(content)))
    assert [line.line_item for line in result.lines] == ["Revenue"]
    assert result.lines[0].grounded is True
    assert result.trace["accepted_count"] == 1
    assert result.trace["discarded_lines"] == [
        {
            "statement": "pnl",
            "line_item": "Made up",
            "value": 999999.0,
            "page": 5,
            "reason": "value_not_found_on_claimed_page",
        }
    ]


def test_413_is_not_retried_and_falls_back_to_regex():
    class ProviderError(Exception):
        status_code = 413

    client = _fake_client("", [ProviderError("request too large")])

    result = asyncio.run(
        extract_structured(
            [(5, "Revenue 100")],
            "HY2026",
            client=client,
            sleep=_no_sleep,
        )
    )

    assert client.calls() == 1
    assert result.lines == []
    assert result.trace["status"] == "regex_only"
    assert result.trace["error_code"] == "413"


def test_429_waits_for_retry_after_before_retrying():
    class ProviderError(Exception):
        status_code = 429

        def __init__(self):
            self.response = type("Response", (), {"headers": {"retry-after": "7"}})()

    client = _fake_client(
        json.dumps({"pnl": [{"line_item": "Revenue", "value": 100, "page": 5}]}),
        [ProviderError()],
    )
    waits = []

    async def record_wait(seconds):
        waits.append(seconds)

    result = asyncio.run(
        extract_structured(
            [(5, "Revenue 100")],
            "HY2026",
            client=client,
            sleep=record_wait,
        )
    )

    assert waits == [7.0]
    assert client.calls() == 2
    assert result.lines[0].line_item == "Revenue"


def test_invalid_json_falls_back_without_retrying():
    client = _fake_client("{not valid json")

    result = asyncio.run(
        extract_structured(
            [(5, "Revenue 100")],
            "HY2026",
            client=client,
            sleep=_no_sleep,
        )
    )

    assert client.calls() == 1
    assert result.trace["status"] == "regex_only"
    assert result.trace["error_code"] == "invalid_json"


def test_partial_response_is_warned_and_reported():
    client = _fake_client(
        json.dumps({"pnl": [{"line_item": "Revenue", "value": 100, "page": 5}]})
    )

    result = asyncio.run(
        extract_structured(
            [(5, "Revenue 100")],
            "HY2026",
            client=client,
            expected_count=2,
            sleep=_no_sleep,
        )
    )

    assert result.trace["status"] == "partial"
    assert "1 partidas fundamentadas de 2 esperadas" in result.trace["warning"]


async def _no_sleep(_seconds):
    return None
