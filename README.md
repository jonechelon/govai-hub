# Celo GovAI Hub 🌿

**Be up-to-date on the Celo Blockchain.**

> AI agent · Telegram · Governance Hub · Built on Celo

---

## What is it?

**Celo GovAI Hub** is a mobile-first AI terminal designed for the Celo ecosystem. It delivers daily on-chain insights and allows you to participate in network governance with a single command (e.g., `/vote 47 YES/NO`—all while keeping your funds strictly non-custodial and secure through Celo's native `LockedGold` architecture.

> 💡 **The UX Problem We Solve:**
> The traditional Web3 governance funnel is broken. Over 70% of users drop off between discovering a proposal, opening a dApp, connecting a wallet, and finally signing an on-chain transaction. 
> 
> **With Celo GovAI Hub**, once `LockedGold` delegation is in place, governance becomes a frictionless, mobile-native interaction. You vote directly from Telegram using a single, fast command: `/vote <id> YES|NO|ABSTAIN` (e.g., `/vote 47 YES`).

**Try it:** [@CeloGovAI_bot](https://t.me/CeloGovAI_bot)

---

## Agent Loop

```text
[ Data Sources ]
├─ Celo RPC (Blocks, Governance state, TVL)
├─ CoinGecko / DeFi Llama (Market data)
├─ RSS Feeds (15+ ecosystem sources)
└─ Twitter/X (10+ ecosystem accounts)
         │
         ▼
[ AI Processing ]
└─ Groq API (llama-3.3-70b-versatile)
         │
         ▼
[ Execution & Output ]
├─ Telegram UI (Daily digests, /ask, Gov Hub)
├─ PostgreSQL (State & Delegation tracking)
└─ Celo Mainnet (On-chain voting via LockedGold)
```

## Key Features

- 🏛️ **Governance Hub** — Real-time Celo governance integration. Includes `/govlist` for active proposals, `/govhistory` for past decisions, AI-powered ELI5 summaries (`/proposal`), and 1-click on-chain voting (`/vote`).
- 🤖 **Conversational Agent** — Use `/ask` to chat with an AI that knows the Celo ecosystem inside out.
- 📰 **Daily AI Digest** — Personalized daily news, DeFi/ReFi updates, and on-chain data.
- ⭐ **Premium Plan (cUSD)** — Access advanced features with frictionless stablecoin payments on Celo Mainnet.
- ⚙️ **Personalization** — Choose exactly which apps and categories you want to follow.
- 🔗 **ERC-8004** — Registered as an on-chain agent.
- 🩺 **Self-Monitoring** — Built-in health checker with admin Telegram alerts.

## Mobile-First Governance

Celo GovAI Hub eliminates that friction with a mobile-first governance flow built on Celo's
`LockedGold` contract using the "Proxy via Delegation" model.

In this model, users keep their CELO in their own self-custodial wallet and make a single,
one-time delegation to enable voting power via `LockedGold`. Once delegation is in place, the
Gov Hub uses that delegated voting power to execute votes on-chain on the user's behalf.

After the one-time setup, participation becomes simple and Telegram-native: users vote with a
single command (e.g., `/vote 123 YES|NO|ABSTAIN`) without repeatedly signing new on-chain
transactions for every proposal.

The outcome is governance that feels like chatting with an assistant, while preserving the security
assumptions and delegation guarantees of the underlying Celo network.

To completely align with real-world economics, the Hub's premium features are monetized exclusively through **cUSD** (Celo Dollar). By charging in a stablecoin rather than a volatile asset, the agent offers predictable pricing for users and supports the "Real World Agents" narrative, proving that automated governance and AI digests can be seamlessly powered by everyday digital currency.

## Security Architecture

The Celo GovAI Hub security model follows industry standards for non-custodial agents operating on
public blockchains. The bot is built around three core pillars:

- **Self-Custody (Zero Private Keys)**: The backend never requests, stores, or has direct access
to users' private keys. All sensitive signing operations happen in the user's own wallet. The
agent only works with public addresses and on-chain state, plus off-chain preferences stored in
a database.
- **Transaction Simulation (Dry-Run)**: Before submitting any transaction on-chain, the bot uses
`eth_call` to simulate the execution against the Celo JSON-RPC endpoint. This dry-run flow
checks for potential reverts, missing approvals, or misconfigured parameters, reducing the risk
of failed transactions and unnecessary gas spending.
- **Gas Price Ceilings**: The agent enforces a hard cap on acceptable gas prices. If network
conditions push gas costs above this ceiling, execution is paused for non-critical actions and an
operator warning is surfaced. This protects the bot's balance and prevents unnecessary spending
during severe congestion.

## Tech Stack


| Layer      | Technology                           |
| ---------- | ------------------------------------ |
| Language   | Python 3.12                          |
| Bot        | python-telegram-bot v21+             |
| AI         | Groq `llama-3.3-70b-versatile`       |
| Blockchain | web3.py · Celo Mainnet RPC           |
| Database   | PostgreSQL (Neon) · SQLAlchemy async |
| Scheduler  | APScheduler                          |
| Deploy     | Render.com                           |


## Monitored Ecosystem


| Category | Apps                                             |
| -------- | ------------------------------------------------ |
| Payments | MiniPay, Valora, HaloFi, Hurupay                 |
| DeFi     | Ubeswap, Mento, Moola, Symmetric, Uniswap (Celo) |
| ReFi     | Toucan, ImpactMarket                             |
| NFTs     | OctoPlace, Hypermove, TrueFeedBack               |
| Network  | Celo Network, Celo Reserve                       |


## Completed Features

- ✅ Daily AI digest engine
- ✅ Conversational AI agent (`/ask`)
- ✅ Personalized settings per user
- ✅ Premium payments on Mainnet (fully migrated from native CELO to cUSD)
- ✅ cUSD-only payment detection and on-chain `/confirmpayment` verification via ERC-20 `Transfer` events
- ✅ Refactored `payment_fetcher.py` cUSD payment detection to rely on `Transfer` events to `BOT_WALLET_ADDRESS` only (Phase 13 · P45)
- ✅ Telegram payment UI fully migrated to cUSD plans (Phase 13)
- ✅ P46 — Updated `/premium` and `/setwallet` handlers to reflect cUSD plan pricing (Phase 13 · P46)
- ✅ Health monitoring + UptimeRobot
- ✅ ERC-8004 on-chain agent registration
- ✅ Governance proposal push alerts (15min polling + `/governance`)
- ✅ Mobile-first governance via `LockedGold` delegation + Telegram voting (`/vote 123 YES`)
- ✅ README value proposition refresh for hackathon judges (Mobile-first governance, cUSD, security) (Phase 12)
- ✅ P47 — Expansão do DB para Governança (LockedGold delegation tracking: `user_wallet`, `delegated_power`, `revoked_at`)
- ✅ P48 — Delegation Command (`/delegate` and `/revoke`) with LockedGold self-custodial instructions (Phase 14 · P48)
- ✅ P49 — On-chain delegation status validator via LockedGold (`/govstatus`) with `delegated_power` + `revoked_at` tracking (Phase 14 · P49)
- ✅ Governance vote intent queue and `/vote` command (Phase 15 · P50)
- ✅ Gas price ceiling safety module for governance transactions (Phase 15 · P51)
- ✅ Governance transaction simulation (dry-run via `eth_call`) for votes (Phase 15 · P52)
- ✅ P53 — Scheduled governance vote executor with majority aggregation and on-chain execution (Phase 15 · P53, runs every 30 minutes)
- ✅ Governance proposal description text extractor with `timeout=5` and 8000-character hard limit (Phase 16 · P54)
- ✅ Phase 16 complete: P54 (extractor) + P55 (`/proposal <id>` Groq ELI5 summaries + AI Summary in governance push alerts)
- ✅ UX fix: `/proposal <id>` now falls back on-chain (via `getProposal()`) only when the proposal is missing from local DB, with a clearer "not found" response (Phase 17 · P56)
- ✅ `/govlist` native on-chain proposal listing via `getQueue()` and `getDequeue()` with `queued` + `dequeued` buckets (Phase 17 · P57)
- ✅ Native on-chain governance history listing (`/govhistory`) with stage-based filtering and safe Telegram limits (Phase 17 · P58)
- ✅ Advanced on-chain filtering for `/govlist` (remove zeros/duplicates, resolve true stage per ID, cap inactive buckets) (Phase 18 · P59)

## Roadmap

- 🔜 Buy CELO directly from the bot
- 🔜 Celo GovAI Hub for Discord
- 🔜 AI-generated price charts in `/ask`
- 🔜 Celo wallet portfolio tracker
- 🔜 Multi-language support (PT · ES · FR · ZH)
- 🔜 Agent-to-agent (A2A) communication
- 🔜 Webhook-based real-time event triggers

## Quick Start

```bash
git clone https://github.com/jonechelon/CeloGovAIHub.git
cd CeloGovAIHub
cp .env.example .env   # fill in keys — see .env.example
pip install -r requirements.txt
python -m src.bot.app
```

Test governance alerts:

```bash
python scripts/test_governance.py
```

Use `/governance` inside Telegram to see live proposals.

## License

MIT — open source, free to fork and build upon.

Built for the Celo Build Agents for the Real World Hackathon V2 · March 2026