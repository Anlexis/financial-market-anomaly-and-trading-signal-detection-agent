"""FIN-C2-097 — declared runtime config reaches the inner graph.

config/agent.yaml is the static manifest; the runtime parameters live in
config/config.yaml. The platform registry (and the standalone server) pass
that file to the outer graph constructor, and TradingSignalGraphNode forwards
the declared values to the inner TradingSignalDomainWorkflowGraph under the
LangGraph ``configurable`` key. These tests prove the declared file values
actually arrive — a reader pointed at a retired location returns {} silently
and every declared value goes dead.
"""

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_FILE = _REPO_ROOT / "config" / "config.yaml"


def _declared() -> dict:
    with open(_CONFIG_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestRuntimeConfigFile:
    def test_config_file_exists_and_declares_runtime_values(self):
        declared = _declared()
        assert declared.get("max_retry") == 3
        assert declared.get("timeout_s") == 30


class TestConfigReachesInnerGraph:
    def test_loader_reads_the_declared_file(self):
        from src.graph.graph import _load_runtime_config

        loaded = _load_runtime_config()
        assert loaded == _declared(), (
            "the runtime-config reader must read config/config.yaml — "
            "a reader pointed at a retired location returns {} silently"
        )

    def test_parent_config_forwards_declared_values(self):
        from src.graph.graph import TradingSignalGraphNode

        forwarded = TradingSignalGraphNode()._parent_config()
        assert forwarded["configurable"]["max_retry"] == 3
        assert forwarded["configurable"]["timeout_s"] == 30

    def test_subgraph_is_built_with_the_declared_config(self):
        """End-to-end within the node: get_subgraph() must construct the inner
        graph with the declared values, not an empty config."""
        from src.graph.graph import TradingSignalGraphNode

        subgraph = TradingSignalGraphNode().get_subgraph()
        assert subgraph.config["configurable"]["max_retry"] == 3
        assert subgraph.config["configurable"]["timeout_s"] == 30

    def test_missing_file_degrades_to_empty_configurable(self, monkeypatch):
        import src.graph.graph as graph_module

        monkeypatch.setattr(graph_module, "_RUNTIME_CONFIG_PATH", str(_REPO_ROOT / "config" / "absent.yaml"))
        forwarded = graph_module.TradingSignalGraphNode()._parent_config()
        assert forwarded == {"configurable": {}}
