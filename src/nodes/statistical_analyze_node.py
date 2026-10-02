"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents

import math
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Statistical thresholds. These are fixed module-level defaults: execute() takes
# no config parameter and config/agent.yaml declares no threshold keys, so the
# values below are the only source. Read them here, not from state or config.
_DEFAULT_ZSCORE_THRESHOLD = 2.5  # z-score above this signals anomaly
_DEFAULT_ZSCORE_WINDOW_DAYS = 30  # rolling window for baseline calculation
_DEFAULT_CANCELLATION_RATE_THRESHOLD = 0.3  # >30% cancellation rate flags anomaly
_DEFAULT_VOLUME_SPIKE_MULTIPLIER = 3.0  # volume > mean * multiplier = spike


def _finite_number(value: Any) -> float | None:
    """Return the value as a finite float, or None.

    Defense in depth behind TradingDataInputNode's bounds: bool is an int
    subclass and NaN/Infinity are float instances, so a bare isinstance check
    admits all three — a single NaN then poisons every mean/std downstream and
    silently blanks the anomaly statistics. Ingest already rejects these; this
    guard keeps the statistics safe even when the node is driven directly.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    num = float(value)
    return num if math.isfinite(num) else None


def _calculate_zscore(values: list[float]) -> tuple[float, float, float]:
    """Return (z_score_max, mean, std) for a list of numeric values.

    Uses population standard deviation. Returns (0.0, 0.0, 0.0) for empty/single input.
    """
    if len(values) < 2:
        return 0.0, 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0.0:
        return 0.0, mean, std
    z_scores = [abs((x - mean) / std) for x in values]
    return max(z_scores), mean, std


class StatisticalAnalyzeNode(FunctionNode):
    """Apply statistical anomaly detection to ingested trading data.

    Analyses:
    - Z-score on order volume deviations (fixed module-level threshold)
    - Volume spike detection (single-day volume vs rolling mean)
    - Order cancellation rate (cancelled orders / total orders)

    Outputs `statistical_flags` dict with:
      z_score_max: float               — peak z-score across order volumes
      z_score_threshold: float         — threshold used for this run
      volume_spike_detected: bool      — True if any daily volume > mean * multiplier
      volume_spike_ratio: float        — peak spike ratio (0.0 if no data)
      cancellation_rate: float         — fraction of orders marked cancelled
      cancellation_rate_threshold: float — threshold used for this run
      anomaly_indicators: list[str]    — human-readable list of triggered flags

    Audit: emits "statistical_analysis_complete" domain audit event.

    Inner domain node — trust level ANONYMOUS.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        zscore_threshold = _DEFAULT_ZSCORE_THRESHOLD
        volume_spike_multiplier = _DEFAULT_VOLUME_SPIKE_MULTIPLIER
        cancellation_threshold = _DEFAULT_CANCELLATION_RATE_THRESHOLD

        audit_trail = list(state.get("audit_trail") or [])
        raw = state.get("raw_trading_data") or {}

        if not raw:
            emit_trace_event(
                "statistical_analysis_skipped",
                {"reason": "no_trading_data", "agent": "FIN-C2-097"},
                state,
            )
            audit_trail.append("[STATISTICAL-ANALYZE][ERROR] No raw_trading_data in state — skipping")
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["StatisticalAnalyzeNode: raw_trading_data not found in state"],
                "audit_trail": audit_trail,
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("StatisticalAnalyzeNode: raw_trading_data not found in state"),
            }

        orders = raw.get("orders", [])
        volumes = raw.get("volumes", [])
        anomaly_indicators = []

        # ── Z-score on order volumes ──────────────────────────────────────────
        order_volumes = []
        cancelled_count = 0
        for order in orders:
            vol = _finite_number(order.get("volume"))
            if vol is not None:
                order_volumes.append(vol)
            # Ingest validation restricts `cancelled` to a JSON boolean.
            if order.get("cancelled", False) is True:
                cancelled_count += 1

        z_score_max, vol_mean, vol_std = _calculate_zscore(order_volumes)
        if z_score_max > zscore_threshold:
            anomaly_indicators.append(f"z_score_volume_spike: z={z_score_max:.2f} > threshold={zscore_threshold}")

        # ── Cancellation rate ─────────────────────────────────────────────────
        total_orders = len(orders)
        cancellation_rate = (cancelled_count / total_orders) if total_orders > 0 else 0.0
        if cancellation_rate > cancellation_threshold:
            anomaly_indicators.append(
                f"high_cancellation_rate: {cancellation_rate:.2%} > threshold={cancellation_threshold:.0%}"
            )

        # ── Volume spike detection ────────────────────────────────────────────
        daily_volumes = []
        for vol_rec in volumes:
            v = _finite_number(vol_rec.get("volume"))
            if v is not None:
                daily_volumes.append(v)

        volume_spike_detected = False
        volume_spike_ratio = 0.0
        if len(daily_volumes) >= 2:
            daily_mean = sum(daily_volumes) / len(daily_volumes)
            if daily_mean > 0:
                max_daily = max(daily_volumes)
                volume_spike_ratio = max_daily / daily_mean
                if volume_spike_ratio > volume_spike_multiplier:
                    volume_spike_detected = True
                    anomaly_indicators.append(
                        f"volume_spike: ratio={volume_spike_ratio:.2f}x > " f"multiplier={volume_spike_multiplier:.1f}x"
                    )

        statistical_flags = {
            "z_score_max": round(z_score_max, 4),
            "z_score_threshold": zscore_threshold,
            "volume_spike_detected": volume_spike_detected,
            "volume_spike_ratio": round(volume_spike_ratio, 4),
            "cancellation_rate": round(cancellation_rate, 4),
            "cancellation_rate_threshold": cancellation_threshold,
            "anomaly_indicators": anomaly_indicators,
            "order_count": total_orders,
            "cancelled_count": cancelled_count,
        }

        emit_trace_event(
            "statistical_analysis_complete",
            {
                "agent": "FIN-C2-097",
                "z_score_max": statistical_flags["z_score_max"],
                "volume_spike_detected": volume_spike_detected,
                "cancellation_rate": statistical_flags["cancellation_rate"],
                "anomaly_indicator_count": len(anomaly_indicators),
            },
            state,
        )
        indicator_summary = (
            f"{len(anomaly_indicators)} indicator(s): {anomaly_indicators}"
            if anomaly_indicators
            else "no anomaly indicators"
        )
        audit_trail.append(
            f"[STATISTICAL-ANALYZE] z_score_max={z_score_max:.2f}, "
            f"cancellation_rate={cancellation_rate:.2%}, "
            f"volume_spike={volume_spike_detected}; {indicator_summary}"
        )
        return {
            "statistical_flags": statistical_flags,
            "audit_trail": audit_trail,
            "status": AgentStatus.SUCCESS.value,
        }
