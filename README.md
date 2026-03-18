# Celo GovAI Hub 🌿

**Be up-to-date on the Celo Blockchain.**

> AI agent · Telegram · Daily digests · Built on Celo

---

## What is it?

Celo GovAI Hub is an autonomous AI agent deployed on Telegram that monitors 
the entire Celo ecosystem and delivers personalized daily digests to 
subscribers. It observes on-chain and off-chain data, reasons with a 
70B LLM, and acts — broadcasting digests, answering questions, and 
processing cUSD payments on Celo Mainnet for premium features.

**Try it:** [@CeloGovAI_bot](https://t.me/UpToCeloBot)

---

## Agent Loop

OBSERVE REASON ACT
────────────────── ─────────────── ──────────────────
Celo RPC (blocks) → → Daily digest (08:30 UTC)
CoinGecko (prices) → llama-3.3-70b → /ask responses
DeFi Llama (TVL) → via Groq API → CELO payment processing
RSS (15+ feeds) → → Admin health alerts
Twitter/X (10 accs) →

text

## Key Features

- 📰 **Daily AI digest** — news, DeFi, ReFi, governance & on-chain data
- 🏛️ **Governance alerts** — real-time Celo governance proposals (`/governance`)
- 🤖 **Conversational agent** — `/ask` anything about the Celo ecosystem
- ⚙️ **Personalization** — choose which apps and categories to follow
- ⭐ **Premium plan** — paid in cUSD on Celo Mainnet
- 🔗 **ERC-8004** — registered as on-chain agent
- 🩺 **Self-monitoring** — health checker with admin Telegram alerts

## Mobile-First Governance

Celo GovAI Hub is designed as a mobile-first governance layer on top of the Celo protocol.
Instead of forcing users to go through multiple wallet and dApp hops to vote on proposals,
the agent abstracts the heavy lifting while preserving self-custody.

On Celo, governance voting power is derived from CELO locked in the `LockedGold` contract and
delegated to validator groups or voting entities. Celo GovAI Hub implements a "proxy via delegation"
model: users keep their CELO in their own self-custodial wallet, sign a one-time delegation
transaction, and the Gov Hub agent uses that delegated voting power to cast votes on their behalf.

This flow removes the traditional UX funnel where more than 70% of users drop off between
discovering a proposal, opening a dApp, connecting a wallet, and finally signing an on-chain
transaction. With Celo GovAI Hub, once the delegation is in place, governance becomes a simple,
mobile-native interaction. Users can vote directly from Telegram with commands such as
`/vote 123 YES`, while the backend translates that intent into an on-chain vote using the
existing Celo governance primitives.

The result is a governance experience that feels like chatting with an assistant, while still
respecting the underlying `LockedGold` delegation model and the security assumptions of the Celo
network.

## Real World Monetization (cUSD)

Celo GovAI Hub started with premium payments priced in native CELO. As the product matured, the
monetization model was migrated to the Celo stablecoin cUSD to better align with real world
economics and user expectations.

cUSD is a stable asset designed to track the value of the US dollar on Celo. By charging for
premium features in cUSD instead of volatile CELO, the agent offers predictable pricing to users,
reduces revenue volatility for the operator, and makes it easier to reason about the cost of
subscription tiers over time.

This transition directly supports the "Real World Agents" theme of the Celo hackathon: the bot
accepts payments in a stable currency that can be used for everyday transactions, while still
settling on Celo Mainnet. Users pay for governance automation, AI digests, and advanced features
in a currency that closely matches the value they perceive off-chain.

## Security Architecture

Celo GovAI Hub is designed with a security architecture that follows industry standards for
non-custodial agents operating on public blockchains. The bot is built around three core pillars:

- **Self-Custody (Zero Private Keys)**: The backend never requests, stores, or has direct access
to users' private keys. All sensitive signing operations happen in the user's own wallet. The
agent only works with public addresses and on-chain state, plus off-chain preferences stored in
a database.
- **Transaction Simulation (Dry-Run)**: Before submitting any transaction on-chain, the bot uses
`eth_call` to simulate the execution against the Celo JSON-RPC endpoint. This dry-run flow
checks for potential reverts, missing approvals, or misconfigured parameters, reducing the risk
of failed transactions and unnecessary gas spending.
- **Gas Price Ceilings**: The agent enforces a hard cap on acceptable gas prices. If network
conditions push gas costs above this ceiling, the bot pauses execution for non-critical flows
and surfaces a warning to operators. This protects the bot's balance and ensures that automated
actions do not accidentally overspend during periods of severe congestion.

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
- ✅ Telegram payment UI fully migrated to cUSD plans (Phase 13)
- ✅ Health monitoring + UptimeRobot
- ✅ ERC-8004 on-chain agent registration
- ✅ Governance proposal push alerts (15min polling + `/governance`)
- ✅ Mobile-first governance via `LockedGold` delegation + Telegram voting (`/vote 123 YES`)
- ✅ Continuous documentation updates for governance, monetization, and security (Phase 12)
- ✅ Governance-ready database schema for delegation tracking (Phase 14 · P47)
- ✅ Telegram delegation UX for `/delegate` and `/revoke` (Phase 14 · P48)
- ✅ On-chain delegation status validator via LockedGold (`/govstatus`) (Phase 14 · P49)
- ✅ Governance vote intent queue and `/vote` command (Phase 15 · P50)
- ✅ Gas price ceiling safety module for governance transactions (Phase 15 · P51)
- ✅ Governance transaction simulation (dry-run via `eth_call`) for votes (Phase 15 · P52)
- ✅ Scheduled governance vote executor with majority aggregation and on-chain execution (Phase 15 · P53)
- ✅ Governance proposal description text extractor with safe timeouts and length limits (Phase 16 · P54)
- ✅ Governance proposal AI ELI5 summaries in `/proposal <id>` and push alerts (Phase 16 · P55 · Phase 16 complete)
- ✅ On-chain fallback for `/proposal <id>`: resolves `descriptionUrl` via `getProposal()` on the Celo Governance contract when proposal is not in local DB (Phase 17 · P56)
- ✅ Native on-chain governance proposal listing via `getQueue()` and `getDequeue()` (`/govlist`) (Phase 17 · P57)
- ✅ Native on-chain governance history listing (`/govhistory`) with stage-based filtering and safe Telegram limits (Phase 17 · P58)

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
git clone https://github.com/jonechelon/up-to-celo
cd up-to-celo
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