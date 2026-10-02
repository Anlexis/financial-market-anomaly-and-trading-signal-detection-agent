# Output-boundary containment: what a caller receives when the output gate
# rejects a report.
#
# Rejecting is not the same as withholding. The outward response is assembled
# from the state the graph ends in, and it falls back to the `result` field
# whenever `formatted_output` is empty — regardless of status. So an output
# gate that returns ERROR without clearing state still delivers the exact
# report it refused to release, inside an error envelope.
#
# For this agent that failure mode is the worst one available: the invariant
# the gate enforces is the [DRAFT] label and the filing-prohibition reminder on
# a suspicious-activity report, so a containment failure ships an UNLABELLED
# regulatory draft to the caller.
#
# These tests drive real rejections and assert on what actually leaves the
# agent: no report text, no traceback, no source paths — and a clean-path
# control proving the gate releases legitimate reports rather than refusing
# everything.

import json
import os

import pytest
from fastapi.testclient import TestClient
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

import src.nodes.output_format_node as output_format_module
from src.nodes.post_process_node import PostProcessNode

_TOKEN = "pb-containment-test-token"

# Distinctive strings planted in the rejected report. None of them may appear
# anywhere in what the caller receives.
_DRAFT_MARKERS = (
    "SUSPICIOUS ACTIVITY REPORT",
    "疑わしい取引報告書",
    "WASH_TRADING",
    "Aozora Trading KK",
    "REPORTABLE",
    "Section 4",
)

# A credential-shaped token inside the rejected report. The framework scans
# every value a node returns for credential patterns and raises on a match; a
# raise discards the node's return value, which would take the clearing with it
# and leave the report in state. Naming only the failed check — never quoting
# the offending text — is what keeps the clearing intact.
_PLANTED_CREDENTIAL = "Bearer abc123def456ghi789jkl012"

_UNLABELLED_REPORT = (
    "SUSPICIOUS ACTIVITY REPORT / 疑わしい取引報告書\n"
    "Financial Services Agency (FSA) submission\n"
    "Anomaly Type: WASH_TRADING   Severity: 0.85\n"
    "Counterparty: Aozora Trading KK\n"
    "Article 159 Reportability: REPORTABLE\n"
    "Section 4: escalate for filing decision.\n"
)

_REPORT_WITH_CREDENTIAL = _UNLABELLED_REPORT + f"Upstream fetch header: {_PLANTED_CREDENTIAL}\n"

# A report carrying the label but not the filing-prohibition reminder — the
# second violation form, and the more dangerous one: it reads as approved.
_LABELLED_BUT_UNRESTRICTED_REPORT = "[DRAFT] " + _UNLABELLED_REPORT


def _order(oid, account, volume, otype="buy", cancelled=False, price=50.0):
    return {"id": oid, "account": account, "volume": volume, "price": price, "type": otype, "cancelled": cancelled}


def _payload(orders):
    return json.dumps(
        {
            "orders": orders,
            "accounts": [{"id": "ACC001", "entity": "Aozora Trading KK"}],
            "volumes": [
                {"symbol": "7203", "volume": 10000, "date": "2024-01-15"},
                {"symbol": "7203", "volume": 9800, "date": "2024-01-14"},
            ],
        }
    )


_VALID_PAYLOAD = _payload(
    [
        _order("ORD101", "ACC001", 100, otype="buy", cancelled=True),
        _order("ORD102", "ACC001", 100, otype="sell", cancelled=True),
        _order("ORD103", "ACC001", 120, otype="buy", cancelled=True),
    ]
)


@pytest.fixture(scope="module")
def client():
    os.environ["INVOKE_AUTH_TOKEN"] = _TOKEN
    import src.api.server as server

    with TestClient(server.app) as test_client:
        yield test_client
    os.environ.pop("INVOKE_AUTH_TOKEN", None)


def _invoke(client, payload=_VALID_PAYLOAD):
    return client.post(
        "/invoke",
        json={"input": payload, "session_id": "pb-containment-001"},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    ).json()


@pytest.fixture
def rejected_report(monkeypatch):
    """Make the assembled report violate the output invariant.

    Returns a callable that installs the given report text as the assembled
    result, so the output gate rejects it on the next invoke. This is how the
    gate's own failure path is exercised: no caller input can force it, which
    is exactly why containment has to be tested deliberately.
    """
    original = output_format_module.OutputFormatNode.execute

    def _install(report_text):
        def _execute(self, state):
            updates = original(self, state)
            updates["result"] = report_text
            return updates

        monkeypatch.setattr(output_format_module.OutputFormatNode, "execute", _execute)

    return _install


class TestCleanPathControl:
    """The gate releases legitimate reports — it is not refusing everything.

    Without this control, every containment assertion below is satisfied by an
    agent that has simply stopped working.
    """

    def test_clean_path_releases_a_labelled_report(self, client):
        body = _invoke(client)
        assert body["status"] == AgentStatus.SUCCESS.value
        output = body["output"]
        assert output, "the clean path must release a non-empty report"
        assert "[DRAFT]" in output
        assert "PROHIBITED" in output or "NOT FILED" in output
        assert "ANOMALY TYPE:" in output, "the released report must be the real assembled alert"


class TestErrorEnvelopeCarriesNoReport:
    @pytest.mark.parametrize(
        "report_text",
        [_UNLABELLED_REPORT, _LABELLED_BUT_UNRESTRICTED_REPORT, "   "],
        ids=["unlabelled", "labelled_without_prohibition", "blank"],
    )
    def test_rejected_report_never_reaches_the_caller(self, client, rejected_report, report_text):
        rejected_report(report_text)
        body = _invoke(client)

        assert body["status"] == AgentStatus.ERROR.value, "a violating report must fail closed"

        envelope = json.dumps(body, ensure_ascii=False)
        for marker in _DRAFT_MARKERS:
            assert marker not in envelope, f"rejected report text {marker!r} reached the caller"

    def test_a_credential_bearing_report_is_refused_before_the_output_gate(self, client, rejected_report):
        """Recorded because the layer that refuses is not the one you would guess.

        The framework scans every value a node returns for credential patterns,
        so a report carrying one is refused where it is assembled — the output
        gate never sees it, and the caller gets an empty output rather than a
        withheld notice. The containment assertion is the same either way.
        """
        rejected_report(_REPORT_WITH_CREDENTIAL)
        body = _invoke(client)

        assert body["status"] == AgentStatus.ERROR.value
        assert not body.get("output")
        envelope = json.dumps(body, ensure_ascii=False)
        assert _PLANTED_CREDENTIAL not in envelope
        for marker in _DRAFT_MARKERS:
            assert marker not in envelope

    def test_error_envelope_carries_no_traceback_or_source_paths(self, client, rejected_report):
        rejected_report(_UNLABELLED_REPORT)
        envelope = json.dumps(_invoke(client), ensure_ascii=False)

        for tell in ("Traceback (most recent call last)", 'File "', "src/nodes/", "site-packages", ".py"):
            assert tell not in envelope, f"internal detail {tell!r} reached the caller"

    def test_caller_receives_a_withheld_notice_not_an_empty_field(self, client, rejected_report):
        """The notice must be non-empty.

        An empty string here is falsy, which re-enables the response builder's
        fallback to the un-gated `result` field — the exact leak this contains.
        """
        rejected_report(_UNLABELLED_REPORT)
        output = _invoke(client)["output"]
        assert output, "the withheld-notice must be non-empty or the fallback re-opens"
        assert "WITHHELD" in output.upper()

    def test_the_next_caller_is_served_normally_after_a_rejection(self, client, rejected_report, monkeypatch):
        """Withholding is per-request: it must not wedge the agent.

        Clearing state fields on the way out is only safe if the next
        invocation starts clean — so reject once, remove the fault, and require
        a real report on the following request.
        """
        rejected_report(_UNLABELLED_REPORT)
        assert _invoke(client)["status"] == AgentStatus.ERROR.value

        monkeypatch.undo()
        recovered = _invoke(client)
        assert recovered["status"] == AgentStatus.SUCCESS.value
        assert "[DRAFT]" in recovered["output"]
        assert "WITHHELD" not in recovered["output"].upper()


class TestGateClearsEveryReportBearingField:
    """Asserted on the node's own return value.

    The end-to-end tests above prove the caller is contained; these prove the
    mechanism, so a future change that reintroduces the leak fails here with a
    readable reason rather than only in an envelope scan.
    """

    @staticmethod
    def _reject(report_text):
        node = PostProcessNode()
        return node.execute(
            {
                "result": report_text,
                "sar_draft_content": report_text,
                "audit_trail": ["[EARLIER-STAGE] prior entry"],
            }
        )

    @pytest.mark.parametrize(
        "report_text",
        [_UNLABELLED_REPORT, _LABELLED_BUT_UNRESTRICTED_REPORT, _REPORT_WITH_CREDENTIAL, "", "   "],
        ids=["unlabelled", "labelled_without_prohibition", "unlabelled_with_credential", "empty", "blank"],
    )
    def test_every_report_bearing_field_is_cleared(self, report_text):
        updates = self._reject(report_text)
        assert updates["status"] == AgentStatus.ERROR.value
        for field in ("result", "sar_draft_content", "hitl_draft"):
            assert field in updates, f"{field} must be explicitly cleared, not merely left behind"
            assert updates[field] is None, f"{field} still carries report data"

    def test_non_string_result_is_also_contained(self):
        updates = self._reject({"report": _UNLABELLED_REPORT})
        assert updates["status"] == AgentStatus.ERROR.value
        assert updates["result"] is None

    def test_formatted_output_is_a_truthy_notice(self):
        updates = self._reject(_UNLABELLED_REPORT)
        assert updates["formatted_output"], "an empty formatted_output re-enables the fallback to result"
        assert "WITHHELD" in updates["formatted_output"].upper()

    def test_rejection_message_names_the_check_and_quotes_nothing(self):
        """The message must not echo the rejected text.

        Quoting it would put report content — and any credential inside it —
        into the value the framework scans on the way out; a match there raises,
        and the raise discards this return value together with the clearing.
        """
        updates = self._reject(_REPORT_WITH_CREDENTIAL)
        recorded = " ".join(updates["error_log"]) + " ".join(updates["audit_trail"])
        assert _PLANTED_CREDENTIAL not in recorded
        for marker in _DRAFT_MARKERS:
            assert marker not in recorded
        assert "missing [DRAFT] label" in " ".join(updates["error_log"])

    def test_prior_audit_entries_are_preserved(self):
        updates = self._reject(_UNLABELLED_REPORT)
        assert "[EARLIER-STAGE] prior entry" in updates["audit_trail"]
        assert any("withheld" in entry.lower() for entry in updates["audit_trail"])

    def test_a_failing_trace_emit_cannot_discard_the_containment(self, monkeypatch):
        """Containment outranks the audit write.

        Anything that raises inside the rejection branch would replace this
        node's return value with a generic error update that clears nothing.
        """
        import src.nodes.post_process_node as post_process_module

        def _boom(*args, **kwargs):
            raise RuntimeError("trace sink unavailable")

        monkeypatch.setattr(post_process_module, "emit_trace_event", _boom)
        updates = self._reject(_UNLABELLED_REPORT)
        assert updates["status"] == AgentStatus.ERROR.value
        assert updates["result"] is None
        assert updates["formatted_output"]

    def test_containment_survives_the_frameworks_own_output_scan(self):
        """Driven through the framework wrapper, not execute() directly.

        The wrapper scans every value this node returns for credential patterns
        and raises on a match, and a raise replaces the node's return value with
        a generic error update that clears nothing — the rejected report would
        stay in state and ship. A violation message quoting the rejected text
        would put any credential inside it straight into that scan. Naming only
        the failed check is what keeps the clearing intact, so assert the
        cleared keys are PRESENT, not merely absent-and-therefore-None.
        """
        node = PostProcessNode()
        updates = node(
            {
                "result": _REPORT_WITH_CREDENTIAL,
                "sar_draft_content": _REPORT_WITH_CREDENTIAL,
                "audit_trail": [],
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "node_history": [],
                "execution_time": {},
            }
        )

        assert updates["status"] == AgentStatus.ERROR.value
        for field in ("result", "sar_draft_content", "hitl_draft"):
            assert field in updates, (
                f"{field} is missing from the node's return value — the clearing was discarded, "
                "which happens when something in the rejection branch raises"
            )
            assert updates[field] is None
        assert updates["formatted_output"]

        returned = json.dumps(updates, ensure_ascii=False)
        assert _PLANTED_CREDENTIAL not in returned
        assert "Traceback" not in returned

    def test_a_conforming_report_is_released_unchanged(self):
        """Control at the node level: the gate is not clearing unconditionally."""
        conforming = "[DRAFT] MARKET ANOMALY ALERT\nAUTOMATIC FILING IS PROHIBITED.\n"
        updates = PostProcessNode().execute({"result": conforming})
        assert updates["status"] == AgentStatus.SUCCESS.value
        assert updates["formatted_output"] == conforming
        assert "result" not in updates, "the clean path must not clear anything"
