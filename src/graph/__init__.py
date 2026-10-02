"""AgentCore Platform v1.0"""

# Convenience re-export of the agent class from this package.
#
# config/agent.yaml points the registry at the full dotted path
# ``src.graph.graph.FinancialMarketAnomalyTradingSignalDetectionAgent``;
# this re-export additionally lets callers import the class from
# ``src.graph`` directly.

from src.graph.graph import FinancialMarketAnomalyTradingSignalDetectionAgent

__all__ = ["FinancialMarketAnomalyTradingSignalDetectionAgent"]
