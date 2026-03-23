# GovAI Hub - Your Celo Telegram Copilot

> **Financial & Political AI Agent for Celo Governance, DeFi, and Treasury Coordination.**  
> *Network-agnostic AI for the Celo ecosystem.*

🎥 **[DROP VIDEO FILE HERE]**

---

## 💡 The Solution: Bridging the Coordination Gap in Celo

Celo's on-chain governance and DeFi ecosystem offer incredible opportunities, but participating often requires navigating complex interfaces and technical documentation. **GovAI Hub** bridges this coordination gap by bringing the entire Celo experience directly into Telegram.

By combining the speed of **Groq-powered LLMs** with the accessibility of a Telegram bot, GovAI Hub empowers users to:
- Understand and vote on complex governance proposals with one click.
- Receive AI-guided DeFi trade suggestions based on natural language intent.
- Coordinate DAO treasury payouts with transparent, N-of-admin approval workflows.
- Stay informed with automated alerts that link governance outcomes to actionable DeFi opportunities.

**GovAI Hub ensures safety first:** All DeFi executions are signed locally in the user's preferred wallet (Valora, MetaMask, etc.) via secure deep links.

---

## 🚀 Key Features

*   **🏛️ Governance ELI5 (`/proposal <id>`)**: Get instant AI summaries of complex proposals. No more reading 50-page forum posts—understand the impact and vote YES/NO/ABSTAIN directly.
*   **💹 AI Trade Suggestions (`/aitrade`)**: Describe your intent (e.g., "I want to swap my CELO for stCELO") and get instant deep links to Ubeswap, Mento, or Jumper.
*   **🔔 Auto-Trade Alerts**: Attach a trade intent to a proposal. When it passes on-chain, GovAI Hub alerts you with the exact venue links to execute.
*   **💧 Liquid Staking Integration**: Real-time tracking of stCELO balances and exchange rates to maximize your Celo yield.
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
