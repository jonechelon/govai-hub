# GovAI Hub — Financial & Political AI Agent

> Network-agnostic AI agent for Celo governance, DeFi, and DAO treasury coordination.  
> Built for the **[Build Agents for the Real World — Celo V2 Hackathon](https://celo.org)**.

[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org)
[![PTB](https://img.shields.io/badge/python--telegram--bot-v21-blue)](https://python-telegram-bot.org)
[![Deploy](https://img.shields.io/badge/deploy-Render-informational)](https://render.com)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What is GovAI Hub?

GovAI Hub is a Telegram bot that connects Celo token holders to on-chain governance,
DeFi venues, and DAO treasury operations.

DeFi actions are always wallet-signed via deep links (Ubeswap, Mento, Valora).  
For governance execution, the bot uses a dedicated delegate wallet configured on the server.

**Who is it for?**

- **Celo token holders** who want to follow, vote on, and act on governance proposals
  directly from Telegram
- **DeFi users** who want AI-guided trade shortcuts to Ubeswap, Mento, and stCELO
- **DAOs and working groups** that need lightweight treasury payout coordination
  with on-chain receipts and multi-admin quorum approval

---

## Core Features

### 🗳️ Governance
Browse active Celo proposals, vote YES / NO / ABSTAIN, view vote history,
and read AI-generated summaries powered by Groq (`llama-3.3-70b-versatile`).

### 🤖 AI Trade (`/aitrade`)
Send a natural-language intent (for example, `swap 10 CELO to stCELO`) and receive
deep links to Ubeswap, Mento, and additional Celo venues. DeFi execution is always
signed in the user's wallet.

### 📋 Auto-Trade Alerts
Attach a trade intent to a governance proposal. When the proposal executes
on-chain, the bot sends venue links. For Ubeswap links, `feeTo=<TREASURY_ADDRESS>`
is appended when treasury is configured.

### 💧 Liquid Staking
Live stCELO wallet balance and stCELO→CELO rate from Celo mainnet contracts:
- stCELO token (ERC-20): `0xC668583dcbDc9ae6FA3CE46462758188adfdfC24`
- StakedCelo manager (`toCelo`): `0x0239b96D10a434a56CC9E09383077A0490cF9398`
- StakedCelo account (reference): `0x4aAD04D41FD7fd495503731C5a2579e19054C432`

### 🏛️ DAO Treasury Payouts (`/payout` — groups)
Create a payout request in groups; N-of-admin approval with an HTML receipt.
Quorum is configurable via `TREASURY_QUORUM`. The bot does not execute treasury transfers.

### 🔗 Share & Earn (`/earnings`)
Share a proposal referral link. Referral activity is tracked in DB (`swap_count`,
`earned_usdm`, `gov_points`) and shown in the earnings dashboard.

### 🌐 Multi-network
Per-user network toggle across Celo Mainnet, Alfajores, and Sepolia.
RPC resolver falls back to mainnet RPC when network-specific env vars are unset.

---

## Stack

| Layer | Technology |
|---|---|
| Bot runtime | Python 3.12 · `python-telegram-bot[webhooks]` v21 |
| AI / NLP | Groq API (`llama-3.3-70b-versatile` + fallbacks) |
| On-chain reads | `web3.py` on Celo RPC (Forno default fallback) |
| Scheduler | APScheduler 3.10 (daily digest, payment poller, governance jobs) |
| Database | SQLAlchemy 2.0 async + `asyncpg` (Neon PostgreSQL) / `aiosqlite` (local) |
| Prices | CoinGecko API (optional key) |
| Tx history | Blockscout Celo REST v2 + Etherscan V2 (`chainid=42220`) |
| Deploy | Render.com |

---

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome, user registration, optional referral binding |
| `/help` | Full command reference |
| `/aitrade <intent>` | AI-guided DeFi deep links via Groq |
| `/earnings` | Referral rewards and GovPoints dashboard |
| `/payout @user <amount> [TOKEN]` | DAO treasury payout request (groups) |
| `/governance` | Governance hub |
| `/vote <proposal_id> <yes\|no\|abstain>` | Record governance vote intent |
| `/proposal <id>` | Proposal details with AI summary |
| `/settings` | Alerts, network, wallet and app preferences |

Admin-only: `/admin_stats`, `/admin_broadcast`, `/admin_digest_now`

---

## Quick Start (local)

```bash
git clone https://github.com/<!-- TODO: preencher -->/govai-hub.git
cd govai-hub
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill required variables
python -m src.bot.app
```

---

## Project Structure

```text
src/
├── ai/            # Groq client, prompt builder, digest generator
├── bot/           # handlers, callbacks, keyboards, app entry point
├── database/      # SQLAlchemy models, async manager, startup migrations
├── fetchers/      # governance, on-chain, market, RSS, CoinGecko
├── scheduler/     # APScheduler jobs: digest, governance, payment poller, AI reminders
└── utils/         # defi_links, text_utils, blockscout, etherscan_v2, token registry
```

---

## Database Schema (key tables)

| Table | Purpose |
|---|---|
| `users` | Telegram user profile, wallet/network prefs, tier, `gov_points`, `referred_by` |
| `auto_trades` | Trade intent tied to proposal execution notifications |
| `ai_trade_sessions` | `/aitrade` suggestion sessions (`session_id`, `suggestions_json`) |
| `payout_requests` | DAO treasury requests with approvals JSON and status |
| `referrals` | Referrer→referee mapping with `swap_count` and `earned_usdm` |
| `governance_alerts` | Proposal broadcast deduplication and send status |
| `governance_votes` | User vote intents and optional executed tx hash |
| `system_state` | Persistent key-value state (stateless deploy safe) |

---

## Supported Tokens (Celo Mainnet)

| Token | Contract |
|---|---|
| CELO | `0x471EcE3750Da237f93B8E339c536989b8978a438` |
| stCELO | `0xC668583dcbDc9ae6FA3CE46462758188adfdfC24` |
| USDm | `0x765DE816845861e75a25fca122bb6898B8B1282a` |
| USDC | `0xceba9300f2b948710d2653dD7B07f33A8B32118C` |
| cEUR | `0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73` |

---

## Documentation

| Doc | Description |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System overview and component diagram |
| [`docs/governance_flow.md`](docs/governance_flow.md) | End-to-end governance and vote flow |
| [`docs/ai-trade-handoff.md`](docs/ai-trade-handoff.md) | AI Trade prompt and handoff spec |
| [`docs/database_schema.md`](docs/database_schema.md) | Full DB schema and migrations |
| [`docs/async_background_workers.md`](docs/async_background_workers.md) | Scheduler jobs and APScheduler setup |
| [`docs/data_aggregation_engine.md`](docs/data_aggregation_engine.md) | Fetcher pipeline (RSS, market, governance, on-chain) |
| [`docs/crypto_payment_gateway.md`](docs/crypto_payment_gateway.md) | cUSD payment flow and payment poller |
| [`docs/ai_prompts.md`](docs/ai_prompts.md) | Prompt library reference |

---

## Hackathon Submission

🏆 Hackathon: Build Agents for the Real World — Celo V2  
📅 Deadline: 22 March 2026

- Karma: <!-- TODO: preencher -->
- Agentscan: <!-- TODO: preencher -->
- Announcement tweet: <!-- TODO: preencher -->

---

## Security & Custody

- The bot never holds user funds for DeFi operations.
- DeFi actions are deep links only; users sign in their own wallet.
- DAO treasury payouts are approval workflows; transfer execution is manual.
- `BOT_WALLET_PRIVATE_KEY` and `GOVERNANCE_PRIVATE_KEY` are sensitive and must never be committed.