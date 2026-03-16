# Up-to-Celo 🌿
**Be up-to-date on the Celo Blockchain.**

> AI agent · Telegram · Daily digests · Built on Celo

---

## What is it?

Up-to-Celo is an autonomous AI agent deployed on Telegram that monitors 
the entire Celo ecosystem and delivers personalized daily digests to 
subscribers. It observes on-chain and off-chain data, reasons with a 
70B LLM, and acts — broadcasting digests, answering questions, and 
processing CELO payments on Celo Mainnet.

**Try it:** [@UpToCeloBot](https://t.me/UpToCeloBot)

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
- 🤖 **Conversational agent** — `/ask` anything about the Celo ecosystem
- ⚙️ **Personalization** — choose which apps and categories to follow
- ⭐ **Premium plan** — paid in CELO on Celo Mainnet
- 🔗 **ERC-8004** — registered as on-chain agent
- 🩺 **Self-monitoring** — health checker with admin Telegram alerts

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Bot | python-telegram-bot v21+ |
| AI | Groq `llama-3.3-70b-versatile` |
| Blockchain | web3.py · Celo Mainnet RPC |
| Database | PostgreSQL (Neon) · SQLAlchemy async |
| Scheduler | APScheduler |
| Deploy | Render.com |

## Monitored Ecosystem

| Category | Apps |
|---|---|
| Payments | MiniPay, Valora, HaloFi, Hurupay |
| DeFi | Ubeswap, Mento, Moola, Symmetric, Uniswap (Celo) |
| ReFi | Toucan, ImpactMarket |
| NFTs | OctoPlace, Hypermove, TrueFeedBack |
| Network | Celo Network, Celo Reserve |

## Roadmap

- ✅ Daily AI digest engine
- ✅ Conversational AI agent (/ask)
- ✅ Personalized settings per user
- ✅ CELO payments on Mainnet
- ✅ Health monitoring + UptimeRobot
- ✅ ERC-8004 on-chain agent registration
- 🔜 Buy CELO directly from the bot
- 🔜 Up-to-Celo for Discord
- 🔜 AI-generated price charts in /ask
- 🔜 Celo wallet portfolio tracker
- 🔜 Governance proposal push alerts
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
License
MIT — open source, free to fork and build upon.

Built for the Celo Build Agents for the Real World Hackathon V2 · March 2026
