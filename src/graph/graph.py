"""AgentCore Platform v1.0"""

# Cat 2 nested graph for FIN-C2-097.
#
# Outer graph: FinancialMarketAnomalyTradingSignalDetectionAgent (AgentBaseGraph)
#   Fixed 5-node backbone: initialize → pre_process → main → post_process → finalize
#   Domain complexity is encapsulated in TradingSignalGraphNode (main slot).
#
# Inner graph: TradingSignalDomainWorkflowGraph (BaseGraph)
#   Located at: src/graph/domain_workflow_graph.py
#   Pipeline: trading_data_input → statistical_analyze → pattern_classify
#             → sar_draft_generate → output_format
#
# Rules:
#   ✅ Outer graph inherits AgentBaseGraph
#   ✅ super().register_nodes() called in outer graph
#   ✅ TradingSignalGraphNode assigned to main slot
#   ✅ Inner graph at src/graph/domain_workflow_graph.py
#   ✅ merge_output() returns only changed state keys
#   ❌ add_edges() is NOT overridden on the outer graph

import os
from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import TradingSignalDomainWorkflowGraph

# Runtime parameters — config/config.yaml at the repo root (three levels up
# from this file: src/graph/graph.py -> src/graph -> src -> <repo root>).
# config/agent.yaml is the static manifest (identity + entry point); the
# runtime values (max_retry, timeout_s) live in config/config.yaml, which the
# platform registry loads and passes to the graph constructor as ``config=``.
_RUNTIME_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "config.yaml",
)


def _load_runtime_config() -> dict[str, Any]:
    """Return the runtime-parameter mapping from config/config.yaml.

    Best-effort: a missing, unreadable or unparseable file yields ``{}`` so
    graph construction never breaks — the inner graph then runs on its declared
    defaults. PyYAML is imported lazily: it is a framework runtime dependency,
    so importing it on demand avoids a hard module-load coupling.
    """
    try:
        import yaml

        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


class TradingSignalGraphNode(GraphNode):
    """GraphNode wrapping the inner TradingSignalDomainWorkflowGraph.

    Assigned to the `main` slot in FinancialMarketAnomalyTradingSignalDetectionAgent.
    Bridges the outer backbone state to the inner domain workflow via:
    - get_subgraph()   — instantiate TradingSignalDomainWorkflowGraph
    - extract_input()  — pull validated_input string for the inner invoke()
    - merge_output()   — map inner sub_result keys back to outer state
    """

    # Inner domain nodes are ANONYMOUS — the outer PreProcessNode gate is the trust boundary.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    # Re-raise inner graph exceptions as SubgraphError (fail fast — no silent degradation)
    error_strategy: ClassVar[str] = "propagate"

    # HITL interrupts stay inside the inner graph (not surfaced to outer caller)
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "TradingSignalDomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        Called on every execute() — inner graph construction is lightweight
        (no LLM initialisation; pure statistical logic). The graph is built
        with the declared runtime config from _parent_config(); built with no
        config, the declared runtime values would never reach it.
        """
        from src.graph.domain_workflow_graph import TradingSignalDomainWorkflowGraph

        return TradingSignalDomainWorkflowGraph(config=self._parent_config())

    def _parent_config(self) -> dict[str, Any]:
        """Forward the declared runtime config to the inner graph.

        config/config.yaml declares the runtime parameters (``max_retry``,
        ``timeout_s``). Without this hook the inner
        TradingSignalDomainWorkflowGraph would be built with an empty config,
        leaving every declared setting dead on arrival. The non-None declared
        settings are exposed under the LangGraph ``configurable`` key, which is
        where BaseGraph consumers look for them.

        Degrades to ``{"configurable": {}}`` when the file is missing or
        malformed — _load_runtime_config() swallows the error and returns {}
        rather than raising during graph construction.

        Scope note: the runtime config declares no statistical-threshold keys,
        and StatisticalAnalyzeNode / PatternClassifyNode read their thresholds
        from module-level defaults rather than from config. This hook forwards
        the declared-config path only; no key is invented here to create a
        consumer that does not exist.
        """
        config = _load_runtime_config()
        declared = {
            "max_retry": config.get("max_retry"),
            "timeout_s": config.get("timeout_s"),
        }
        return {"configurable": {k: v for k, v in declared.items() if v is not None}}

    def extract_input(self, state: AgentState) -> str:
        """Return the validated trading data string for the inner graph invoke().

        Prefers validated_input (set by PreProcessNode) over raw user_input.
        """
        return cast(str, state.get("validated_input", state.get("user_input", "")))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map inner graph sub_result back into the outer state.

        sub_result is the dict returned by TradingSignalDomainWorkflowGraph.get_output().
        Returns ONLY the keys this node changes — never the full state.

        Designed together with TradingSignalDomainWorkflowGraph.get_output():
          sub_result["output"]      → outer state["result"]
          sub_result["status"]      → outer state["status"]
          sub_result["audit_trail"] → outer state["audit_trail"]
        """
        return {
            "result": sub_result.get("output"),
            "status": sub_result.get("status"),
            "audit_trail": sub_result.get("audit_trail", []),
        }


class FinancialMarketAnomalyTradingSignalDetectionAgent(AgentBaseGraph):
    """Outer Cat 2 graph for FIN-C2-097.

    Backbone: initialize → pre_process → main (TradingSignalGraphNode)
              → post_process → finalize

    Domain logic is encapsulated in TradingSignalGraphNode which invokes
    TradingSignalDomainWorkflowGraph (inner BaseGraph).

    Class name matches:
    - config/agent.yaml  class: FinancialMarketAnomalyTradingSignalDetectionAgent
    - src/api/server.py  from src.graph.graph import FinancialMarketAnomalyTradingSignalDetectionAgent
    """

    @property
    def name(self) -> str:
        return "FinancialMarketAnomalyTradingSignalDetectionAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        # super() injects InitializeNode and FinalizeNode into the backbone.
        super().register_nodes()

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = TradingSignalGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
