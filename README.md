# GovAI Hub - Financial & Political AI Agent

> **Financial & Political AI Agent for Celo Governance, DeFi, and Treasury Coordination.**  
> *Network-agnostic AI for the Celo ecosystem. 🟡 Celo Mainnet/🍪 Alfajores/🧪 Sepolia*

---

### 🤖 [**Access GovAI Hub on Telegram**](https://t.me/GovAIHub_bot)

---

### 🎥 [**Watch the Video Demo on Google Drive**](https://drive.google.com/file/d/1TxhvU1I-BiozT2FPUFvzdHbeVR3dwll9/view?usp=sharing)

---


## ⛓️ LockedGold Governance Architecture
GovAI Hub's on-chain governance voting is built on Celo's **LockedGold** contract (upgradeable/proxy-style architecture).

At a high level:
- The user **locks CELO** and **delegates voting power** to GovAI Hub's governance delegate address via `LockedGold (delegate(address))`.
- The bot **verifies delegation on-chain** with `/govstatus` by reading the user's current delegate from `LockedGold`.
- Users submit their vote intent with `/vote <proposal_id> YES|NO|ABSTAIN`.
- A scheduled executor aggregates intents and then **submits the final vote transaction** on the Celo Governance contract (`vote(proposalId, value)`).

This is self-custodial: the bot never requests private keys or signs user-controlled delegations.


## 💡 The Solution: Bridging the Coordination Gap in Celo

Celo's on-chain governance and DeFi ecosystem offer incredible opportunities, but participating often requires navigating complex interfaces and technical documentation (fricction 70%). **GovAI Hub** bridges this coordination gap by bringing the entire Celo experience directly into Telegram.

By combining the speed of **Groq-powered LLMs** with the accessibility of a Telegram bot, GovAI Hub empowers users to:
- Understand and vote on complex governance proposals with one click.
- Receive AI-guided DeFi trade suggestions based on natural language intent.
- Coordinate DAO treasury payouts with transparent, N-of-admin approval workflows.
- Stay informed with automated alerts that link governance outcomes to actionable DeFi opportunities.

**GovAI Hub ensures safety first:** All DeFi executions are signed locally in the user's preferred wallet (Valora, MetaMask, etc.) via secure deep links.

---

## 🚀 Key Features

*   **🏛️ Governance ELI5 (`/proposal <id>`)**: Get instant AI summaries of complex proposals. No more reading 50-page forum posts—understand the impact and vote YES/NO/ABSTAIN directly by LockedGold Architecture.
*   **💹 AI Trade Suggestions (`/aitrade`)**: Turn news into action: GovAI analyzes the context and suggests personalized routes across CELO, stCELO, and stablecoins (USDm, cEUR, USDC), with one-tap deep links to supported Celo venues.
*   **🔔 Auto-Trade Alerts**: Link a trade intent to a governance proposal; when it is executed on-chain, GovAI sends personalized Celo venue shortcuts so you can complete the trade in your wallet.
*   **🏦 DAO Treasury Payouts (`/payout`)**: Streamline working group operations with approval-based treasury requests and clean HTML receipts.
*   **💰 Share & Earn (`/earnings`)**: A built-in referral system that rewards users for growing the Celo governance community.
*   **🌐 Multi-Network Support**: Seamlessly toggle between **Celo Mainnet**, **Alfajores**, and **Sepolia** for testing and production use.

---

## 🛠 Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.12 (100% Async/Await) |
| **Bot Framework** | `python-telegram-bot` (v21+) |
| **AI Engine** | Groq API (`llama-3.3-70b-versatile`) |
| **Blockchain** | `web3.py` (Celo/EVM integration) |
| **Database** | PostgreSQL + SQLAlchemy (Asyncpg) |
| **Data Sources** | CoinGecko API & Blockscout V2 |
| **Automation** | APScheduler (Background pollers & notifications) |

---

## ⚙️ How to Run

### Prerequisites
- Python 3.12+
- PostgreSQL database
- Telegram Bot Token (via @BotFather)
- Groq API Key

### Local Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/govai-hub.git
    cd govai-hub
    ```

2.  **Set up environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Configure variables:**
    ```bash
    cp .env.example .env
    # Edit .env with your tokens and DB credentials
    ```

4.  **Run the bot:**
    ```bash
    python -m src.bot.app
    ```

---

*Built with ❤️ for the Celo V2 Hackathon: Build Agents for the Real World.*
