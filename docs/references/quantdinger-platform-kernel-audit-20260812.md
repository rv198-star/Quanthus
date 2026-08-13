# QuantDinger Platform-Kernel Source Audit

| Field | Value |
| --- | --- |
| Authority | Non-governing external-source audit |
| Audit date | 2026-08-12 |
| Upstream | `https://github.com/OpenByteInc/QuantDinger.git` |
| Version | `v5.0.15` |
| Commit | `e64e1c227bf3174e441a42143620179b286387e1` |
| Backend license | Apache License 2.0 |
| Governing Quanthus decision | `docs/architecture/adr-0010-platform-kernel-integration.md` |

This record freezes the source facts used for the Platform Kernel decision. It does not grant
implementation authority and does not claim that every upstream behavior is correct or suitable
for Quanthus. Counts describe the audited commit only.

## 1. Repository and delivery shape

The audited repository contains the Flask backend, worker processes, deployment definitions,
operations configuration, tests, and a standalone MCP server. Desktop Web and Mobile source are
not present. The Compose files consume published frontend and mobile images; the README points to
separate `QuantDinger-Vue` and `QuantDinger-Mobile` repositories under their own source terms.

The backend is Apache-2.0 licensed. Extraction must preserve applicable copyright, license, and
attribution notices and mark modified files. QuantDinger names, logos, and product identity are
covered separately by `TRADEMARKS.md`; Quanthus must not imply upstream endorsement. Repository
visibility is not an architectural criterion. Actual source access, license compatibility, and
provenance are extraction prerequisites.

## 2. Modularity findings

QuantDinger is a layered monolith with separate process roles, not a feature-plugin architecture.
Useful ownership directories exist, but four composition points make deletion cross-cutting:

| Composition point | Audited fact | Consequence |
| --- | --- | --- |
| Human API | `app/openapi/register.py` imports and registers all human blueprints | Route deletion needs an explicit feature manifest, not only file removal |
| Agent API | `app/routes/agent_v1/__init__.py` imports 15 route modules unconditionally | MCP can be removed cheaply, but the old Agent surface must also be trimmed or deferred |
| Startup | `app/startup.py` can start trading, portfolio, payment, market, indicator, calibration, and reflection services | Disabled product domains remain reachable unless startup composition is replaced |
| Application factory | `app/__init__.py` always patches IBKR asyncio, bootstraps the full database, and runs startup hooks | A minimal API cannot be demonstrated from environment flags alone |
| Database | `migrations/init.sql` contains 78 `CREATE TABLE` declarations and startup replays it with market and strategy seeds | Schema ownership is not modular; per-feature migrations are required before deletion |
| Dependencies | One `requirements.txt` includes market, broker, AI, PDF, Redis/Celery, and platform libraries | Minimal installation and security scanning retain deleted capability cost |
| Test suite | 191 backend `test*.py` files; CI shards by file | There is useful coverage, but no package gate maps tests to removable features |

The upstream process-role split is still valuable. API, Celery, scheduler, and trading commands are
separate, and finite jobs are distinguished from long-lived workers. Quanthus can retain that
principle while admitting only API and consumer-backed job roles in the first kernel.

## 3. Domain findings

QuantDinger's data sources include `yfinance`, `akshare`, `finnhub`, `ccxt`, broker SDKs, market
catalog synchronization, and other provider adapters. The system is operationally broad, but it
does not supply Quanthus's immutable DWD snapshots, PIT visibility contracts, or accepted replay
authority. These providers are therefore replacement inputs, not migration targets.

The upstream strategy surface includes Strategy V2 runtime, backtest tables and services, factor
research, broker credentials, live execution, pending orders, grid orders, portfolio monitoring,
and reconciliation projections. These overlap Quanthus-owned semantics and cannot be admitted into
the Platform Kernel.

## 4. AI findings

The AI surface contains several distinct products rather than one shared AI kernel:

| Capability | Source examples | Quanthus disposition |
| --- | --- | --- |
| Fast Analysis | `routes/fast_analysis.py`, `services/fast_analysis*`, `tasks/fast_analysis.py` | Remove |
| Copilot/chat and tool registry | `routes/ai_chat.py`, `services/ai_copilot_store.py`, `services/ai_tool_registry.py` | Remove from first kernel |
| Strategy/indicator generation | `/strategies/generate`, `/indicator/aiGenerate`, generation contracts | Defer as isolated Authoring candidate |
| Strategy review | `qd_strategy_review_reports` and associated services | Remove |
| Reflection/calibration | `services/reflection.py`, `services/ai_calibration.py`, Celery beat entries | Remove |
| AI decision client | `services/ai_decision.py`, `qd_ai_strategy_decisions` | Remove |

The authoring candidate cannot be copied as an execution feature. Its only admissible output is a
versioned draft that passes Quanthus schema validation, static restrictions, deterministic replay,
and human governance before any separate publication decision.

## 5. Recovery implication

MCP is a standalone package and is inexpensive to reconstruct, but its current tools target the
old Agent Gateway and domain model. If a real consumer appears, rebuild it as an adapter over the
then-current Quanthus API instead of restoring old route assumptions.

Other removed modules are recoverable from the pinned tag and commit. Before deletion, the
Platform Kernel work must retain a machine-readable inventory of source paths, API operations,
tables, dependencies, process roles, tests, license notices, and any local modifications. The main
branch should not retain unreachable copies solely as a recovery mechanism.

## 6. Evidence paths

- `backend_api_python/app/__init__.py`
- `backend_api_python/app/openapi/register.py`
- `backend_api_python/app/routes/agent_v1/__init__.py`
- `backend_api_python/app/startup.py`
- `backend_api_python/app/celery_app.py`
- `backend_api_python/app/utils/db.py`
- `backend_api_python/migrations/init.sql`
- `backend_api_python/requirements.txt`
- `docker-compose.yml` and `docker-compose.ghcr.yml`
- `README.md`, `LICENSE`, and `TRADEMARKS.md`
