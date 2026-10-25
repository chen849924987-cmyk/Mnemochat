# Mnemochat · Long-Term Memory for Group-Chat Bots

[![CI](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml/badge.svg)](https://github.com/chen849924987-cmyk/Mnemochat/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**中文说明 → [README.md](README.md)**

> Give your API-driven group-chat bot a human-like memory: it forgets gradually, remembers when triggered, gets stronger every time it recalls — and **never wipes the database**.

A self-contained memory core: tiered funnel (STM → Staging → LTM) + Ebbinghaus decay + retrieval reinforcement + hybrid retrieval (BM25 / vectors / entity graph) + persona anchoring + persona drift detection. Single-file SQLite storage, no external services. Connects to **QQ groups** (OneBot v11) and **Telegram groups**, plus a console simulator for offline development.

## Why not "periodic summarization + wipe"

Treating context management as a *compression problem* forces the model to guess "what will matter later" at wipe time — a guess that is often wrong, and summaries-of-summaries drift with every round. Mnemochat reframes it as a **storage & retrieval problem**:

- **Append-only, never physically deleted**: raw messages are fully logged; "forgetting" is just a decayed retrieval priority (memories sink, but can always be fished back by keywords/entities).
- **Decay affects ranking, not existence**: retention `R = exp(-Δt/S)`; each recall strengthens `S ×1.25` (capped at a year) — "the more you recall it, the firmer it gets".
- **Changed facts ≠ destroyed history**: a contradicting new fact marks the old one `superseded` — no longer injected, but always traceable.

## Architecture

```
group messages (whether the bot replies or not)
   │
   ▼
 messages log (append-only — the bot learns by lurking)
   │ every N messages (async, never blocks replies)
   ▼
 ┌────────── ingest ──────────────┐
 │ LLM extracts candidates         │
 │ noise gate: low-info discarded  │
 │ dedup: seen before → reinforce  │
 └──────────────┬──────────────────┘
                ▼
            staging
                │ consolidation (periodic "sleep")
                ▼
 ┌────────── consolidate ─────────┐
 │ promote: old enough + important│
 │ contradiction → superseded     │
 │ sleep: episodes → semantics    │
 └──────────────┬─────────────────┘
                ▼
   LTM (SQLite + embeddings + FTS5 + entity index)
                │ before every reply (local only, no LLM calls)
                ▼
 ┌────────── recall ──────────────┐
 │ BM25 ∪ vector ∪ entity graph   │
 │ filter: superseded / persona   │
 │ salience re-rank → Top-K       │
 └──────────────┬─────────────────┘
                ▼
   retrieval = reinforcement (access+1, S×1.25, decay clock reset)
```

**Persona anchoring**: the character card (`personas/*.yaml`) is injected verbatim into every system prompt and never decays; the knowledge boundary decides which memories this persona is *allowed* to know (cross-group privacy is blocked by default).

**Persona drift detection**: after each consolidation the bot's recent replies are sampled and judged against the card; on drift (customer-service tone, "as an AI…" style) a re-anchoring hint is injected into subsequent prompts until it recovers. All reports are stored for long-term persona-health tracking.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows; use .venv/bin/pip on Linux/macOS

# fully offline demo (no API calls, rule-based mock)
python main.py --sim --mock

# real APIs
python main.py --init        # generate config.yaml & .env
# fill DEEPSEEK_API_KEY and SILICONFLOW_API_KEY (or any OpenAI-compatible endpoint)
python main.py --sim
```

Two-minute taste of how it "lives":

```
群聊> 小明: 我对花生过敏，大家点菜注意点        # "I'm allergic to peanuts"
  📖 记住了（staging）：小明对花生过敏
群聊> /warp 14d                               # fast-forward two weeks
  😴 巩固：晋升 1 条
群聊> /window reset                           # wipe the conversation window
群聊> 小明: 小满，今晚聚餐吃什么好
小满: 我记得——约两周前，小明对花生过敏          # recalled from long-term memory
```

**QQ (OneBot v11)**: run NapCat / LLOneBot / Lagrange with a forward-WebSocket server (default port 3001), set `onebot.ws_url`, then `python main.py --onebot`.

**Telegram**: create a bot via @BotFather, put the token in `.env`, disable privacy mode (`/setprivacy` → Disable), add it to a group, then `python main.py --telegram`.

**Backup / migration**: `python main.py --export` / `--import file.json` (embeddings excluded — rebuild with `--reembed`).

## Design notes

- Decay math: `R = exp(-Δdays / S)`, initial `S = 2 + importance` days; each recall `S ← min(S×1.25, 365)`.
- Salience: `0.35·sim + 0.25·BM25 + 0.20·retention + 0.10·log(access) + 0.10·(importance+emotion)/2` (all weights configurable).
- Chinese BM25: jieba segmentation on both index and query sides (SQLite FTS5's unicode61 doesn't segment CJK).
- The recall query includes the speaker's name — whoever is talking, memories *about them* surface more easily. Humans work this way too.
- Recall is pure SQL + numpy (milliseconds at thousands of memories); LLM calls only happen in async ingestion and periodic consolidation.
- Multi-group safe: per-group ingest buffers and window resets; a group-scoped memory never leaks into another group (persona boundary).

## Tests

```bash
python -m pytest tests/ -q      # 58 passed, fully offline
```

## Roadmap

- [x] QQ adapter (OneBot v11 forward WebSocket)
- [x] Telegram adapter (long polling)
- [x] Persona drift detection (sampling review + re-anchor loop)
- [x] Memory export / import
- [ ] Self-reflection memories (`visibility: self`)
- [ ] Discord adapter, multi-persona dashboard, memory visualization

> ⚠️ Note: unofficial QQ protocol implementations (NapCat etc.) may violate Tencent's ToS and risk account restrictions — use a throwaway account. This repo ships no protocol implementation; it is only a standard OneBot 11 client.

## Acknowledgements

Design informed by great work in the community: [Mem0](https://github.com/mem0ai/mem0), [Zep](https://github.com/getzep/zep) (temporal knowledge graphs, fact validity), MemoryBank (Ebbinghaus forgetting curve), A-MEM, MENTOR/BRIDGE (persona anchoring, dual-chain memory). This is an independent implementation with no affiliation to those projects.

## License

[MIT](LICENSE)
