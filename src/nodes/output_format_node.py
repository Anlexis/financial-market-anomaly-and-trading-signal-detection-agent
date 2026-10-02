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


class OutputFormatNode(FunctionNode):
    """Assemble and format the final anomaly alert for delivery.

    Combines all domain analysis results into a structured alert string:
    - Pattern type and FIEA Article 159 classification
    - Severity score and alert level
    - FIEA reportability flag
    - SAR draft reference (DRAFT only — see SARDraftGenerateNode)
    - Immutable audit trail summary

    All outputs are clearly labeled DRAFT where applicable.
    Includes a reminder that SAR filing requires human authorisation.

    Audit: emits "output_formatted" domain audit event.

    Inner domain node — trust level ANONYMOUS.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        audit_trail = list(state.get("audit_trail") or [])

        anomaly_type = state.get("anomaly_type") or "none"
        severity_score = state.get("severity_score") or 0.0
        fiea_reportable = bool(state.get("fiea_reportable", False))
        statistical_flags = state.get("statistical_flags") or {}

        if severity_score >= 0.7:
            alert_level = "HIGH"
        elif severity_score >= 0.4:
            alert_level = "MEDIUM"
        else:
            alert_level = "LOW"

        fiea_line = (
            "REPORTABLE — Please consult compliance officer and file with FSA"
            if fiea_reportable
            else "Below reporting threshold — FIEA Article 159 criteria not met"
        )

        # Format key statistical indicators
        z_score = statistical_flags.get("z_score_max", 0.0)
        cancel_rate = statistical_flags.get("cancellation_rate", 0.0)
        volume_spike = statistical_flags.get("volume_spike_detected", False)
        indicators = statistical_flags.get("anomaly_indicators", [])
        indicator_text = (
            "\n".join(f"  • {ind}" for ind in indicators) if indicators else "  • No anomaly indicators triggered"
        )

        audit_summary_text = (
            "\n".join(f"  {i + 1}. {entry}" for i, entry in enumerate(audit_trail))
            if audit_trail
            else "  (no audit entries)"
        )

        alert = (
            f"════════════════════════════════════════════════════════════\n"
            f"[DRAFT] MARKET ANOMALY ALERT — FIN-C2-097\n"
            f"Financial Market Anomaly & Trading Signal Detection Agent\n"
            f"════════════════════════════════════════════════════════════\n"
            f"\n"
            f"ALERT LEVEL:          {alert_level}\n"
            f"ANOMALY TYPE:         {anomaly_type.upper()}\n"
            f"SEVERITY SCORE:       {severity_score:.2f} / 1.00\n"
            f"\n"
            f"── FIEA Article 159 Assessment ─────────────────────────────\n"
            f"Reportability:        {fiea_line}\n"
            f"\n"
            f"── Statistical Indicators ──────────────────────────────────\n"
            f"Z-Score (max):        {z_score:.4f}\n"
            f"Cancellation Rate:    {cancel_rate:.2%}\n"
            f"Volume Spike:         {'YES' if volume_spike else 'NO'}\n"
            f"Triggered Flags:\n"
            f"{indicator_text}\n"
            f"\n"
            f"── SAR Draft (DRAFT ONLY — NOT FILED) ──────────────────────\n"
            f"A Suspicious Activity Report (SAR) draft has been prepared.\n"
            f"AUTOMATIC FILING IS PROHIBITED.\n"
            f"All filing decisions require human compliance officer review.\n"
            f"\n"
            f"── Processing Audit Trail ───────────────────────────────────\n"
            f"{audit_summary_text}\n"
            f"\n"
            f"════════════════════════════════════════════════════════════\n"
            f"[DRAFT] This output is for internal compliance review only.\n"
            f"No regulatory action has been taken automatically.\n"
            f"════════════════════════════════════════════════════════════"
        )

        audit_trail.append(
            f"[OUTPUT-FORMAT] Alert formatted: level={alert_level}, "
            f"anomaly_type={anomaly_type}, fiea_reportable={fiea_reportable}"
        )
        emit_trace_event(
            "output_formatted",
            {
                "agent": "FIN-C2-097",
                "alert_level": alert_level,
                "anomaly_type": anomaly_type,
                "fiea_reportable": fiea_reportable,
                "severity_score": severity_score,
            },
            state,
        )
        return {
            "result": alert,
            "audit_trail": audit_trail,
            "status": AgentStatus.SUCCESS.value,
        }
