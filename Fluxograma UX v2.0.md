


````
# Fluxograma UX v2.0 — Elementos Ausentes no Roadmap v3.0 Final
**Projeto:** GovAI Hub | Para anexar ao Gemini como contexto da Fase 7

---

## Elemento 1 — Intelligence & Signals

* **Arquivos:** `src/bot/keyboards.py`, `src/bot/callbacks.py`, `src/fetchers/rss_fetcher.py`, `src/fetchers/twitter_fetcher.py`
* **Dependência:** Independente. Pode ser executado após Fase 1 (rebranding).
* **Nota:** O label do botão muda de "Latest Digest" para "Intelligence & Signals" mas o callback existente é mantido para não quebrar o fluxo atual.

```mermaid
graph TD
    A[📰 Intelligence & Signals] -->|callback: menu:digest| B(Gera resumo diário)
    
    B --> C[📰 Details]
    B --> D[🤖 Ask AI]
    B --> E[📈 View Signals ⭐ NOVO]
    
    E -->|callback: digest:signals| F(Groq analisa rss_fetcher + twitter_fetcher)
    F --> G(Gera análise de sentimento curta)
    G --> H[Mensagem: Bullish sentiment on UBE]
    H --> I[⚡ Buy UBE]
    I -->|callback: trade:buy:UBE| J([Feature coming soon - placeholder hackathon])
    
    style E stroke:#ff9900,stroke-width:2px
````

---

## Elemento 2 — Wallet & Premium Unificado

- **Arquivos:** `src/bot/keyboards.py` (`get_wallet_keyboard()`), `src/bot/callbacks.py`
    
- **Dependência:** Requer P4 da Fase 2 (regex de carteira já ativa em `app.py`).
    

**Becos sem saída eliminados:**

- **ANTES:** texto "Usage: /setwallet 0x..." → utilizador precisava copiar e digitar comando
    
- **ANTES:** `[✅ I sent]` → texto "Run /confirmpayment 0xTxHash" → comando manual
    
- **DEPOIS:** tudo resolvido por regex automática e PaymentPoller
    

Snippet de código

```
graph TD
    A[👛 Wallet & Premium] -->|callback: menu:wallet| B(Painel unificado)
    B --> C[🔗 Connect Wallet]
    B --> D[💎 Buy Premium]
    
    C -->|callback: wallet:connect| E(Responde: Send your wallet address now)
    E --> F{Regex ^0x... captura}
    F --> G[(grava User.user_wallet)]
    G --> H(Responde: Wallet registered)
    
    D -->|callback: premium:plans| I(Mostra planos: ⭐ 7 days / ⭐ 30 days)
    I --> J[✅ I sent]
    J -->|callback: premium:sent| K(Checking your transaction... You will be notified automatically)
```

---

## Elemento 3 — PaymentPoller (Auto-detecção de Premium)

- **Arquivos:** `src/fetchers/payment_fetcher.py`, `src/scheduler/` (APScheduler job), `src/database/models.py` (campo `tier`)
    
- **Dependência:** Requer Elemento 2 (carteira registada em `User.user_wallet`).
    

**Campos do modelo `users` usados:**

- `wallet_address` — carteira registada pelo utilizador
    
- `tier` — "free" | "pending" | "premium"
    
- `premium_expires_at` — data de expiração
    
- `premium_tx_hash` — hash da tx confirmada (previne replay attack)
    

Snippet de código

```
graph TD
    A(((TRIGGER: APScheduler<br>a cada 60s))) --> B[check_payment_tx<br>em payment_fetcher.py]
    B --> C(web3 consulta onchain)
    C --> D{TX encontrada?}
    
    D -- Sim --> E[(UPDATE users:<br>tier = premium)]
    E --> F(Push Telegram: ✅ Premium activated!)
    
    D -- Não --> G(Aguarda próximo ciclo)
    
    H[FALLBACK MANUAL: /confirmpayment 0xTxHash] -.-> E
    style H stroke:#ff0000,stroke-dasharray: 5 5
```

---

## Elemento 4 — AI Summary ELI5 por Proposta

- **Arquivos:** `src/bot/callbacks.py`, `src/ai/groq_client.py`
    
- **Dependência:** Requer P2/P3 da Fase 2 (proposal view) e P8 da Fase 4 (groq_client atualizado).
    

Snippet de código

```
graph TD
    A[🏛️ Governance & Yield] -->|callback: menu:governance| B(Painel governança)
    B --> C[📋 Active Proposals]
    C --> D(Utilizador clica em Proposal #47)
    
    D --> E[Proposal #47 Details]
    E --> F[📖 AI Summary ⭐ NOVO]
    E --> Y[👍 YES / 👎 NO / 🤷 ABSTAIN]
    E --> Z[🤖 Create Auto-Trade]
    
    F -->|callback: gov:summary:47| G(groq_client chamado<br>+ instrução ELI5)
    G --> H{Groq Client}
    
    H -- Sucesso --> I(edit_message_text<br>com resumo ELI5)
    H -- Timeout --> J(Summary unavailable.<br>Try again later.)
    
    style F stroke:#ff9900,stroke-width:2px
```

---

## Mapa de Dependências Entre Elementos

Snippet de código

```
graph LR
    F1[Fase 1: Rebranding] --> E1(Elemento 1:<br>Intelligence & Signals)
    F1 --> F2P4[Fase 2 - P4: Regex wallet]
    
    F2P4 --> E2(Elemento 2:<br>Wallet & Premium)
    E2 --> E3(Elemento 3:<br>PaymentPoller)
    
    F2P2[Fase 2 - P2/P3:<br>Proposal view] --> E4(Elemento 4:<br>AI Summary ELI5)
    F4P8[Fase 4 - P8:<br>groq_client atualizado] --> E4
```

---

## Prioridade para o Hackathon

- 🔴 **Alta** — Elemento 4 (AI Summary ELI5): maior impacto de demo, baixa complexidade, `groq_client` já existe.
    
- 🟠 **Média** — Elemento 2 (Wallet unificado): UX polish visível, apenas edição de keyboard.
    
- 🟠 **Média** — Elemento 3 (PaymentPoller): impressiona juízes, complexidade média com APScheduler.
    
- 🟡 **Baixa** — Elemento 1 (Intelligence & Signals): label change + placeholder, pode ficar pós-hackathon.