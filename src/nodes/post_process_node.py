"""AgentCore Platform v1.0"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result
from framework.security import detect_credentials_in_value

# The output invariant this template owns, enforced here — the last node this
# template controls before the response leaves the agent:
#   1. The outward response is a non-empty string.
#   2. It is explicitly labelled [DRAFT] — no output of this agent is ever a
#      filed or filing-ready document.
#   3. It carries the filing-prohibition reminder — filing is a human
#      compliance-officer decision, never an agent action.
# A violation fails CLOSED (ERROR, nothing published) rather than shipping an
# unlabelled report.
_DRAFT_LABEL = "[DRAFT]"
_PROHIBITION_MARKERS = ("PROHIBITED", "NOT FILED")

# Every state field that can carry generated report text onward. On a violation
# each one is cleared, because refusing is not the same as withholding: the
# outward response is assembled from the state the graph ends in, and it falls
# back to `result` whenever `formatted_output` is empty — so an ERROR status
# alone still ships the very draft this gate rejected.
#
# `formatted_output` is set to a NON-EMPTY notice on purpose. An empty string is
# falsy and would re-enable that fallback to `result`; a short withheld-notice
# both blocks it and tells the caller what happened.
_WITHHELD_NOTICE = (
    "[WITHHELD] The generated report did not meet this agent's outward labelling "
    "contract and has been withheld in full. No report text is released, and no "
    "regulatory filing or other regulatory action has been taken or prepared for "
    "submission. Re-run once the upstream fault is corrected; raise it with the "
    "compliance owner if it recurs."
)
_CLEARED_OUTPUT_FIELDS = ("result", "sar_draft_content", "hitl_draft")


class PostProcessNode(FunctionNode):
    """Enforce the output invariant and hand the result to FinalizeNode.

    Verifies the assembled alert satisfies the template's outward contract
    (non-empty, [DRAFT]-labelled, filing-prohibition reminder present), then
    copies it to `formatted_output`, which FinalizeNode maps to the final
    `output` key in the invoke() response.

    A violation returns ERROR *and clears every report-bearing state field*,
    replacing `formatted_output` with a withheld notice. Returning ERROR on its
    own is not containment: the outward response falls back to `result`
    regardless of status, so the rejected draft would still reach the caller.

    Audit: emits "post_process_complete" on success and
    "post_process_rejected" on an invariant violation.

    Outer backbone node — trust level ANONYMOUS.
    """

    # ANONYMOUS — outer PreProcessNode (VERIFIED_EXTERNAL) is the trust
    # boundary; post_process operates on already-validated data.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result", "")

        # Violation reasons name the CHECK that failed and nothing else. They
        # must never quote the offending text: the framework scans every value
        # of the dict returned below for credential patterns and raises on a
        # match — and a raise here discards this whole return value, including
        # the clearing, leaving the rejected draft in state to be shipped.
        _llm, _ = resolve_llm(None, state)
        _remarks = review_result(
            _llm,
            user_input=str(state.get("user_input") or ""),
            result=result,
            domain="FIN Financial Market Anomaly & Trading Signal Detection Agent",
        )
        _review = render_review(_remarks)
        # Remarks are LLM text derived from the caller's raw words, so they pass through the
        # same gate the answer does -- appending after the gate would put unscanned text past
        # it. A tripped review is dropped on its own: withholding a correct answer because an
        # advisory remark quoted an identifier would let the review change the outcome, and
        # the whole design rests on it being unable to.
        # This node has no domain credential scan -- its invariants are presence checks --
        # so the guard calls the framework detector directly. "No domain gate" is not "no
        # gate": the framework re-scans this node's returned dict and RAISES on a match,
        # which discards the whole delta. An advisory remark that turned a success into a
        # discarded error would be changing the outcome, which this design forbids.
        if _review and isinstance(result, str) and not detect_credentials_in_value(result + _review):
            result = result + _review

        violation = None
        if not isinstance(result, str) or not result.strip():
            violation = "empty or non-string result"
        elif _DRAFT_LABEL not in result:
            violation = "missing [DRAFT] label"
        elif not any(marker in result for marker in _PROHIBITION_MARKERS):
            violation = "missing filing-prohibition reminder"

        if violation is not None:
            # Assembled before anything that could fail, and returned on every
            # path out of this branch — an exception raised after this point
            # would replace it with the framework's generic node-error update,
            # which clears nothing.
            contained: dict[str, Any] = {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: output invariant violated — {violation}"],
                "audit_trail": list(state.get("audit_trail") or [])
                + [f"[POST-PROCESS][ERROR] Output invariant violated — {violation}. Report withheld in full."],
                "formatted_output": _WITHHELD_NOTICE,
            }
            for field in _CLEARED_OUTPUT_FIELDS:
                contained[field] = None
            try:
                emit_trace_event(
                    "post_process_rejected",
                    {"reason": violation, "agent": "FIN-C2-097"},
                    state,
                )
            except Exception:
                # Containment outranks the trace emit: a failed audit write is
                # recorded in the audit trail rather than allowed to propagate
                # and discard the cleared fields.
                contained["audit_trail"] = list(contained["audit_trail"]) + [
                    "[POST-PROCESS][ERROR] Rejection trace event could not be emitted; report withheld regardless."
                ]
            return contained

        emit_trace_event(
            "post_process_complete",
            {
                "agent": "FIN-C2-097",
                "result_length": len(result),
            },
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
