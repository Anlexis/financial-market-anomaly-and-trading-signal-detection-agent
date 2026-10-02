# Test Specification — FIN-C2-097

Financial Market Anomaly & Trading Signal Detection Agent

## Test Strategy

- **Coverage target**: 85%+ on `src/nodes/` domain node logic
- **Test types**: Unit (per-node execute() logic + validation contract) +
  Proof-of-Boundary (framework compliance + end-to-end through the real ASGI entry point)
- **SDK**: `agenticstar-agentcore==1.0.1` (real wheel)

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | File |
|-------|------|----------------|------|
| TC-01 | State contract: flat TypedDict | `State` extends `AgentState`; no Pydantic, no dataclass | `test_state_safety.py` |
| TC-02 | `PreProcessNode.required_trust_level == VERIFIED_EXTERNAL` | Trust gate enforced at outer boundary | `test_trust_gate.py::TestTrustLevelMatrix` |
| TC-03 | All inner nodes `required_trust_level == ANONYMOUS` | Inner nodes declared ANONYMOUS | `test_trust_gate.py::TestTrustLevelMatrix` |
| TC-04 | No credential fields in `State` | `token`, `api_key`, `secret`, etc. absent | `test_state_safety.py` |
| TC-05 | No platform-internal imports in `src/` | AST scan: 0 violations | `test_import_isolation.py` |
| TC-06 | Default input gate cannot be overridden | Class definition raises | `test_framework_compliance_tc06_tc07.py` |
| TC-07 | Default output gate cannot be overridden | Class definition raises | `test_framework_compliance_tc06_tc07.py` |
| TC-08 | Trust gate fires before `execute()` | Denial carries no node-output keys | `test_trust_gate.py::TestTrustGateDenial` |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | File |
|-------|----------|------|----------------|------|
| PB-4 | Import isolation | No platform-internal imports under `src/` | AST scan: 0 violations | `test_import_isolation.py` |
| PB-2/5 | State serialization / checkpoint safety | State fields are primitives or JSON-serializable; no JWT / Pydantic | State scan pass | `test_state_safety.py` |
| PB-6 | Backbone invoke order (full pipeline) | `invoke(valid_payload, ctx=VERIFIED_EXTERNAL)` → `SUCCESS`; `node_history` order: `InitializeNode → PreProcessNode → TradingSignalGraphNode → PostProcessNode → FinalizeNode` | Order verified | `test_pb_invoke_order.py` |
| PB-7 | HITL interrupt propagation | Not applicable (no interrupt checkpoints) | Explicit skip stub | `test_pb7_hitl_interrupt_propagation.py` |
| PB-8 | End-to-end `/invoke` (ASGI, Bearer auth) | Real outputs per classification, validation rejects, output contract | All assertions pass | `test_pb_invoke_endpoint.py` |
| PB-9 | Entry-point auth boundary | Token opt-in, 401 generic body, upstream trust never demoted | All assertions pass | `test_server_boot.py` |

### PB-6 Critical Notes

- Caller trust level **must** be `TrustLevel.VERIFIED_EXTERNAL` — `PreProcessNode` requires it; using `INTERNAL` would mask inner-node trust-level violations.
- Payload must yield `AgentStatus.SUCCESS` — non-SUCCESS short-circuits `main→finalize` and omits `post_process`.
- Assert `result["output"]` (NOT `result["formatted_output"]`).
- Assert the status equals `AgentStatus.SUCCESS.value` (the plain string, not the enum member).

## Business Logic Tests

### TradingDataInputNode (`test_agent.py`, `test_validation_contract.py`)

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | Valid JSON payload | `{"orders":[...], "accounts":[...], "volumes":[...]}` | `SUCCESS`, `trading_data_validated=True`, `raw_trading_data` populated |
| BL-02 | Invalid JSON | `"not-json{"` | `ERROR`, `trading_data_validated=False` |
| BL-03 | Missing required key | payload without `accounts` / `volumes` | `ERROR` |
| BL-04 | Order missing required fields | Order without `account`, `price`, `type` | `ERROR` |
| BL-05 | `orders` not a list | `{"orders": "not-a-list", ...}` | `ERROR` |
| BL-06 | Non-finite numerics per field | `NaN` / `Infinity` / `-Infinity` / bools / over-magnitude in `volume`, `price`, daily `volume` | `ERROR`; field named; value never echoed |
| BL-07 | Structural limits | >10,000 entries; over-long strings; bad `type`; non-bool `cancelled` | `ERROR` |
| BL-08 | Post-parse content screen | `\u`-escaped control token, hostile mapping key, nested hostile text, PII in values | `ERROR`; content never echoed |
| BL-09 | Legitimate domain text passes | fiscal-year rows, ISO dates, buy/sell vocabulary | `SUCCESS` |

### StatisticalAnalyzeNode (`test_agent.py`, `test_validation_contract.py`)

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-10 | Normal trading data — no anomaly | 2 orders, similar volumes, 0 cancellations | `anomaly_indicators=[]`, `cancellation_rate=0.0`, `volume_spike_detected=False` |
| BL-11 | 100% order cancellation rate | All orders `cancelled=true` | `cancellation_rate=1.0`, indicator flagged |
| BL-12 | Volume spike >3× daily mean | One day volume = 10× others | `volume_spike_detected=True` |
| BL-13 | No `raw_trading_data` in state | `raw_trading_data=None` | `ERROR` |
| BL-14 | NaN volume driven directly | `float("nan")` in a volume | excluded from statistics; all flags finite |

### PatternClassifyNode (`test_agent.py`)

| TC-ID | Test | Statistical Flags | Expected Result |
|-------|------|-------------------|----------------|
| BL-20 | No anomaly indicators | No flags, z=0.5 | `anomaly_type="none"`, low severity, `fiea_reportable=False` |
| BL-21 | Wash trading (self-dealing + high cancel) | `cancellation_rate>0.3`, same account buy+sell | `anomaly_type="wash_trading"`, `fiea_reportable=True` |
| BL-22 | Spoofing (high z-score + high cancel, diff. accounts) | `z>2.5`, `cancellation>0.3` | `anomaly_type="spoofing"`, `fiea_reportable=True` |
| BL-23 | Layering (volume spike) | `volume_spike_detected=True` | `anomaly_type="layering"`, `fiea_reportable=True` |
| BL-24 | Severity score in [0.0, 1.0] | Any flags | `0.0 ≤ severity_score ≤ 1.0` |
| BL-25 | No statistical flags | `statistical_flags=None` | `ERROR` |

### SARDraftGenerateNode (`test_agent.py`)

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-30 | Draft generated for wash_trading | `anomaly_type="wash_trading"` | `SUCCESS`, `[DRAFT]` in content |
| BL-31 | DRAFT label always present | Any anomaly type | `[DRAFT]` in `sar_draft_content` |
| BL-32 | Disclaimer present | Any input | No-auto-filing disclaimer in draft |
| BL-33 | "none" anomaly still generates draft | `anomaly_type="none"` | `SUCCESS`, `[DRAFT]` in content |
| BL-34 | Output gate rejects auto-filing term | draft containing an auto-filing directive | raises (behavioural assertion — not wording) |
| BL-35 | Output gate passes clean content | Clean draft string | Returns string unchanged |
| BL-36 | Audit trail records generation | Any input | `[SAR-DRAFT]` entry in `audit_trail` |

### OutputFormatNode (`test_agent.py`)

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-40 | Full state → formatted alert | All domain fields populated | `SUCCESS`, `result` non-empty |
| BL-41 | Anomaly type uppercased in output | `anomaly_type="wash_trading"` | `WASH_TRADING` in `result` |
| BL-42 | Severity score in output | `severity_score=0.70` | `0.70` or `0.7` in `result` |
| BL-43 | FIEA reportability flag | `fiea_reportable=True` | `REPORTABLE` in `result` |
| BL-44 | DRAFT label in output | Any input | `[DRAFT]` in `result` |
| BL-45 | No auto-filing in output | Any input | Auto-filing prohibition text in `result` |
| BL-46 | "none" anomaly output valid | `anomaly_type="none"` | `SUCCESS`, `NONE` in `result` |

### Configuration Forwarding (`test_config_forwarding.py`)

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| CF-01 | `config/config.yaml` declares runtime values | `max_retry=3`, `timeout_s=30` |
| CF-02 | Reader reads the declared file | loader output equals the file contents |
| CF-03 | GraphNode forwards to the inner graph | inner `config["configurable"]` carries both values |
| CF-04 | Missing file degrades cleanly | `{"configurable": {}}`, no raise |
| CF-05 | Server passes config to the compiled graph | `agent.config` carries both values (PB-8) |

### End-to-End (`test_pb_invoke_endpoint.py`)

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| E2E-01 | Clean payload | `SUCCESS`; `[DRAFT]` alert; `NONE` / `LOW` |
| E2E-02 | Wash-trading payload | `WASH_TRADING`, `REPORTABLE`, `MEDIUM` |
| E2E-03 | Spoofing payload | `SPOOFING`, `REPORTABLE`, `HIGH` |
| E2E-04 | Layering payload | `LAYERING`, `REPORTABLE` |
| E2E-05 | Output varies with payload | different payloads → different alerts |
| E2E-06 | Validation rejects through the stack | missing keys / non-finite numerics → `ERROR`, nothing published, values never echoed |
| E2E-07 | Injection / PII refused | `ERROR`, nothing published |
| E2E-08 | Oversized `input_context` | 413 at the adapter |
| E2E-09 | Output contract scan | every alert `[DRAFT]`-labelled + prohibition reminder; no verbatim caller strings |

## Security Tests

| SEC-ID | Security Requirement | Test | Expected Result |
|--------|---------------------|------|----------------|
| SEC-01 | External trust gate | `test_trust_gate.py` — denial before `execute()`, admission at VERIFIED_EXTERNAL | Behavioural assertions pass |
| SEC-02 | Inner nodes use ANONYMOUS | All 5 inner domain nodes + PostProcessNode | `ANONYMOUS` on each |
| SEC-03 | No auto-filing output | `_security_gate_output()` rejects prohibited terms; `PostProcessNode` re-verifies the outward contract | Fail-closed on violation |
| SEC-04 | Domain audit events | `emit_trace_event` called in every `execute()` | ≥1 per node |
| SEC-05 | No hardcoded credentials | `grep -rnE "(password|api_key|secret).*=.*['\"]" src/` | 0 matches |
| SEC-06 | Caller numerics finite + bounded | `test_validation_contract.py` non-finite matrix per field | Fail-closed, field-naming errors |
| SEC-07 | Caller strings never render | E2E output scan | No verbatim caller strings in the alert |

## Test Execution Summary

| Field | Value |
|-------|-------|
| Test files: unit | `tests/unit/test_agent.py`, `test_trust_gate.py`, `test_validation_contract.py`, `test_config_forwarding.py`, `test_framework_compliance_tc06_tc07.py` |
| Test files: PB | `tests/proof_of_boundary/test_pb_invoke_order.py`, `test_pb_invoke_endpoint.py`, `test_server_boot.py`, `test_import_isolation.py`, `test_state_safety.py`, `test_pb7_hitl_interrupt_propagation.py` |
| Coverage target | ≥85% on `src/nodes/` |
