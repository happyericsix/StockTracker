# StockTracker

[中文](README.md) | **English**

> **Quant platforms perfect "how to find a statistical edge"; StockTracker perfects "how to make every decision explainable, verifiable, and disciplined."**

> **A multi-agent paper-trading system for China A-shares** — an 8-role AI committee tends your paper account: event-driven meetings, intraday monitoring, realistic fills, full audit trail.

[![Java](https://img.shields.io/badge/Java-21-orange)]()
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-4-green)]()
[![Vue](https://img.shields.io/badge/Vue-3-brightgreen)]()
[![Python](https://img.shields.io/badge/Python-3.11-blue)]()
[![License](https://img.shields.io/badge/License-MIT-purple)]()

> ⚠️ **Disclaimer**: This project is for learning and technical research only. It is a **paper-trading system and never connects to any real brokerage channel**. Nothing it outputs constitutes investment advice. Markets carry risk; decide carefully.

---

## What it is — and what it is not

Existing LLM stock agents ([TradingAgents](https://github.com/TauricResearch/TradingAgents), ai-hedge-fund, etc.) answer the question "*you ask, it produces a research report*" — user-triggered, one session, then gone.

**StockTracker is the opposite: a resident trading system.** Strategies sit in the system 24/7; deterministic code decides "*when it is worth waking the AI*"; the AI only judges when woken; execution and bookkeeping go back to deterministic code.

| | Research-tool agents | **StockTracker** |
|---|---|---|
| Trigger | Manual clicks | **Event-driven gating** (rule signals / volatility anomalies / stale positions — deciding "is a meeting worth it" costs zero LLM tokens) |
| Lifecycle | One session | Resident: daily settlement + intraday watch + weekly reflection loop |
| Execution | Stops at conclusions | **A-share-realistic fills**: board lots / commission floor / stamp tax / T+1 / limit-up-down blocking |
| Audit trail | One report | Every decision carries an evidence snapshot, per-role speech hashes, token usage, and a skip reason (closed enum) |
| Learning | None | Weekly reflection distills lessons that flow back → **revised/retired** when falsified by track record or new evidence (version chains keep all history) |

It also ships a complete personal stock assistant: real-time quotes (SSE fast lane), price alerts, a news-event radar with topic clustering (multi-source wire incl. Cailian Press telegrams and per-stock popularity rankings, **proactive push of high-impact events for watchlist stocks**), an AI assistant (tool calls + memory), and a PWA.

## Core features

### 🏛 Multi-role decision committee (agent decision mode)

```
Evidence pack (as-of cutoff) → Bull ⚔ Bear → Research director → Trader
                           → Risk trio (aggressive / conservative / neutral) → Risk verdict (the only decision exit)
```

- **The model decides whether to act; code decides how**: committee output is deterministically parsed into a closed enum — anything unparsable becomes SKIP, never a guess
- **Deterministic bear brief** (`agent/dissent.py`): bearish facts — bearish MA alignment, drawdowns, overheated RSI, rising price on shrinking volume, valuations at historical highs — are computed by **pure code** into the evidence pack; the floor of dissent never depends on the model's goodwill
- **Meeting context**: attendees know who called the meeting (including the rule signal) and which strategy it concerns (strategy intent)
- **Hard budgets**: dual caps on call count and tokens, plus a daily cost stop-loss; exceeding them logs `agent_budget_exceeded` (counted separately from HOLD)
- **Intraday authorization**: the committee draws `allow_open/allow_close/max_position_pct` boundaries; the watch agent reacts within them at second-level latency; the hard stop-loss channel never waits for the model

### ⏰ Event-driven gating (the key to the cost structure)

On most days the answer is HOLD — convening daily means paying for repeated work. A meeting happens only on four deterministic triggers (a closed set): rule signal / volatility anomaly (2× relative or 3% absolute) / stale position (3 days holding, 10 days flat) / first meeting. Otherwise `agent_no_new_information` is logged — **"didn't look" and "looked and chose not to act" are two different things in the trail**.

### 🧾 Realistic execution and full audit trail

- **Backtest and paper trading share one execution contract** (`agent/execution_contract.py`): backtests fill at *next open + slippage*; daily paper settlement fills at *the signal bar's close + slippage* (a 15:30 settlement structurally cannot know tomorrow's open). Every trail carries an **execution fingerprint** (fill basis / adjust mode / fee-table version / engine version / decision source), and **results with different fingerprints are not comparable** — the convention is an attribute of the data, not a second code path. Limit-up/down blocking, T+1, board lots, stamp tax, transfer fee, ¥5 commission floor
- Every decision is persisted: evidence snapshot, 8-role speech digests + SHA-256, prompt version hash, gate verdict details
- **Decision-quality reconciliation**: every fill is re-checked 5 trading days later for direction (a buy was right if price rose, a sell if it fell); fills whose window has not elapsed stay pending and are never judged early — the model's quality is answered by records

### 🔄 Experience loop and self-evolution

- Weekly reflection (1 LLM call) distills ≤3 actionable lessons → flows into the next meeting's experience pack
- **Lesson retirement**: once ≥4 evaluated agent fills exist after a lesson's birth and the win rate is ≤30%, the lesson auto-retires — a bad lesson never pollutes prompts forever
- **Challenge loop**: weekly reflection sits with the strategy's surviving old lessons (`prior_lessons`); when this week's material clearly contradicts one, it may emit a **revise** or **retire** instruction; instructions pass a double id-whitelist gate (the parser drops unknown ids + Java only accepts the fed set); revisions land as pending human confirmation, retirement takes effect immediately — lessons can be challenged by the system itself, but an agent's rewrites always pass through a human
- **Lesson revision via succession chains** (isomorphic with the fact layer): a revision appends a new row pointing at the old one; content is never UPDATEd and history always stays reviewable; recurrence counting only counts independently-born rows — revisions cannot inflate a lesson into promotion
- **Self-evolution sandbox** (`finetune/evolve_replay.py`): replays a year as-of, day by day; the committee trades and proposes weekly strategy edits behind backtest gates with frozen-segment validation; valuation/fundamental evidence blocks are as-of sliced to match production (the sandbox sees the same evidence production sees)
- **Strategy revision loop** (`agent/revision.py`, production version): health signals trigger → 1 LLM parameter-level proposal → two-stage gating (recency-weighted main verdict + holdout counter-evidence + weak-baseline bonus + **complexity penalty**: BIC-style, 5pt weighted-annualized penalty per net-added condition, capped reward for removing conditions) → **handed to a human for approval with evidence — passing the gate never auto-applies**
- **Committee ablation harness** (`finetune/committee_ablation.py`): judge/duo/core4/full8 variants compared on identical market data — answering "were the first seven roles worth the tokens"

### 🧠 AI assistant and memory

A tool-calling agent (quotes / indicators / backtest / news / memory tools) with a mandatory default-disclosure rule and a critic role that checks where every number came from; objective facts are written by code — the model cannot rewrite its own history; hybrid keyword + vector recall (RRF).

Four memory layers (event ledger / session summaries / semantic facts / lessons) are append-only: retractions and edits go through succession chains and logical withdrawal, so history is always recoverable. The memory page is fully white-box: every memory can be traced back to **its source conversation**, manually **edited** (a new version lands; the old one is kept), or disabled with one click — and you can **cross-examine the agent** on any lesson: it must present that lesson's origin and subsequent track record; if it doesn't hold up, it concedes and recommends retirement or revision (recommendations never auto-apply; the decision stays with the human). Experience is partitioned per strategy (`strategy:N`) — strategy A's lessons never leak into strategy B's committee prompts.

## Paper-trading ledger (live instance, data as of 2026-10-16)

All numbers below come from a genuinely deployed production instance (a 2-core 4GB cloud server), with nothing cherry-picked. **The sample is tiny — 1 strategy, 9 trading days, 6 fills — and implies nothing about performance**; this section exists to show what the trail and the reconciliation look like, not to show returns.

The live strategy: Bank of China (601988) · price-crosses-60-day-MA trend following · **committee (agent) mode**, initial capital ¥100,000.

**Equity snapshots** (all fees included; the equity identity `equity == cash + shares × price` holds with zero tolerance):

| Date | Equity (¥) | Cumulative |
|---|---|---|
| 10-08 | 100,000.00 | — |
| 10-09 | 100,000.00 | — |
| 10-12 | 102,429.49 | +2.43% |
| 10-13 | 102,989.06 | +2.99% |
| 10-14 | 101,945.11 | +1.95% |
| 10-15 | 100,853.41 | +0.85% |
| 10-16 | 100,853.41 | +0.85% (flat) |

**Three closed round trips** (gross return = sell price / FIFO entry cost − 1, same convention as realizedPnl, fees excluded):

| # | Buy | Sell | Gross |
|---|---|---|---|
| 1 | 10-12 @6.7968 | 10-13 @7.0230 | **+3.33%** |
| 2 | 10-13 @7.0671 | 10-14 @7.1129 | **+0.65%** |
| 3 | 10-14 @7.1271 | 10-15 @6.9600 | **−2.34%** |

**29 trails = 6 fills + 23 non-actions, and every non-action has a name** (closed-enum skip reasons):

| Trail | Count | Meaning |
|---|---|---|
| Filled (no skip) | 6 | — |
| `watch_hold` | 5 | Watch agent was asked and chose not to act (incl. vetoing a rule signal) |
| `already_traded_today` | 4 | Already acted intraday; close only re-values, no new order |
| `agent_intraday_disabled` | 3 | Committee met and deliberately disabled intraday execution |
| `awaiting_authorization` | 3 | Committee has not yet granted intraday authorization |
| `t1_blocked` | 3 | T+1: shares bought today cannot be sold today |
| `no_bar` / `rule_not_met` / `agent_no_new_information` | 2 / 2 / 1 | No candle / rule not met / no new information, no meeting |

Trigger mix: event 15 / cron 9 / watch 5 — **event-driven is the norm; cron is only the fallback**. Settlement ran unattended: `paper-daily` finished ok 5 trading days in a row, and `paper-weekly` reflection plus `paper-periodic` review fired on schedule.

As for the 5-day direction reconciliation: all 6 fills **have not yet elapsed their window — `evaluated=0`**. No window, no verdict — that discipline is exactly what this section most wants to show.

## System architecture

```
Vue 3 frontend (PWA)
      │ REST / SSE
Java 21 · Spring Boot ── MySQL (single database, JPA)
      │ WebClient (X-Internal-Token, fail-closed)
Python 3.11 · FastAPI
      ├── agent/: committee · intraday watch · chat ReAct · weekly reflection · memory · dissent
      ├── strategy_engine: rule DSL + realistic backtest (same engine as settlement)
      └── data sources: akshare / Eastmoney / Tencent quotes / Cailian Press wire (optional watchlist sync)
```

## Quick start

Dependencies: Docker (with the compose plugin). No overseas API keys are needed for market data; the LLM is [DeepSeek](https://platform.deepseek.com) (OpenAI-compatible; swap in any compatible endpoint, including local Ollama).

```bash
git clone https://github.com/happyericsix/StockTracker.git
cd StockTracker

cp .env.example .env
# Edit .env — three required values:
#   MYSQL_ROOT_PASSWORD / INTERNAL_API_TOKEN (strong random) / DEEPSEEK_API_KEY

docker compose build          # first build takes roughly 10-20 minutes
docker compose up -d
docker compose ps             # all five containers Up (mysql/redis healthy)

# Acceptance checks
curl -I http://localhost/                  # 200: the frontend page
curl http://localhost/api/v1/market/status # 401: API reachable (not logged in)
```

Open `http://localhost/` in a browser, register, add stocks to the watchlist, let the AI assistant create a strategy, then start paper trading. For server deployment and an HTTPS go-live path, see [DEPLOY.md](DEPLOY.md) (Chinese).

On machines with less than 4GB of memory, use the override file: `docker compose -f docker-compose.yml -f docker-compose.small.yml up -d`.

### Decision modes

Each strategy picks one of two modes: **rule** (DSL: MA crosses / RSI / MACD / stop-loss & take-profit / valuation percentile, executed automatically by the engine) or **committee** (discretionary decisions by the 8-role committee, event-driven meetings). Both modes run **the same execution path**, and `fingerprint.decision_mode` is written into every trail — the two equity curves are never silently mixed.

## Optional: THS watchlist sync

For compliance reasons this repository **does not include** the third-party implementation of the THS (Tonghuashun) protocol (`vendor/thspypc_src`). To enable QR-login / watchlist sync, fetch it yourself:

```bash
python python-data-service/_fetch_ths.py   # pulls the upstream source into vendor/
```

Everything else works without it (`ths_client` prints a clear notice).

## Tests

```bash
# Python (~1130 tests: committee / watch / decision parsing / ablation / memory / reflection challenge loop)
cd python-data-service && python -m pytest tests

# Java (~490 tests: settlement / authorization / portfolio / lesson retirement & revision)
./mvnw -s maven-settings.xml test
```

## Directory tour

| Path | Contents |
|---|---|
| `python-data-service/agent/trading_committee.py` | 8-role committee + event-driven gating |
| `python-data-service/agent/dissent.py` | Deterministic bear brief |
| `python-data-service/agent/intraday_watch.py` | Intraday watch trader |
| `python-data-service/agent/strategy_engine.py` | Rule DSL + realistic backtest engine |
| `python-data-service/finetune/` | Self-evolution sandbox + committee ablation harness |
| `src/main/java/.../PaperTradingService.java` | Settlement / execution / trails / lesson retirement (the Java-side heart) |
| `docs/ROADMAP.md` | Engineering decisions and incident retrospectives (Chinese) |

## Credits

- [TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache-2.0): the committee shape of bull/bear debate + risk approval, and the structured-line output contract
- [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (MIT): the practice of writing stylized discipline into role cards

This project borrows their **shape**; content and implementation were rewritten to its own conventions. Execution, audit trail, gating, and the experience loop are original designs of this project.

## License

[MIT](LICENSE) © 2026 happyericsix
