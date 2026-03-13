# 🟡 Up-to-Celo
> **Be up-to-date on the Celo Blockchain**

A Telegram bot that delivers daily AI-powered digests about the Celo ecosystem.
Covers: MiniPay, Valora, Ubeswap, Mento, ImpactMarket, Celo Network and more.

## Stack
- Python 3.12
- python-telegram-bot v21+ (Bot API — not userbot)
- Groq API — llama-3.3-70b-versatile
- feedparser, web3.py, aiohttp
- APScheduler, SQLAlchemy + aiosqlite

## Quick Start
1. Copy `.env.example` → `.env` and fill in the required variables
2. `pip install -r requirements.txt`
3. `python scripts/test_connection.py` — verify all services
4. `python -m src.bot.app` — start the bot

## Architecture
RSS (Celo Blog / Forum / GitHub) + Nitter (Twitter/X) + CoinGecko + DeFi Llama + Celo RPC
→ DigestBuilder → Groq → Telegram (inline keyboards)

## Bot Commands
| Command        | Description                    |
|----------------|--------------------------------|
| /start         | Onboarding & welcome           |
| /subscribe     | Enable daily digest            |
| /unsubscribe   | Disable daily digest           |
| /digest        | Get today's digest on demand   |
| /settings      | Choose your apps               |
| /ask           | Ask AI about Celo              |
| /status        | System health (admin only)     |
| /help          | All commands                   |

## Deploy
Worker on Render.com. See `render.yaml` (created in P38).

## Hackathon
Built for: Build Agents for the Real World — Celo V2 (deadline 18/03/2026)
