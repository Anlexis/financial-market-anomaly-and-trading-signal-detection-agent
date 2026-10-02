# Template Design Specification — FIN-C2-097

## Position in the Framework Architecture

| Aspect | Value |
|---|---|
| Agent Class | `FinancialMarketAnomalyTradingSignalDetectionAgent` |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance (Cat 2 nested: outer `AgentBaseGraph` + inner `BaseGraph`) |
| Category | Cat 2 — FIN industry-specific multi-step domain workflow |
| Industry | FIN (Financial Services) |
| State | flat TypedDict composition (`src/schemas/state.py` — no Pydantic; msgpack incompatible) |
| Nodes | framework `FunctionNode` subclasses (Template Method: `execute(self, state) -> dict` override only) |
| Graph | composition (`register_nodes()` for node substitution) |

## Use Case Summary

Detects market manipulation and trading anomalies in Japanese financial markets (FIEA Article 159
compliance). Ingests structured trading data (order books, account activity, volume data), applies
statistical analysis (z-score, volume spike, cancellation rate), classifies anomaly patterns
(wash trading / spoofing / layering), and generates a FSA 疑わしい取引報告書 DRAFT for manual
human review.

**Critical constraint**: Automatic SAR filing is PROHIBITED (regulatory violation). This agent
produces a DRAFT only; all filing decisions require human authorization. Enforced by the
output gate in `SARDraftGenerateNode` and re-verified at the outer boundary by
`PostProcessNode`.

**Out of scope**: Vibe-Trading execution features, live order placement, automated regulatory
submission, real-time market data feeds.

## Architecture Overview (Cat 2 Nested)

### Outer Backbone (fixed 5-node)

```
START → initialize → pre_process → main (TradingSignalGraphNode) → post_process → finalize → END
                                           ↓ (retry, max 3)
                                      pre_process
```

- `initialize` — default `InitializeNode` (schema_version, session_id, trust_level)
- `pre_process` — `PreProcessNode` (trust gate VERIFIED_EXTERNAL; input validation)
- `main` — `TradingSignalGraphNode` (GraphNode wrapping inner domain workflow)
- `post_process` — `PostProcessNode` (format final output)
- `finalize` — default `FinalizeNode` (response_metadata, total_time_ms)

### Inner Domain Workflow (TradingSignalDomainWorkflowGraph)

```
START → trading_data_input → statistical_analyze → pattern_classify
      → sar_draft_generate → output_format → END
```

All inner nodes inherit `FunctionNode` with `required_trust_level = TrustLevel.ANONYMOUS`.

### Node Configuration

| Node | Class | Slot | trust_level | Responsibility |
|------|-------|------|-------------|----------------|
| initialize | `InitializeNode` (default) | backbone | — | Session bootstrap |
| pre_process | `PreProcessNode` | outer backbone | `VERIFIED_EXTERNAL` | Input screens; trust gate |
| main | `TradingSignalGraphNode` | outer backbone | — | GraphNode; invokes inner workflow |
| post_process | `PostProcessNode` | outer backbone | `ANONYMOUS` | Output-invariant enforcement |
| finalize | `FinalizeNode` (default) | backbone | — | Response assembly |
| trading_data_input | `TradingDataInputNode` | inner | `ANONYMOUS` | JSON ingest; full caller-data validation contract |
| statistical_analyze | `StatisticalAnalyzeNode` | inner | `ANONYMOUS` | Z-score, volume spike, cancellation rate |
| pattern_classify | `PatternClassifyNode` | inner | `ANONYMOUS` | FIEA Art 159 taxonomy; severity scoring |
| sar_draft_generate | `SARDraftGenerateNode` | inner | `ANONYMOUS` | FSA SAR DRAFT population; auto-filing output gate |
| output_format | `OutputFormatNode` | inner | `ANONYMOUS` | Alert assembly; audit trail |

### State Definition (`src/schemas/state.py`)

Extends `AgentState` (flat TypedDict).

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `trading_data_validated` | `Optional[bool]` | Ingest validation flag | TradingDataInputNode |
| `raw_trading_data` | `Optional[dict]` | Parsed trading data (orders/accounts/volumes) | TradingDataInputNode |
| `statistical_flags` | `Optional[dict]` | Z-score, volume spike, cancellation rate flags | StatisticalAnalyzeNode |
| `anomaly_type` | `Optional[str]` | `wash_trading` / `spoofing` / `layering` / `none` | PatternClassifyNode |
| `severity_score` | `Optional[float]` | Alert severity 0.0–1.0 | PatternClassifyNode |
| `fiea_reportable` | `Optional[bool]` | FIEA Article 159 reportability flag | PatternClassifyNode |
| `sar_draft_content` | `Optional[str]` | FSA SAR DRAFT text (labeled DRAFT, no filing) | SARDraftGenerateNode |
| `audit_trail` | `Optional[list]` | Immutable ordered list of processing entries | Accumulated |

**State Constraints (mandatory)**:
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

### Data Flow

```
user_input (JSON trading data)
    │
    ▼ PreProcessNode (VERIFIED_EXTERNAL)
validated_input
    │
    ▼ TradingSignalGraphNode → invoke inner graph
        │
        ▼ TradingDataInputNode
        raw_trading_data, trading_data_validated
            │
            ▼ StatisticalAnalyzeNode
            statistical_flags (z_score_max, volume_spike, cancellation_rate)
                │
                ▼ PatternClassifyNode
                anomaly_type, severity_score, fiea_reportable
                    │
                    ▼ SARDraftGenerateNode [output gate]
                    sar_draft_content (DRAFT labeled, no auto-filing)
                        │
                        ▼ OutputFormatNode
                        result (formatted alert with all findings)
    │
    ▼ (merge_output → outer state: result, status, audit_trail)
    │
    ▼ PostProcessNode
    formatted_output
        │
        ▼ FinalizeNode
        output (invoke result)
```

## Security Design

### Trust Gate

`PreProcessNode` requires `TrustLevel.VERIFIED_EXTERNAL` — only verified external callers may
submit trading data for analysis. Unauthenticated callers are rejected at the backbone level
before any domain processing begins.

### Input Screens (PreProcessNode)

Module-level screens run inline from `execute()`, before `validated_input` is written:
- Injection scan: prompt-injection phrasing, chat-template control tokens
  (`<|im_start|>`, `[INST]`, `<<SYS>>`, ...), SQL/script/HTML markers.
- PII scan: email / phone / credit-card. The grouped credit-card form is anchored to real
  issuer prefixes and a consistent separator, so numeric domain text (fiscal-year rows,
  ISO dates) does not false-positive — probed in both directions in the test suite.
- Caller metadata: `input_context` must be a mapping; its `channel` field (the only
  consumed key, rendered into the audit trail) is locked to an inert identifier
  `[a-z0-9_]{1,32}`. Rejections name the field, never the value.

### Caller-Data Validation Contract (TradingDataInputNode)

Strict, fail-closed validation of the JSON trading payload. Rejections name the failing
field path (e.g. `order[3].volume`) and never echo the rejected value.

| Field | Rule |
|---|---|
| root | JSON object with `orders` / `accounts` / `volumes` lists |
| list sizes | ≤ 10,000 entries each; payload ≤ 1 MB before parsing |
| `orders[].id`, `orders[].account` | non-empty string ≤ 64 chars |
| `orders[].type` | `buy` or `sell` |
| `orders[].volume` | finite number in [0, 1e12] — bools, NaN, ±Infinity, over-magnitude all rejected |
| `orders[].price` | finite number in [0, 1e9] |
| `orders[].cancelled` | JSON boolean (optional; defaults false) |
| `orders[].timestamp` | string ≤ 64 chars (optional; carried, not parsed) |
| `accounts[].id` | non-empty string ≤ 64 chars |
| `accounts[].entity` | string ≤ 256 chars |
| `volumes[].symbol` | non-empty string ≤ 64 chars |
| `volumes[].volume` | finite number in [0, 1e15] |
| `volumes[].date` | string ≤ 64 chars |

NaN and Infinity deserve emphasis: they parse fine (both as raw JSON tokens and via
`float()`), and every ordered comparison against NaN returns False — an unchecked NaN
volume silently blanks the exact statistics this template exists to compute. The
`_finite_in_range` parser rejects them outright, and `StatisticalAnalyzeNode` carries a
second finite guard as defense in depth.

After parsing, the whole structure — mapping keys included — is re-screened with the
injection and PII scans. The raw-text screens cannot see through JSON `\u` escapes; the
post-parse scan closes that blind spot.

### Output Gate (No Auto-Filing)

`SARDraftGenerateNode` enforces the auto-filing prohibition via a module-level
`_security_gate_output()` helper called from `execute()`. Any SAR draft containing
auto-filing references raises `RuntimeError` (framework converts to ERROR status).
All SAR outputs carry DRAFT label and human-review disclaimer.

`PostProcessNode` independently re-verifies the outward contract on the assembled alert —
non-empty, `[DRAFT]`-labelled, filing-prohibition reminder present — and fails CLOSED
(ERROR, nothing published) on a violation.

### Output Precision

This template renders **no monetary aggregates**: the outward alert carries statistical
indicators (z-score, cancellation rate, spike ratio), record counts and taxonomy
classifications — volumes are share counts and order prices are never rendered. There is
therefore no numeric rounding/summary grid to enforce at the output boundary. The output
invariants this template owns instead are: every response is `[DRAFT]`-labelled with the
filing-prohibition reminder (enforced fail-closed in `PostProcessNode`), and **no
caller-supplied string is ever reproduced verbatim in the alert** — identifiers and entity
names feed the statistics but never render, which is proven by the end-to-end output scan
in the test suite.

### Audit Logging

Every `execute()` method emits at least one domain-specific event via
`emit_trace_event(event, payload, state)` (positional — all three args required).
Framework automatically emits `node_start` / `node_complete` / `node_error` — domain events
must NOT duplicate these names.

### Credential Safety

No credentials, API keys, or secrets in State or node code. External integrations use
`framework.secrets.context.bound_secrets` and `shared.secrets.factory` (see `server.py`).
`_security_gate_output()` (framework `@final`) runs automatically after every `execute()` and
scans all string values for credentials.

## Configuration Contract

`config/agent.yaml` is the flat static manifest (identity, entry point, trust level,
`requires`). Runtime parameters live in `config/config.yaml`:

| Key | Value | Consumer |
|---|---|---|
| `max_retry` | 3 | outer backbone retry routing |
| `timeout_s` | 30 | forwarded to the inner graph under `configurable` |

The platform registry loads `config/config.yaml` and passes it to the graph constructor;
the standalone server (`src/api/server.py`) does the same. `TradingSignalGraphNode`
forwards the declared values to the inner graph under the LangGraph `configurable` key —
the forwarding is proven end-to-end in the test suite, because a reader pointed at a
retired location returns `{}` silently and every declared value goes dead.

The statistical thresholds (z-score 2.5, cancellation rate 0.30, volume-spike multiplier
3.0) are module-level code defaults in `StatisticalAnalyzeNode`, not configuration —
`config/config.yaml` declares no threshold keys and no node reads any.

## Caller Metadata Channel

`/invoke` accepts an optional `input_context` mapping (≤ 256 KB, enforced at the HTTP
adapter). The only consumed field is `channel`, validated and rendered by the outer
`PreProcessNode`. No inner node reads `input_context`, so no outer-to-inner context bridge
is needed — the caller data proper travels as the `user_input` JSON payload, which
`extract_input()` hands to the inner graph.

## Framework Utilization

### Shared Components Used

- [x] `InvocationContext` — trust level propagation from outer to inner graph
- [x] `FunctionNode` — all domain nodes; `execute()` template method
- [x] `GraphNode` — `TradingSignalGraphNode` (main slot); `get_subgraph()` / `extract_input()` / `merge_output()`
- [x] `AgentBaseGraph` — outer graph backbone
- [x] `BaseGraph` — inner domain workflow (custom topology, 7 ABC methods)
- [x] `AgentStatus` — enum constants in all `execute()` returns
- [x] `TrustLevel` — `required_trust_level` on every node
- [x] `emit_trace_event` — audit logging (positional call per node)
- [x] Output gate: module-level `_security_gate_output()` helper in `sar_draft_generate_node.py`
- [x] Input size gate: module-level `_extra_security_gate_input()` helper in `trading_data_input_node.py`

## Import Isolation Confirmation

- [x] Template does not import the platform-internal SDK
- [x] Import targets: `framework.*` and `shared.*` only (no `agents/base/` required)
- [x] No `.run()` calls — only `.invoke()` used

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| Base type | `AgentBaseGraph` | `AutonomousBaseGraph` | `AgentBaseGraph` | Fixed pipeline; domain steps are deterministic, not LLM-driven loops |
| Composition pattern | Cat 1 flat | Cat 2 nested GraphNode | Cat 2 nested | 5 sequential domain steps; FIN industry → Cat 2 required |
| Inner graph parent | `BaseGraph` | `AgentBaseGraph` | `BaseGraph` | Custom linear topology; no backbone pre_process/main/post_process needed inside |
| SAR filing | Auto-file | DRAFT only | DRAFT only | Regulatory requirement (FIEA / FSA); auto-filing = violation |
| Statistical analysis | ML model | Rule-based z-score | Rule-based z-score | Deterministic, auditable, no external model dependency |
| Pattern taxonomy | Custom | FIEA Article 159 | FIEA Article 159 | Compliance-aligned; wash trading / spoofing / layering |
