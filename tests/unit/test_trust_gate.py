# FIN-C2-097 — Unit tests: the trust gate.
#
# Every invocation here goes through node(state) — BaseNode.__call__ — which
# runs the trust gate -> the input gate -> execute() -> the output gate.
# Calling node.execute(state) directly bypasses __call__ and therefore never
# exercises the gate at all, which is why the previous suite (test_agent.py)
# could pass with no trust-gate coverage.
#
# A denial RETURNS an error dict (it never raises): status ERROR and
# "trust gate denied" in error_log. execute() does not run on a denial, so
# none of the keys that node writes appear in the returned dict — that
# absence, not the generic ERROR status, is what proves the gate fired rather
# than some later failure inside the node.

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.output_format_node import OutputFormatNode
from src.nodes.pattern_classify_node import PatternClassifyNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.sar_draft_generate_node import SARDraftGenerateNode
from src.nodes.statistical_analyze_node import StatisticalAnalyzeNode
from src.nodes.trading_data_input_node import TradingDataInputNode

# Keys PreProcessNode.execute() writes. audit_trail is written on EVERY one of
# its return paths (success and every rejection), so its absence is a positive
# signal that execute() never ran.
_PRE_PROCESS_OUTPUT_KEYS = ("validated_input", "enriched_context", "audit_trail")

_VALID_QUERY = "analyse order-book anomalies for the last trading session"


def _state(trust_value: str, user_input: str = _VALID_QUERY, **extra) -> dict:
    state = {
        "user_input": user_input,
        "caller_trust_level": trust_value,
        "node_history": [],
        "error_log": [],
        "audit_trail": [],
        "session_id": "test-session",
        "execution_time": {},
    }
    state.update(extra)
    return state


class TestTrustGateDenial:
    """An ANONYMOUS caller must be denied at the VERIFIED_EXTERNAL boundary."""

    def test_anonymous_caller_denied_on_pre_process(self):
        """ANONYMOUS caller on PreProcessNode: __call__ returns an error dict."""
        node = PreProcessNode()  # required_trust_level = VERIFIED_EXTERNAL

        result = node(_state(TrustLevel.ANONYMOUS.value))

        assert result.get("status") == AgentStatus.ERROR.value
        error_log = result.get("error_log", [])
        assert any(
            "trust gate denied" in str(entry) for entry in error_log
        ), f"expected 'trust gate denied' in error_log, got: {error_log}"

    def test_denial_returns_none_of_the_node_output_keys(self):
        """execute() must not run on denial — none of its output keys appear.

        This is the assertion that distinguishes a real gate denial from any
        other ERROR: PreProcessNode writes audit_trail on every return path it
        has, so an error dict with no audit_trail cannot have come from the
        node body.
        """
        node = PreProcessNode()

        result = node(_state(TrustLevel.ANONYMOUS.value))

        for key in _PRE_PROCESS_OUTPUT_KEYS:
            assert key not in result, f"{key} leaked from a denied invocation — execute() ran despite " "the trust gate"

    def test_denial_error_is_not_the_nodes_own_error(self):
        """The denial error must not carry PreProcessNode's own error prefix.

        Every error PreProcessNode itself raises is prefixed 'PreProcessNode:'.
        A denial is produced above the node, so that prefix must be absent.
        """
        node = PreProcessNode()

        result = node(_state(TrustLevel.ANONYMOUS.value))

        assert result.get("status") == AgentStatus.ERROR.value, (
            "an ANONYMOUS caller must be denied here — a non-ERROR status means " "the gate did not fire"
        )
        assert not any(
            str(entry).startswith("PreProcessNode:") for entry in result.get("error_log", [])
        ), "denial error_log contains the node's own error — execute() ran"

    def test_denial_holds_for_input_the_node_would_reject_anyway(self):
        """Empty input from an ANONYMOUS caller is still a GATE denial.

        Without the gate this input reaches execute() and produces the node's
        own 'user_input is empty or missing' error, so this case separates the
        two failure modes.
        """
        node = PreProcessNode()

        result = node(_state(TrustLevel.ANONYMOUS.value, user_input=""))

        assert result.get("status") == AgentStatus.ERROR.value
        assert any("trust gate denied" in str(entry) for entry in result.get("error_log", []))
        assert not any(
            "empty or missing" in str(entry) for entry in result.get("error_log", [])
        ), "execute() ran and produced its own empty-input error despite the trust gate"


class TestTrustGateAdmission:
    """A VERIFIED_EXTERNAL caller must clear the gate and reach execute()."""

    def test_verified_external_caller_passes_pre_process(self):
        node = PreProcessNode()

        result = node(_state(TrustLevel.VERIFIED_EXTERNAL.value))

        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("validated_input") == _VALID_QUERY
        assert result.get("audit_trail"), "execute() must have written its audit trail"

    def test_node_own_validation_still_applies_after_the_gate(self):
        """Past the gate, the node's own input checks produce its own errors.

        Confirms the ERROR status asserted in the denial tests is not simply
        the only outcome this node can produce for a caller that fails
        validation.
        """
        node = PreProcessNode()

        result = node(_state(TrustLevel.VERIFIED_EXTERNAL.value, user_input="   "))

        assert result.get("status") == AgentStatus.ERROR.value
        assert any(
            str(entry).startswith("PreProcessNode:") for entry in result.get("error_log", [])
        ), "post-gate rejection must carry the node's own error prefix"
        assert not any("trust gate denied" in str(entry) for entry in result.get("error_log", []))

    def test_anonymous_caller_admitted_by_an_inner_node(self):
        """Inner domain nodes are ANONYMOUS and must admit an ANONYMOUS caller."""
        node = TradingDataInputNode()

        result = node(_state(TrustLevel.ANONYMOUS.value, validated_input="{}"))

        assert not any(
            "trust gate denied" in str(entry) for entry in result.get("error_log", [])
        ), "an ANONYMOUS inner node must not deny an ANONYMOUS caller"


class TestTrustLevelMatrix:
    """The declared trust matrix: the outer boundary vs inner domain nodes."""

    def test_pre_process_is_the_verified_external_boundary(self):
        assert PreProcessNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL

    def test_inner_and_post_process_nodes_admit_anonymous(self):
        for node_cls in (
            TradingDataInputNode,
            StatisticalAnalyzeNode,
            PatternClassifyNode,
            SARDraftGenerateNode,
            OutputFormatNode,
            PostProcessNode,
        ):
            assert node_cls.required_trust_level is TrustLevel.ANONYMOUS, (
                f"{node_cls.__name__} must declare TrustLevel.ANONYMOUS — the "
                "outer PreProcessNode is the trust boundary"
            )
