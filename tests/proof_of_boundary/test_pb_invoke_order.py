# PB-6: Backbone Invoke-Order Verification — FIN-C2-097
#
# Verifies that a full Graph.invoke() over a SUCCESS-yielding payload exercises
# the complete outer backbone in order:
#   InitializeNode → PreProcessNode → TradingSignalGraphNode
#   → PostProcessNode → FinalizeNode
#
# The caller must use TrustLevel.VERIFIED_EXTERNAL to exercise the real trust
# path (PreProcessNode requires VERIFIED_EXTERNAL). An INTERNAL context would
# mask inner-node trust-level violations — VERIFIED_EXTERNAL is load-bearing.
#
# A non-SUCCESS status short-circuits main→finalize and skips post_process,
# so the SUCCESS-yielding payload is also load-bearing.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import FinancialMarketAnomalyTradingSignalDetectionAgent

# ── Template-specific constants ───────────────────────────────────────────────

# The class name of the GraphNode subclass registered in the `main` slot.
_MAIN_SLOT_NODE = "TradingSignalGraphNode"

# A SUCCESS-yielding payload: valid JSON trading data that passes all inner
# domain node validations with no anomaly indicators (clean baseline data).
_VALID_PAYLOAD = json.dumps(
    {
        "orders": [
            {
                "id": "ORD001",
                "account": "ACC001",
                "volume": 100,
                "price": 50.0,
                "type": "buy",
                "cancelled": False,
                "timestamp": "2024-01-15T10:00:00Z",
            },
            {
                "id": "ORD002",
                "account": "ACC002",
                "volume": 102,
                "price": 50.1,
                "type": "sell",
                "cancelled": False,
                "timestamp": "2024-01-15T10:01:00Z",
            },
        ],
        "accounts": [
            {"id": "ACC001", "entity": "Test Corp A"},
            {"id": "ACC002", "entity": "Test Corp B"},
        ],
        "volumes": [
            {"symbol": "TEST", "volume": 10000, "date": "2024-01-15"},
            {"symbol": "TEST", "volume": 9800, "date": "2024-01-14"},
        ],
    }
)

# Expected backbone node names in execution order.
_EXPECTED_BACKBONE = [
    "InitializeNode",
    "PreProcessNode",
    _MAIN_SLOT_NODE,
    "PostProcessNode",
    "FinalizeNode",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pos_in_history(name: str, node_history: list) -> int:
    """Return the index of the first node_history entry containing `name`."""
    for i, entry in enumerate(node_history):
        if name in str(entry):
            return i
    return -1


# ── PB-6 Test ─────────────────────────────────────────────────────────────────


class TestPB6BackboneInvokeOrder:
    """PB-6: Full backbone invoke-order with a real external caller.

    Uses VERIFIED_EXTERNAL trust level — the same path a real caller takes.
    Exercises: the trust gate (PreProcessNode) + inner domain workflow via
    TradingSignalGraphNode + the output-gate and audit contracts in all nodes.
    """

    @pytest.fixture(scope="class")
    def invoke_result(self):
        """Compile and invoke the agent once; reused by all tests in this class."""
        agent = FinancialMarketAnomalyTradingSignalDetectionAgent()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(_VALID_PAYLOAD, ctx=ctx)
        return result

    def test_invoke_returns_success(self, invoke_result):
        """Full pipeline with a valid payload must yield AgentStatus.SUCCESS.value."""
        assert invoke_result["status"] == AgentStatus.SUCCESS.value, (
            f"Expected AgentStatus.SUCCESS.value but got {invoke_result['status']}.\n"
            f"error_log={invoke_result.get('error_log')}\n"
            f"output={invoke_result.get('output', '')[:200]}"
        )

    def test_invoke_output_is_non_empty(self, invoke_result):
        """The invoke() output key must be a non-empty string."""
        output = invoke_result.get("output")
        assert output, (
            "Expected non-empty result['output'] — " "check PostProcessNode.execute() and FinalizeNode mapping."
        )

    def test_node_history_is_non_empty(self, invoke_result):
        """node_history must be populated by the framework on every invoke."""
        node_history = invoke_result.get("node_history", [])
        assert node_history, "node_history is empty — the framework should populate it on every invoke."

    def test_all_backbone_nodes_executed(self, invoke_result):
        """Every backbone node must appear in node_history."""
        node_history = invoke_result.get("node_history", [])
        missing = [name for name in _EXPECTED_BACKBONE if _pos_in_history(name, node_history) == -1]
        assert not missing, f"Backbone nodes not found in node_history: {missing}\n" f"node_history={node_history}"

    def test_backbone_execution_order(self, invoke_result):
        """Backbone nodes must execute in the correct order (backbone contract)."""
        node_history = invoke_result.get("node_history", [])
        positions = {name: _pos_in_history(name, node_history) for name in _EXPECTED_BACKBONE}

        for i in range(len(_EXPECTED_BACKBONE) - 1):
            a = _EXPECTED_BACKBONE[i]
            b = _EXPECTED_BACKBONE[i + 1]
            pos_a = positions[a]
            pos_b = positions[b]
            assert pos_a != -1 and pos_b != -1 and pos_a < pos_b, (
                f"Backbone order violation: {a} (pos={pos_a}) must precede "
                f"{b} (pos={pos_b}).\n"
                f"Full node_history={node_history}"
            )

    def test_output_key_not_formatted_output(self, invoke_result):
        """Invoke result must expose 'output', not 'formatted_output'."""
        assert "output" in invoke_result, (
            "invoke() result must contain key 'output' — " "do NOT assert against 'formatted_output'."
        )

    def test_status_is_value_string_not_enum(self, invoke_result):
        """State status must be the AgentStatus .value string, not the enum."""
        assert (
            invoke_result["status"] == AgentStatus.SUCCESS.value
        ), f"Status must equal AgentStatus.SUCCESS.value, got: {invoke_result['status']!r}"
        assert type(invoke_result["status"]) is str, (  # noqa: E721 — exact type, not a subclass
            "Status written to State must be a plain str (AgentStatus.SUCCESS.value), "
            "not the AgentStatus enum member."
        )
        assert (
            invoke_result["status"] != "SUCCESS"
        ), "Status should be AgentStatus.SUCCESS.value, not the member-name string 'SUCCESS'."
