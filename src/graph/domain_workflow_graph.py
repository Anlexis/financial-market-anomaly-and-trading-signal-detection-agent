"""AgentCore Platform v1.0 — Inner domain workflow graph for FIN-C2-097"""

# src/graph/domain_workflow_graph.py
#
# Inner graph for the Cat 2 nested pattern.
# Instantiated by TradingSignalGraphNode.get_subgraph() in graph.py.
#
# Inherits BaseGraph (fully custom linear topology — no backbone slots).
# Implements all 7 abstract methods required by BaseGraph:
#   name, state_schema, _validate_config, register_nodes,
#   add_edges, route, get_output
#
# Pipeline (linear):
#   START → trading_data_input → statistical_analyze → pattern_classify
#         → sar_draft_generate → output_format → END
#
# All inner nodes use TrustLevel.ANONYMOUS (outer PreProcessNode is the
# VERIFIED_EXTERNAL boundary; inner nodes inherit the validated trust context
# propagated by GraphNode.execute() via InvocationContext.from_state()).

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.pattern_classify_node import PatternClassifyNode
from src.nodes.sar_draft_generate_node import SARDraftGenerateNode
from src.nodes.statistical_analyze_node import StatisticalAnalyzeNode
from src.nodes.trading_data_input_node import TradingDataInputNode
from src.schemas.state import State


class TradingSignalDomainWorkflowGraph(BaseGraph):
    """Inner domain workflow for FIN-C2-097 market anomaly detection.

    Inherits BaseGraph directly for a fully custom linear topology.
    Called by TradingSignalGraphNode.get_subgraph() in src/graph/graph.py.

    Pipeline:
        START → trading_data_input → statistical_analyze → pattern_classify
              → sar_draft_generate → output_format → END

    The get_output() result is received by TradingSignalGraphNode.merge_output()
    as the `sub_result` argument; design both methods together.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "trading_signal_domain_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No mandatory config keys for the inner workflow.

        TradingSignalGraphNode._parent_config() (graph.py) forwards the
        manifest's declared agent.config settings under config["configurable"];
        all of them are optional, so validation is permissive rather than
        raising ConfigError.

        The statistical thresholds (z-score, cancellation rate, volume-spike
        multiplier) are NOT configuration: config/agent.yaml declares no such
        keys and StatisticalAnalyzeNode reads its module-level defaults
        directly. Making them tunable requires declaring the keys in the
        manifest and reading them in the node — a design change, not a doc fix.
        """
        pass

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all inner domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize (outer backbone concern).
        All nodes instantiated with NO constructor arguments (SDK-v1 rule).
        """
        self._nodes["trading_data_input"] = TradingDataInputNode()
        self._nodes["statistical_analyze"] = StatisticalAnalyzeNode()
        self._nodes["pattern_classify"] = PatternClassifyNode()
        self._nodes["sar_draft_generate"] = SARDraftGenerateNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear domain topology.

        Linear flow: trading_data_input → statistical_analyze → pattern_classify
                     → sar_draft_generate → output_format → END
        """
        self._sg.add_edge(START, "trading_data_input")
        self._sg.add_edge("trading_data_input", "statistical_analyze")
        self._sg.add_edge("statistical_analyze", "pattern_classify")
        self._sg.add_edge("pattern_classify", "sar_draft_generate")
        self._sg.add_edge("sar_draft_generate", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Required by BaseGraph ABC.

        This graph uses a purely linear topology — route() is never called
        unless add_conditional_edges() references it. Implemented to satisfy
        the abstract method contract.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the sub_result dict returned to TradingSignalGraphNode.merge_output().

        Keys returned here must align with merge_output() in graph.py.
        """
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "audit_trail": state.get("audit_trail", []),
        }
