"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants — never plain strings [A1]
#  - Never import from mediator/, api/, or other agents

import json
import math
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.pre_process_node import _scan_injection, _scan_pii

# Required top-level keys in structured trading data payload.
_REQUIRED_KEYS = frozenset({"orders", "accounts", "volumes"})

# Required fields within each order record.
_ORDER_REQUIRED_FIELDS = frozenset({"id", "account", "volume", "price", "type"})

# Required fields within each account / volume record.
_ACCOUNT_REQUIRED_FIELDS = frozenset({"id", "entity"})
_VOLUME_REQUIRED_FIELDS = frozenset({"symbol", "volume", "date"})

# ── Explicit caller-data bounds ───────────────────────────────────────────────
#
# Every caller-controlled number goes through _finite_in_range (finite AND
# bounded — NaN/Infinity parse fine as floats, and raw JSON even carries bare
# NaN/Infinity tokens, but every ordered comparison against NaN is False, so an
# unchecked value silently disables the exact statistics this template exists
# to compute). Every caller string is type- and length-capped. Rejections name
# the field path, never the value.

_MAX_ORDERS = 10_000
_MAX_ACCOUNTS = 10_000
_MAX_VOLUME_RECORDS = 10_000

_ORDER_VOLUME_MIN, _ORDER_VOLUME_MAX = 0.0, 1e12  # shares per order
_PRICE_MIN, _PRICE_MAX = 0.0, 1e9  # price per unit
_DAILY_VOLUME_MIN, _DAILY_VOLUME_MAX = 0.0, 1e15  # shares per day

_MAX_ID_LEN = 64  # order id / account id / symbol
_MAX_ENTITY_LEN = 256  # legal entity name
_MAX_DATE_LEN = 64  # date / timestamp strings (not parsed, only carried)

_ORDER_TYPES = frozenset({"buy", "sell"})

# Post-parse content screen: nested structures deeper than this are hostile by
# construction for this flat schema (records sit two levels down).
_MAX_SCAN_DEPTH = 6


def _extra_security_gate_input(user_input: str) -> None:
    """Domain input gate: reject inputs that exceed safe size limits."""
    if len(user_input) > 1_000_000:
        raise ValueError("TradingDataInputNode: input payload exceeds 1 MB size limit")


def _finite_in_range(value: Any, lo: float, hi: float) -> tuple[bool, float]:
    """Parse a caller-controlled numeric defensively; fail CLOSED.

    Accepts int/float only (bool is explicitly rejected — it is an int
    subclass). NaN and +/-Infinity parse fine as floats but every ordered
    comparison against them returns False, which would silently disable any
    bound built on such a comparison — so non-finite values are rejected
    outright, as are values outside [lo, hi].

    Returns (ok, parsed_value); parsed_value is 0.0 whenever ok is False.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, 0.0
    num = float(value)
    if not math.isfinite(num):
        return False, 0.0
    if num < lo or num > hi:
        return False, 0.0
    return True, num


def _scan_parsed(data: Any, path: str = "payload", depth: int = 0) -> str | None:
    """Screen the PARSED payload — keys included — for hostile content.

    The raw-text screens in PreProcessNode cannot see through JSON \\u
    escapes; after json.loads() the decoded text is visible, so every string
    in the parsed structure (mapping KEYS as much as values) is re-screened
    here. Returns a structural path label on a hit ("orders[3]"), never the
    matched content — list indices and our own schema key names are safe to
    echo, caller-chosen key names and values are not.
    """
    if depth > _MAX_SCAN_DEPTH:
        return path
    if isinstance(data, str):
        if _scan_injection(data) is not None or _scan_pii(data) is not None:
            return path
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and (_scan_injection(key) is not None or _scan_pii(key) is not None):
                return f"{path} (mapping key)"
            hit = _scan_parsed(value, path, depth + 1)
            if hit is not None:
                return hit
        return None
    if isinstance(data, list):
        for idx, item in enumerate(data):
            hit = _scan_parsed(item, f"{path}[{idx}]", depth + 1)
            if hit is not None:
                return hit
        return None
    return None


class TradingDataInputNode(FunctionNode):
    """Ingest and validate structured trading data for anomaly analysis.

    Accepts a JSON-encoded payload with keys:
      orders   — list of order records (id, account, volume, price, type, [cancelled], [timestamp])
      accounts — list of account records (id, entity)
      volumes  — list of daily volume records (symbol, volume, date)

    Validation contract (fail CLOSED, field-naming errors, values never
    echoed):
      - Input size gate (1 MB) before parsing.
      - Strict JSON schema: required keys, list types, per-record required
        fields, entry caps on every list.
      - Every caller number finite AND bounded (_finite_in_range) — bools,
        NaN, +/-Infinity and over-magnitude values are rejected.
      - Every caller string type- and length-capped; order type restricted to
        buy/sell; cancelled restricted to a JSON boolean.
      - Post-parse content screen over keys and values (injection + PII),
        closing the JSON \\u-escape blind spot of the raw-text screens.
    Audit: emits "trading_data_ingested" on success and
    "trading_data_input_rejected" on every rejection path.

    Inner domain node — trust level ANONYMOUS (outer PreProcessNode is the
    VERIFIED_EXTERNAL gate; inner nodes inherit the validated trust context).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _reject(self, state: dict[str, Any], audit_trail: list[str], reason: str, message: str) -> dict[str, Any]:
        """Build the fail-closed rejection dict; names fields, never values."""
        emit_trace_event(
            "trading_data_input_rejected",
            {"reason": reason, "agent": "FIN-C2-097"},
            state,
        )
        audit_trail.append(f"[TRADING-DATA-INPUT][ERROR] {message}")
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [f"TradingDataInputNode: {message}"],
            "trading_data_validated": False,
            "audit_trail": audit_trail,
            # The runner surfaces `formatted_output or result` as `output`. A reason left only in
            # error_log reaches no one: the terminal result carries just `status`, and get_output()
            # does not copy error_log out of the graph -- the caller sees a blank spinner.
            "formatted_output": "Request could not be completed. " + (f"TradingDataInputNode: {message}"),
        }

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("validated_input", state.get("user_input", ""))
        audit_trail = list(state.get("audit_trail") or [])

        # Input size gate
        try:
            _extra_security_gate_input(user_input)
        except ValueError:
            return self._reject(
                state,
                audit_trail,
                "size_limit_exceeded",
                "input payload exceeds 1 MB size limit",
            )

        # JSON parse (bare NaN/Infinity tokens parse here as floats — they are
        # rejected below by the per-field finite checks, not by the parser)
        try:
            data = json.loads(user_input)
        except (json.JSONDecodeError, TypeError):
            return self._reject(
                state,
                audit_trail,
                "invalid_json",
                "payload is not valid JSON",
            )

        if not isinstance(data, dict):
            return self._reject(
                state,
                audit_trail,
                "not_a_dict",
                "payload root must be a JSON object",
            )

        # Required-key check
        missing = _REQUIRED_KEYS - set(data.keys())
        if missing:
            return self._reject(
                state,
                audit_trail,
                "missing_keys",
                f"missing required keys {sorted(missing)}",
            )

        orders = data.get("orders", [])
        accounts = data.get("accounts", [])
        volumes = data.get("volumes", [])

        if not isinstance(orders, list) or not isinstance(accounts, list) or not isinstance(volumes, list):
            return self._reject(
                state,
                audit_trail,
                "invalid_list_types",
                "orders/accounts/volumes must be lists",
            )

        # Entry caps — structural limits before any per-record work
        if len(orders) > _MAX_ORDERS:
            return self._reject(
                state,
                audit_trail,
                "too_many_entries",
                f"orders exceeds the {_MAX_ORDERS}-entry limit",
            )
        if len(accounts) > _MAX_ACCOUNTS:
            return self._reject(
                state,
                audit_trail,
                "too_many_entries",
                f"accounts exceeds the {_MAX_ACCOUNTS}-entry limit",
            )
        if len(volumes) > _MAX_VOLUME_RECORDS:
            return self._reject(
                state,
                audit_trail,
                "too_many_entries",
                f"volumes exceeds the {_MAX_VOLUME_RECORDS}-entry limit",
            )

        # Post-parse content screen (keys + values) — closes the \u-escape
        # blind spot of the raw-text screens in PreProcessNode.
        hit = _scan_parsed(data)
        if hit is not None:
            return self._reject(
                state,
                audit_trail,
                "screened_content",
                f"screened content detected at {hit}",
            )

        # Per-record validation: orders
        for idx, order in enumerate(orders):
            if not isinstance(order, dict):
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_order_record",
                    f"order[{idx}] is not a JSON object",
                )
            missing_order_fields = _ORDER_REQUIRED_FIELDS - set(order.keys())
            if missing_order_fields:
                return self._reject(
                    state,
                    audit_trail,
                    "missing_order_fields",
                    f"order[{idx}] missing required fields " f"{sorted(missing_order_fields)}",
                )
            for field in ("id", "account"):
                value = order[field]
                if not isinstance(value, str) or not value or len(value) > _MAX_ID_LEN:
                    return self._reject(
                        state,
                        audit_trail,
                        "invalid_order_field",
                        f"order[{idx}].{field} must be a non-empty string of at " f"most {_MAX_ID_LEN} characters",
                    )
            order_type = order["type"]
            if not isinstance(order_type, str) or order_type.lower() not in _ORDER_TYPES:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_order_field",
                    f"order[{idx}].type must be one of {sorted(_ORDER_TYPES)}",
                )
            ok, _ = _finite_in_range(order["volume"], _ORDER_VOLUME_MIN, _ORDER_VOLUME_MAX)
            if not ok:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_order_field",
                    f"order[{idx}].volume must be a finite number in "
                    f"[{_ORDER_VOLUME_MIN:g}, {_ORDER_VOLUME_MAX:g}]",
                )
            ok, _ = _finite_in_range(order["price"], _PRICE_MIN, _PRICE_MAX)
            if not ok:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_order_field",
                    f"order[{idx}].price must be a finite number in " f"[{_PRICE_MIN:g}, {_PRICE_MAX:g}]",
                )
            if "cancelled" in order and not isinstance(order["cancelled"], bool):
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_order_field",
                    f"order[{idx}].cancelled must be a JSON boolean",
                )
            if "timestamp" in order:
                ts = order["timestamp"]
                if not isinstance(ts, str) or len(ts) > _MAX_DATE_LEN:
                    return self._reject(
                        state,
                        audit_trail,
                        "invalid_order_field",
                        f"order[{idx}].timestamp must be a string of at most " f"{_MAX_DATE_LEN} characters",
                    )

        # Per-record validation: accounts
        for idx, account in enumerate(accounts):
            if not isinstance(account, dict):
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_account_record",
                    f"account[{idx}] is not a JSON object",
                )
            missing_account_fields = _ACCOUNT_REQUIRED_FIELDS - set(account.keys())
            if missing_account_fields:
                return self._reject(
                    state,
                    audit_trail,
                    "missing_account_fields",
                    f"account[{idx}] missing required fields " f"{sorted(missing_account_fields)}",
                )
            acc_id = account["id"]
            if not isinstance(acc_id, str) or not acc_id or len(acc_id) > _MAX_ID_LEN:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_account_field",
                    f"account[{idx}].id must be a non-empty string of at most " f"{_MAX_ID_LEN} characters",
                )
            entity = account["entity"]
            if not isinstance(entity, str) or len(entity) > _MAX_ENTITY_LEN:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_account_field",
                    f"account[{idx}].entity must be a string of at most " f"{_MAX_ENTITY_LEN} characters",
                )

        # Per-record validation: volumes
        for idx, vol_rec in enumerate(volumes):
            if not isinstance(vol_rec, dict):
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_volume_record",
                    f"volume[{idx}] is not a JSON object",
                )
            missing_volume_fields = _VOLUME_REQUIRED_FIELDS - set(vol_rec.keys())
            if missing_volume_fields:
                return self._reject(
                    state,
                    audit_trail,
                    "missing_volume_fields",
                    f"volume[{idx}] missing required fields " f"{sorted(missing_volume_fields)}",
                )
            symbol = vol_rec["symbol"]
            if not isinstance(symbol, str) or not symbol or len(symbol) > _MAX_ID_LEN:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_volume_field",
                    f"volume[{idx}].symbol must be a non-empty string of at " f"most {_MAX_ID_LEN} characters",
                )
            ok, _ = _finite_in_range(vol_rec["volume"], _DAILY_VOLUME_MIN, _DAILY_VOLUME_MAX)
            if not ok:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_volume_field",
                    f"volume[{idx}].volume must be a finite number in "
                    f"[{_DAILY_VOLUME_MIN:g}, {_DAILY_VOLUME_MAX:g}]",
                )
            date = vol_rec["date"]
            if not isinstance(date, str) or len(date) > _MAX_DATE_LEN:
                return self._reject(
                    state,
                    audit_trail,
                    "invalid_volume_field",
                    f"volume[{idx}].date must be a string of at most " f"{_MAX_DATE_LEN} characters",
                )

        emit_trace_event(
            "trading_data_ingested",
            {
                "agent": "FIN-C2-097",
                "order_count": len(orders),
                "account_count": len(accounts),
                "volume_record_count": len(volumes),
            },
            state,
        )
        audit_trail.append(
            f"[TRADING-DATA-INPUT] Ingested {len(orders)} orders, "
            f"{len(accounts)} accounts, {len(volumes)} volume records"
        )
        return {
            "trading_data_validated": True,
            "raw_trading_data": {
                "orders": orders,
                "accounts": accounts,
                "volumes": volumes,
            },
            "audit_trail": audit_trail,
            "status": AgentStatus.SUCCESS.value,
        }
