"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# FIEA Article 159 anomaly taxonomy
_ANOMALY_NONE = "none"
_ANOMALY_WASH_TRADING = "wash_trading"
_ANOMALY_SPOOFING = "spoofing"
_ANOMALY_LAYERING = "layering"

# Severity thresholds
_SEVERITY_HIGH_THRESHOLD = 0.7
_SEVERITY_MEDIUM_THRESHOLD = 0.4


def _compute_severity(z_score_max: float, cancellation_rate: float, volume_spike: bool) -> float:
    """Compute severity score in [0.0, 1.0] from statistical signals.

    Combines normalised z-score contribution, cancellation rate, and volume spike flag.
    Capped at 1.0.
    """
    z_component = min(z_score_max / 5.0, 0.5)  # z up to 5 → 0.5 contribution
    cancel_component = min(cancellation_rate, 0.3)  # up to 0.3 contribution
    spike_component = 0.2 if volume_spike else 0.0  # binary 0.2 contribution
    raw = z_component + cancel_component + spike_component
    return round(min(raw, 1.0), 4)


def _classify_pattern(flags: dict[str, Any], raw_data: dict[str, Any]) -> str:
    """Classify anomaly type per FIEA Article 159 taxonomy.

    Classification logic:
    - Wash trading:  high cancellation rate + self-dealing (same account on both sides)
    - Spoofing:      high cancellation rate + large order volume anomaly (z-score spike)
    - Layering:      volume spike flag + multiple repeated cancellations
    - None:          no significant anomaly indicators

    Returns one of: "wash_trading" / "spoofing" / "layering" / "none".
    """
    indicators = flags.get("anomaly_indicators", [])
    z_score_max = float(flags.get("z_score_max", 0.0))
    cancellation_rate = float(flags.get("cancellation_rate", 0.0))
    volume_spike = bool(flags.get("volume_spike_detected", False))
    z_threshold = float(flags.get("z_score_threshold", 2.5))
    cancel_threshold = float(flags.get("cancellation_rate_threshold", 0.3))

    if not indicators:
        return _ANOMALY_NONE

    orders = (raw_data or {}).get("orders", [])
    # Detect self-dealing: same account appears on both buy and sell sides
    buy_accounts = {o.get("account") for o in orders if o.get("type", "").lower() == "buy"}
    sell_accounts = {o.get("account") for o in orders if o.get("type", "").lower() == "sell"}
    self_dealing = bool(buy_accounts & sell_accounts)

    high_cancellation = cancellation_rate > cancel_threshold
    high_z = z_score_max > z_threshold

    if high_cancellation and self_dealing:
        return _ANOMALY_WASH_TRADING
    if high_cancellation and high_z:
        return _ANOMALY_SPOOFING
    if volume_spike:
        return _ANOMALY_LAYERING
    return _ANOMALY_NONE


class PatternClassifyNode(FunctionNode):
    """Classify trading anomaly type per FIEA Article 159 taxonomy.

    Uses statistical flags from StatisticalAnalyzeNode to determine:
    - Anomaly type: wash_trading / spoofing / layering / none
    - Severity score: 0.0–1.0 (continuous risk indicator)
    - FIEA reportability: True when anomaly type is not "none"

    FIEA Article 159 taxonomy references:
    - Wash trading: 仮装売買 (fictitious transactions; Art. 159(1)(i))
    - Spoofing: 見せ玉 (false order book entries; Art. 159(1)(vi))
    - Layering: レイヤリング (systematic cancel-and-rebook; Art. 159(1)(vi))

    Audit: emits "pattern_classified" domain audit event.

    Inner domain node — trust level ANONYMOUS.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        audit_trail = list(state.get("audit_trail") or [])
        flags = state.get("statistical_flags") or {}
        raw_data = state.get("raw_trading_data") or {}

        if not flags:
            emit_trace_event(
                "pattern_classification_skipped",
                {"reason": "no_statistical_flags", "agent": "FIN-C2-097"},
                state,
            )
            audit_trail.append("[PATTERN-CLASSIFY][ERROR] No statistical_flags in state — skipping")
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PatternClassifyNode: statistical_flags not found in state"],
                "audit_trail": audit_trail,
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("PatternClassifyNode: statistical_flags not found in state"),
            }

        anomaly_type = _classify_pattern(flags, raw_data)
        severity_score = _compute_severity(
            z_score_max=float(flags.get("z_score_max", 0.0)),
            cancellation_rate=float(flags.get("cancellation_rate", 0.0)),
            volume_spike=bool(flags.get("volume_spike_detected", False)),
        )
        fiea_reportable = anomaly_type != _ANOMALY_NONE

        # Severity label for audit
        if severity_score >= _SEVERITY_HIGH_THRESHOLD:
            severity_label = "HIGH"
        elif severity_score >= _SEVERITY_MEDIUM_THRESHOLD:
            severity_label = "MEDIUM"
        else:
            severity_label = "LOW"

        emit_trace_event(
            "pattern_classified",
            {
                "agent": "FIN-C2-097",
                "anomaly_type": anomaly_type,
                "severity_score": severity_score,
                "fiea_reportable": fiea_reportable,
            },
            state,
        )
        audit_trail.append(
            f"[PATTERN-CLASSIFY] anomaly_type={anomaly_type}, "
            f"severity={severity_score:.2f} ({severity_label}), "
            f"fiea_reportable={fiea_reportable}"
        )
        return {
            "anomaly_type": anomaly_type,
            "severity_score": severity_score,
            "fiea_reportable": fiea_reportable,
            "audit_trail": audit_trail,
            "status": AgentStatus.SUCCESS.value,
        }
