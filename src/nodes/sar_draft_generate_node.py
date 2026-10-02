"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents

import datetime as _dt
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# ── Output gate (module-level, column 0 — not a class method) ─────────────────
#
# Enforces the auto-filing prohibition: any SAR draft content that contains
# auto-filing keywords is rejected immediately (RuntimeError → framework ERROR).
# The framework's @final _security_gate_output() also runs automatically after
# execute() and performs the default credential scan.

_AUTO_FILING_TERMS = [
    "auto-file",
    "auto-submit",
    "automatic filing",
    "automatic submission",
    "自動提出",
    "自動申告",
    "自動送信",
    "自動届出",
]


def _security_gate_output(content: str) -> str:
    """Domain output gate: reject SAR drafts containing auto-filing references.

    Called from SARDraftGenerateNode.execute() before returning the draft content.
    Any match raises RuntimeError; the framework converts unhandled exceptions in
    execute() to AgentStatus.ERROR.value.

    Returns the content unchanged when no prohibited terms are found.
    """
    lower = content.lower()
    for term in _AUTO_FILING_TERMS:
        if term.lower() in lower:
            raise RuntimeError(
                f"Output gate violation: prohibited auto-filing term '{term}' "
                "detected in SAR draft. Auto-filing is PROHIBITED — output rejected."
            )
    return content


# ── FIEA taxonomy descriptions ────────────────────────────────────────────────

_ANOMALY_DESCRIPTIONS = {
    "wash_trading": (
        "仮装売買 (Wash Trading) — Art. 159(1)(i): "
        "Fictitious transactions intended to create a misleading appearance of market activity. "
        "Same account detected on both buy and sell sides of transactions."
    ),
    "spoofing": (
        "見せ玉 (Spoofing) — Art. 159(1)(vi): "
        "Placement of large orders with intent to cancel before execution, "
        "creating a false impression of supply or demand. "
        "High order cancellation rate combined with anomalous volume z-score detected."
    ),
    "layering": (
        "レイヤリング (Layering) — Art. 159(1)(vi): "
        "Systematic placement and cancellation of multiple orders at different price levels "
        "to manipulate order book depth. "
        "Abnormal daily volume spike pattern detected."
    ),
    "none": ("No FIEA Article 159 anomaly pattern identified in the submitted trading data."),
}

_SAR_DISCLAIMER = (
    "\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "IMPORTANT DISCLAIMER — DRAFT ONLY\n"
    "This document is an AI-generated DRAFT for internal review purposes only.\n"
    "This report has NOT been filed with the Financial Services Agency (FSA).\n"
    "AUTOMATIC FILING IS STRICTLY PROHIBITED.\n"
    "All Suspicious Activity Reports (SARs) require human review and\n"
    "authorisation by a qualified compliance officer before submission.\n"
    "Filing decisions rest solely with authorised compliance personnel.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)


class SARDraftGenerateNode(FunctionNode):
    """Generate an FSA 疑わしい取引報告書 (Suspicious Activity Report) DRAFT.

    Produces a structured SAR draft template based on the anomaly classification
    from PatternClassifyNode. The draft is for human compliance review only.

    Critical constraints:
    - Output is ALWAYS labeled DRAFT
    - ALWAYS includes a disclaimer that auto-filing is prohibited
    - The output gate (module-level _security_gate_output) rejects any content
      containing auto-filing references before the draft is returned

    Auto-filing is a regulatory violation under FSA guidelines. This node
    will raise RuntimeError (→ AgentStatus.ERROR.value) if any prohibited terms
    are detected in the generated draft.

    Audit: emits "sar_draft_generated" domain audit event.

    Inner domain node — trust level ANONYMOUS.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        audit_trail = list(state.get("audit_trail") or [])
        anomaly_type = state.get("anomaly_type") or "none"
        severity_score = state.get("severity_score") or 0.0
        fiea_reportable = bool(state.get("fiea_reportable", False))
        raw_data = state.get("raw_trading_data") or {}

        draft_date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        anomaly_desc = _ANOMALY_DESCRIPTIONS.get(anomaly_type, _ANOMALY_DESCRIPTIONS["none"])

        order_count = len(raw_data.get("orders", []))
        account_count = len(raw_data.get("accounts", []))

        # Severity label
        if severity_score >= 0.7:
            severity_label = "HIGH"
        elif severity_score >= 0.4:
            severity_label = "MEDIUM"
        else:
            severity_label = "LOW"

        fiea_status = (
            "REPORTABLE — FIEA Article 159 threshold met"
            if fiea_reportable
            else "Below reporting threshold — FIEA Article 159 criteria not met"
        )

        draft_content = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"[DRAFT] 疑わしい取引報告書 / Suspicious Activity Report (SAR)\n"
            f"Financial Services Agency (FSA) Template — FIN-C2-097\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"Report Date:        {draft_date}\n"
            f"Status:             [DRAFT — NOT FILED — HUMAN REVIEW REQUIRED]\n"
            f"Prepared By:        FinancialMarketAnomalyTradingSignalDetectionAgent (AI)\n"
            f"\n"
            f"── Section 1: Anomaly Classification ───────────────────────\n"
            f"Anomaly Type:       {anomaly_type.upper()}\n"
            f"Severity Score:     {severity_score:.2f} / 1.00 ({severity_label})\n"
            f"FIEA Reportability: {fiea_status}\n"
            f"\n"
            f"── Section 2: Regulatory Reference ─────────────────────────\n"
            f"{anomaly_desc}\n"
            f"\n"
            f"── Section 3: Transaction Summary ───────────────────────────\n"
            f"Total Orders Analysed:   {order_count}\n"
            f"Unique Accounts:         {account_count}\n"
            f"\n"
            f"── Section 4: Compliance Action Required ────────────────────\n"
            f"[ ] Review flagged transactions against account history\n"
            f"[ ] Cross-reference with prior SAR records\n"
            f"[ ] Escalate to compliance officer for filing decision\n"
            f"[ ] File with FSA within required reporting window (if applicable)\n"
            f"\n"
            f"Filing Decision:    [ ] File with FSA  [ ] No action required\n"
            f"Authorised By:      ______________________________ (Compliance Officer)\n"
            f"Date Authorised:    ______________________________\n"
        )

        # Output gate — rejects auto-filing references
        draft_content = _security_gate_output(draft_content)
        sar_with_disclaimer = draft_content + _SAR_DISCLAIMER

        emit_trace_event(
            "sar_draft_generated",
            {
                "agent": "FIN-C2-097",
                "anomaly_type": anomaly_type,
                "severity_label": severity_label,
                "fiea_reportable": fiea_reportable,
                "draft_date": draft_date,
            },
            state,
        )
        audit_trail.append(
            f"[SAR-DRAFT] DRAFT generated for anomaly_type={anomaly_type}, "
            f"severity={severity_label}, fiea_reportable={fiea_reportable}. "
            f"Auto-filing: PROHIBITED. Human review required."
        )
        return {
            "sar_draft_content": sar_with_disclaimer,
            "audit_trail": audit_trail,
            "status": AgentStatus.SUCCESS.value,
        }
