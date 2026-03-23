# AI Trade — Technical Handoff (GovAI Hub)

## Purpose

**AI Trade** is a Telegram flow where the user picks a **numbered source** from the **Daily Sources** list (digest cache). The bot **fetches the article URL**, extracts **plain text** from HTML (with optional in-memory cache), and sends **title + text** to **Groq** so the model outputs **2–5** distinct DeFi-oriented suggestions for the Celo ecosystem. The UI shows those lines as a **numbered HTML list**, optional **CoinGecko** spot context (including **multi-fiat** quotes for stablecoins when available), **on-chain Celo trading shortcuts** (`build_personalized_trading_dex_keyboard` in `src/utils/defi_links.py` — pair-aware Ubeswap / Uniswap / Jumper), and an **optional fiat on-ramp** row (`build_onramp_keyboard_rows` in `src/utils/onramp_links.py`) when stablecoins appear in suggestions or in the resolved swap pair — **not** a single `build_venue_links` intent from the first suggestion only.

**HTML / copy pass:** `ParseMode.HTML`, dynamic segments escaped with `hesc()` from `src/utils/text_utils.py`. The mandatory DeFi disclaimer matches **§8** in `.cursor/rules/ui_protection.mdc` (see §20 supplement for exact placement).

The bot **never** signs transactions server-side; venue buttons open wallet/deep links for the user to complete actions locally.

---

## User entry points

1. **Main menu** — **“💹 AI Trade”** (`callback_data=digest:latest`). Runs digest generation, then **Daily Sources** (hyperlinked titles + numbered keyboard).
2. **Numbered buttons** — `1`–`N` map to `extract_links_from_digest(digest_id)` (`callback_data=digest:link:{digest_id}:{index}`, **1-based** index).
3. **`/digest`** and scheduled digests may attach the same digest keyboard; behavior is identical when the user taps a number.
4. **`/aitrade &lt;text&gt;`** — separate **Modo IA** flow (`handlers.py`): Groq + up to **5** `ai_pick:` buttons (session table `ai_trade_sessions`).

---

## End-to-end flow (`digest:link:…` → `_handle_digest_link`)

Implementation: `src/bot/callbacks.py` (`_handle_digest_link`).

1. **Resolve the link** — Parse `digest_id` and **1-based** `index`. Load links via `extract_links_from_digest` (`src/utils/digest_links.py`). Use `url`, `title`, `display_title` (or `title`) for UI.
2. **Loading UI** — `query.edit_message_text` → **“Reading article…”** + short label (HTML).
3. **Fetch text** — `await fetch_url_text(url)` (`src/utils/url_fetch.py`). In-memory **cache** (24h TTL, non-empty only) skips HTTP on repeat for the same URL in-process.
4. **Groq** — Only if scrape returned non-empty text: `user_blob = Article: {title}\n\n{scraped}` (truncated to ~14k). `get_ai_suggestions` → `parse_ai_suggestions` (up to **5** items).
5. **Display labels** — `_build_ai_trade_display_labels` builds **2–5** lines with emoji-led copy; if Groq fails or returns empty, **fallback pool** (shuffled, count 2–5 stable per URL+digest).
5b. **Market context** — `fetch_trade_token_market_stats` (`src/fetchers/coingecko_prices.py`): **simple/price** with **`vs_currencies`** (default **usd,eur,brl,gbp,mxn** via `COINGECKO_FIAT_CURRENCIES`) and **24h %% change** (USD) for mapped tokens; in-process TTL cache. **Stablecoins** may show **multi-fiat** reference (e.g. USD + EUR/BRL) in the spot summary and enriched lines. **`COINGECKO_API_KEY`** optional (demo/pro headers). Per-line intent from label + venue: **stake** → **Celo TVL** via `fetch_celo_chain_tvl_usd` (`src/fetchers/defillama_celo_tvl.py`), no spot on that row; **buy / sell / swap** → spot + 24h (dual-asset on swaps when both priced); **edge** (incl. yield/apy hints) → 24h + TVL. Summary block: italic **Spot (CoinGecko)** for trading symbols + **Celo on-chain TVL (DeFi Llama)** when TVL was fetched.
6. **Result message** — `_edit_ai_trade_result`: header `💹 AI Trade — {title}`, optional spot line, numbered `<b>` lines, subsection **“On-chain Celo — trading shortcuts”**, **§8 disclaimer** (italic), `build_personalized_trading_dex_keyboard` (pair from `resolve_swap_pair_from_suggestions`; keyword profiles: Mento / DeFi / Opera·stCELO / yield / balanced), **2 URL buttons per row**; **optional** **`url=`** row from `build_onramp_keyboard_rows` (`src/utils/onramp_links.py`) when stablecoins appear — **Transak** / **Ramp** if env keys set; **Celo on-ramps** directory → `https://docs.celo.org/home/ramps`; optional wallet prefill via `db.get_wallet`; then **⬅️ Back** (`back:{digest_id}`) **alone** on the last row. `disable_web_page_preview=True`.

---

## UI / UX / HTML copy checklist (implemented)

| Element | Behavior |
|--------|------------|
| Loading | `…Reading article…` + short title in italics |
| Title | `💹 <b>AI Trade — {truncated display title}</b>` |
| Suggestion lines | `1.` … `N.` **2–5** lines, `<b>` per line; emoji in Groq labels or fallback / prefix rotation |
| Shortcuts header | `<b>On-chain Celo — trading shortcuts</b>` |
| Disclaimer | §8 exact line in `<i>…</i>` (see `ui_protection.mdc` §8) |
| Keyboard | Personalized DEX URLs, **2 per row**; optional **on-ramp** `url=` row (Transak / Ramp / Celo docs) when stables; **Back** alone on last row (`callback_data`, not `url`) |
| Escape | `hesc()` on titles and labels |

---

## Environment variables (digest AI Trade)

| Variable | Role |
|----------|------|
| `COINGECKO_API_KEY` | Optional; demo/pro headers for `api.coingecko.com` (see `COINGECKO_API_BASE`). |
| `COINGECKO_FIAT_CURRENCIES` | Comma-separated `vs_currencies` for `/simple/price` (default `usd,eur,brl,gbp,mxn`). |
| `COINGECKO_CACHE_TTL_SEC` | In-process cache TTL for CoinGecko bundles (default `600`). |
| `TRANSAK_API_KEY` | Required for **Transak** widget URLs; without it, the Transak button is omitted. |
| `RAMP_HOST_API_KEY` | Required for **Ramp** widget URLs; without it, the Ramp button is omitted. |
| `DEFAULT_ONRAMP_FIAT` | Default ISO fiat for Transak (default `USD`). |
| `DEFAULT_RAMP_FIAT` | Default ISO fiat for Ramp; falls back to `DEFAULT_ONRAMP_FIAT` if unset. |

See `.env.example` for canonical comments.

---

## Swap pair resolution (`resolve_swap_pair_from_suggestions`)

Implementation: `src/utils/celo_token_registry.py`.

- **Inputs:** Groq `suggestions` (list of dicts with `token_in` / `token_out`) and optional **`groq_hint`** string (flattened venues/tokens/labels for keyword scoring).
- **Priority:** first suggestion with two resolvable symbols → symbols found in `groq_hint` (greedy longest-token scan) → default **`CELO` → `USDm`**.
- **Consumers:** `build_personalized_trading_dex_keyboard` (DEX deep links) and `build_onramp_keyboard_rows` (whether to show the on-ramp row and which stable to prefer). **Token addresses** for DEX URLs come from `CELO_MAINNET_TOKENS` in the same module.

---

## Scraper: `fetch_url_text` (`src/utils/url_fetch.py`)

| Aspect | Behavior |
|--------|----------|
| Transport | HTTP(S) `aiohttp`, timeout **15s**, up to **5** redirects |
| Parsing | BeautifulSoup: strip `script` / `style` / `noscript`, `get_text`, ~**12k** chars max |
| Cache | In-process dict: **24h TTL**, successful non-empty body only; max **300** keys |
| Headless | **No** — SPAs / paywalls may yield empty scrape |

---

## Groq contract

- **Calls:** `get_ai_suggestions` + `build_ai_purchase_suggestions_prompt` (`src/ai/prompt_builder.py`).
- **Output:** JSON with `suggestions` array (**2–5** items). Each: `label` (≤64 chars, emoji-led), `venue` (`ubeswap` \| `mento` \| `stcelo`), `token_in`, `token_out`, optional `pair_hint`.
- **Parse:** `parse_ai_suggestions` returns up to **5** dicts; **max_tokens** 768 in `groq_client.py`.
- **When scrape is empty:** Groq is **not** called; UI uses **fallback labels** only. The DEX keyboard still uses `build_personalized_trading_dex_keyboard` with title + empty body + empty Groq hint (keyword scoring on title only). **pair** defaults to **CELO → USDm**, so the **on-ramp** row may still appear (stable **USDm** in pair).

---

## Personalized DEX keyboard (`build_personalized_trading_dex_keyboard`)

- **Input:** `article_title`, `article_body`, optional `groq_hint`, optional `suggestions` (for **pair-aware** Ubeswap / Uniswap / Jumper).
- **Logic:** Keyword scores in `src/utils/defi_links.py` → profile **mento** / **defi** / **stcelo+opera** / **yield** / **balanced** → ordered list of **https://** `url=` buttons (Ubeswap, Mento, Uniswap Celo, Galaxy, Jumper, stCELO Celoscan, Valora, etc.), **2 per row**.

## Fiat on-ramp row (`build_onramp_keyboard_rows` — `src/utils/onramp_links.py`)

- **When:** A **stablecoin symbol** appears in Groq `token_in` / `token_out` **or** in the resolved swap pair from `resolve_swap_pair_from_suggestions` (including the default **CELO → USDm** when suggestions are empty), so the on-ramp row can appear even if Groq was not called (empty scrape).
- **URLs (bases):** Transak `https://global.transak.com/`; Ramp `https://buy.ramp.network/`; directory **Celo on-ramps** `https://docs.celo.org/home/ramps` (no key).
- **Buttons:** **Transak** supports multiple Celo assets (`USDM`, `CUSD`, `CEUR`, `USDC`, `USDT`, `CELO`, `EURC`, `GLO`, … per partner mapping). **Ramp** only builds links for **USDC** and **USDT** (`CELO_USDC` / `CELO_USDT`). **Transak** / **Ramp** buttons appear only if **`TRANSAK_API_KEY`** / **`RAMP_HOST_API_KEY`** are set; the **directory** button is always included when this row is shown.
- **Layout:** **Max 3** `url=` buttons in one row (§4). Optional **`walletAddress`** / **`userAddress`** query params from `db.get_wallet(user_id)` when the user has registered a wallet.

---

## Relationship to `/aitrade`

- Same Groq pipeline + parser.
- **Input:** free-form user text (not article blob).
- **UI:** Up to **5** `ai_pick:` buttons; pick uses `build_venue_links` from selected suggestion (unchanged).

---

## Daily Sources list

- **Extraction:** `extract_links_from_digest` — sections, dedupe, mento-core collapse, `max_daily_source_links`.
- **Formatting:** `format_daily_sources_html` — linked titles, optional source suffix.

---

## Failure modes

| Condition | Typical outcome |
|-----------|-----------------|
| Empty scrape | No Groq; fallback **2–5** emoji lines + DEX keyboard from title keywords; default pair **CELO/USDm** → may still show **on-ramp** + Celo docs row |
| Groq / JSON error | Same as above |
| Expired digest | Empty list / alerts earlier in flow |
| No `TRANSAK_API_KEY` / `RAMP_HOST_API_KEY` | Transak / Ramp buttons omitted; **Celo on-ramps** directory still shown when the on-ramp row is present |

---

## Quick reference

| Question | Answer |
|----------|--------|
| Where is the callback? | `_handle_digest_link` — `src/bot/callbacks.py` |
| Where is fetch + cache? | `fetch_url_text` — `src/utils/url_fetch.py` |
| Where is DEX keyboard? | `build_personalized_trading_dex_keyboard` — `src/utils/defi_links.py` |
| Pair resolution + token addresses? | `resolve_swap_pair_from_suggestions`, `CELO_MAINNET_TOKENS` — `src/utils/celo_token_registry.py` |
| CoinGecko multi-fiat + cache? | `fetch_trade_token_market_stats` — `src/fetchers/coingecko_prices.py` |
| On-ramp rows? | `build_onramp_keyboard_rows` — `src/utils/onramp_links.py` |
| Result screen assembly? | `_edit_ai_trade_result` — `src/bot/callbacks.py` |
| Protected UI rules? | `.cursor/rules/ui_protection.mdc` **§7**, **§8**, **§20** |

---

## Related roadmap

- **Roadmap de Prompts — GovAI Hub nativo.md** — Fase 4 (P8, P8.1, P8.2, P9): Groq suggestions, `/aitrade`, `build_venue_links`. AI Trade digest extensions (pair-aware DEX, **CoinGecko multi-fiat**, **fiat on‑ramps**, HTML copy) are tracked in **`ui_protection.mdc` §7** and **§20**.
