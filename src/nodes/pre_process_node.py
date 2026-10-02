"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Read input_context via state.get("input_context", {}) — read-only [C1]
#  - Never import from mediator/, api/, or other agents

import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# ── Input screens (module-level, column 0 — NOT an _extra_* node method) ──────
#
# The framework wheel auto-wraps `_extra_security_gate_*` instance methods into
# the LangGraph chain, which breaks in the e2e .invoke() path (None-state
# AttributeError). These screens are therefore plain module-level helpers
# called INLINE from execute(), before validated_input is written. Patterns
# are compiled once at import (column 0).
#
# Coverage:
#   1. Prompt-injection markers (ignore/disregard prior instructions, system
#      prompt exfiltration, role-override / jailbreak phrasing).
#   2. Chat-template control tokens (<|im_start|>, [INST], <<SYS>>, ...) — the
#      token form of a role-override attack; phrase patterns alone miss it.
#   3. SQL-injection markers (UNION SELECT, DROP TABLE, stacked ; --, tautology).
#   4. Script / HTML-injection markers (<script>, javascript:, on*= handlers).
#   5. PII (email / phone / credit-card) — no PII belongs in structured trading
#      data; a hit indicates a malformed or malicious payload.
#
# These helpers are also imported by TradingDataInputNode, which re-runs them
# over the PARSED payload (keys and string values), so \u-escaped content
# that the raw-text scan cannot see is still screened after json.loads().
#
# Calibrated to NOT false-positive on the SUCCESS + Proof-of-Boundary JSON
# trading-data payloads (orders/accounts/volumes, ISO timestamps, ISO dates,
# integer volumes/prices) — verified in tests/unit/test_agent.py.

_INJECTION_PATTERNS = (
    # ── prompt injection ──
    re.compile(
        r"\bignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|preceding|earlier)\s+"
        r"(?:instruction|prompt|context|message|rule)s?\b",
        re.I,
    ),
    re.compile(
        r"\bdisregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier|system)\b",
        re.I,
    ),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(
        r"\b(?:reveal|show|print|leak|expose|repeat)\s+(?:me\s+)?(?:your\s+|the\s+)?"
        r"(?:system\s+)?(?:prompt|instruction|secret|password|api[\s_-]?key)s?\b",
        re.I,
    ),
    re.compile(
        r"\bact\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a\s+|an\s+)?"
        r"(?:different|new|unrestricted|jailbroken|dan)\b",
        re.I,
    ),
    # ── chat-template control tokens (token-form role override) ──
    re.compile(r"<\|\s*(?:im_start|im_end|system|user|assistant|endoftext)\s*\|>", re.I),
    re.compile(r"\[/?INST\]", re.I),
    re.compile(r"<</?SYS>>"),
    re.compile(r"<\|[a-z_]{2,24}\|>", re.I),
    # ── SQL injection ──
    re.compile(
        r"\b(?:union\s+select|drop\s+table|insert\s+into|delete\s+from|"
        r"truncate\s+table|update\s+\w+\s+set|select\s+.+\s+from\s+\w)\b",
        re.I,
    ),
    re.compile(r"(?:'|\")\s*(?:or|and)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+", re.I),  # ' OR 1=1
    re.compile(r";\s*--"),  # stacked-query / SQL comment
    re.compile(r"/\*.*?\*/", re.S),  # SQL block comment
    # ── script / HTML injection ──
    re.compile(r"<\s*script\b", re.I),
    re.compile(r"</\s*script\s*>", re.I),
    re.compile(r"javascript\s*:", re.I),
    re.compile(r"\bon(?:error|load|click|mouseover|focus)\s*=", re.I),
    re.compile(r"\beval\s*\(", re.I),
)

_PII_PATTERNS = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"),
    ),
    (
        "credit_card",
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?"  # Visa (13 or 16)
            r"|5[1-5][0-9]{14}"  # Mastercard (16)
            r"|3[47][0-9]{13}"  # Amex (15)
            r"|6(?:011|5[0-9]{2})[0-9]{12})\b"  # Discover (16)
            # Grouped 16-digit form, anchored to a real issuer prefix on the
            # first group and to a consistent separator, so numeric domain text
            # (fiscal-year rows "2023 2024 2025 2026", date-like runs) does not
            # match. Both directions probed in tests/unit/test_agent.py.
            r"|\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
            r"(?:([- ])\d{4}\1\d{4}\1\d{4})\b"
        ),
    ),
)


def _scan_injection(text: str) -> str | None:
    """Return the matched injection pattern string on a hit, else None."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def _scan_pii(text: str) -> str | None:
    """Return the PII category label on a hit (email/phone/credit_card), else None."""
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return label
    return None


# Caller-metadata contract: the only input_context field this pipeline
# consumes is ``channel`` — a short transport label that is written into the
# audit trail. It is locked to an inert identifier so caller-controlled free
# text can never ride into audit output.
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,32}$")


class PreProcessNode(FunctionNode):
    """Validate and enrich incoming trading data input before domain processing.

    Trust gate: VERIFIED_EXTERNAL — only verified external callers may submit
    trading data for analysis. This is the security boundary for all inbound
    requests to the FIN-C2-097 pipeline.

    Input screens (enforced in execute(), before validated_input is written):
      - Type guard: user_input must be a str.
      - Injection scan: prompt-injection phrases + chat-template control
        tokens + SQL/script/HTML markers.
      - PII scan: email / phone / credit-card.
      - Caller metadata: ``input_context`` must be a mapping; its ``channel``
        field, when present, must be an inert identifier ([a-z0-9_]{1,32}).
    Audit: emit_trace_event(...) on every rejection path AND the success path.
    """

    # External trust gate: only VERIFIED_EXTERNAL callers pass.
    # Inner domain nodes are ANONYMOUS — the outer gate is the boundary.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only [C1]

        # ── caller metadata: input_context must be a mapping ──────────────────
        if not isinstance(input_context, dict):
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "invalid_input_context", "agent": "FIN-C2-097"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: input_context must be a mapping"],
                "audit_trail": ["[PRE-PROCESS][ERROR] Non-mapping input_context — rejected"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("PreProcessNode: input_context must be a mapping"),
            }

        # ── caller metadata: channel is locked to an inert identifier ─────────
        # The value is rendered into the audit trail, so free text here would
        # be caller-controlled output injection. The rejected value is never
        # echoed — the error names the field only.
        channel = input_context.get("channel", "unknown")
        if not (isinstance(channel, str) and _CHANNEL_RE.fullmatch(channel)):
            if "channel" in input_context:
                emit_trace_event(
                    "pre_process_rejected",
                    {"reason": "invalid_caller_metadata", "field": "channel", "agent": "FIN-C2-097"},
                    state,
                )
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": ["PreProcessNode: input_context field 'channel' failed validation"],
                    "audit_trail": ["[PRE-PROCESS][ERROR] Invalid caller metadata field 'channel' — rejected"],
                }
            channel = "unknown"

        # ── type guard — user_input MUST be a string ──────────────────────────
        if not isinstance(user_input, str):
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "invalid_type", "agent": "FIN-C2-097"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input must be a string " f"(got {type(user_input).__name__})"],
                "audit_trail": ["[PRE-PROCESS][ERROR] Non-string user_input — rejected"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + (f"PreProcessNode: user_input must be a string (got {type(user_input).__name__})"),
            }

        # ── empty / whitespace-only rejection ─────────────────────────────────
        if not user_input.strip():
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "empty_input", "agent": "FIN-C2-097"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
                "audit_trail": ["[PRE-PROCESS][ERROR] Empty or missing user_input — rejected"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("PreProcessNode: user_input is empty or missing"),
            }

        stripped = user_input.strip()

        # ── injection scan ────────────────────────────────────────────────────
        injection_hit = _scan_injection(stripped)
        if injection_hit is not None:
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "injection_pattern", "agent": "FIN-C2-097"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: potential injection pattern detected in input — rejected"],
                "audit_trail": ["[PRE-PROCESS][ERROR] Injection pattern detected — rejected"],
            }

        # ── PII scan ──────────────────────────────────────────────────────────
        pii_hit = _scan_pii(stripped)
        if pii_hit is not None:
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "pii_detected", "category": pii_hit, "agent": "FIN-C2-097"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: PII ({pii_hit}) detected in input — rejected"],
                "audit_trail": [f"[PRE-PROCESS][ERROR] PII ({pii_hit}) detected — rejected"],
            }

        # ── success ───────────────────────────────────────────────────────────
        emit_trace_event(
            "pre_process_validated",
            {
                "agent": "FIN-C2-097",
                "input_length": len(stripped),
                "channel": channel,
            },
            state,
        )
        return {
            "validated_input": stripped,
            "enriched_context": {
                "source": "FinancialMarketAnomalyTradingSignalDetectionAgent",
                "channel": channel,
            },
            "audit_trail": [f"[PRE-PROCESS] Input validated, length={len(stripped)}, " f"channel={channel}"],
            "status": AgentStatus.SUCCESS.value,
        }
