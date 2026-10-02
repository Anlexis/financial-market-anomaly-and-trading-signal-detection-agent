# FIN-C2-097 — FinancialMarketAnomalyTradingSignalDetectionAgent

> **Category**: Cat 2 (domain-specific analysis pipeline)
> **Industry**: FIN (financial services)

## Overview

Detects market-manipulation patterns in structured trading data and drafts the
paperwork a compliance team needs next. The agent validates and screens a JSON
payload of orders, accounts and daily volumes (schema, bounds, injection and
PII checks), computes deterministic anomaly statistics (volume z-score,
cancellation rate, daily volume spikes), classifies the result against the
market-manipulation taxonomy of Japan's Financial Instruments and Exchange Act
(Article 159: wash trading, spoofing, layering), and renders a severity-graded
anomaly alert together with a Suspicious Activity Report (SAR) draft for the
Financial Services Agency.

Every output is explicitly labelled DRAFT and carries a filing-prohibition
reminder: the agent never files anything — filing decisions rest with a human
compliance officer, and the output gate enforces exactly that. The alert
renders aggregates and classifications only; caller-supplied identifiers and
entity names are never reproduced verbatim.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails
during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

