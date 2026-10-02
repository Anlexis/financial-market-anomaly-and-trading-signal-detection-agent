"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from typing import Any, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for FIN-C2-097 Financial Market Anomaly & Trading Signal Detection.

    Fields align with the 5-node inner domain workflow pipeline:
    TradingDataInputNode → StatisticalAnalyzeNode → PatternClassifyNode
    → SARDraftGenerateNode → OutputFormatNode

    All fields are Optional — nodes return only the keys they modify
    and the framework merges updates into the shared state checkpoint.
    """

    # Stage 1: TradingDataInputNode — ingest & validate structured trading data
    trading_data_validated: Optional[bool]  # True when JSON schema validation passes
    raw_trading_data: Optional[dict[str, Any]]  # Parsed trading data: {orders, accounts, volumes}

    # Stage 2: StatisticalAnalyzeNode — z-score analysis, volume spike, cancellation rate
    statistical_flags: Optional[dict[str, Any]]  # {z_score_max, volume_spike_detected,
    #                                           cancellation_rate, anomaly_indicators}

    # Stage 3: PatternClassifyNode — FIEA Article 159 taxonomy classification
    anomaly_type: Optional[str]  # wash_trading / spoofing / layering / none
    severity_score: Optional[float]  # Alert severity 0.0–1.0
    fiea_reportable: Optional[bool]  # FIEA Article 159 reportability flag

    # Stage 4: SARDraftGenerateNode — FSA SAR draft (DRAFT only, never auto-filed)
    sar_draft_content: Optional[str]  # FSA 疑わしい取引報告書 DRAFT text
    #                                          Always labeled DRAFT; human review required
    #                                          Auto-filing is PROHIBITED (output gate enforced)

    # Accumulated across all inner stages
    audit_trail: Optional[list[str]]  # Ordered list of immutable audit entries
    #                                          Each entry: "[NODE] description timestamp"
    #                                          Never modified in place — always replaced
    #                                          with an extended copy (msgpack-safe)
