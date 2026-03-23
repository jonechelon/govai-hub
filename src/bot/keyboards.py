from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.config_loader import CONFIG
from src.utils.blockscout_fetcher import blockscout_address_explorer_url
from src.utils.onchain_txlist_format import explorer_address_url

from src.database.models import APPS_AVAILABLE
from src.utils.user_network import network_toggle_label


# Display metadata for each category key (must match APPS_AVAILABLE in models.py).
CATEGORY_DISPLAY: dict[str, tuple[str, str]] = {
    "payments":   ("💳", "Payments & Wallets"),
    "defi":       ("🔄", "DeFi & Swaps"),
    "onramp_nft": ("🌍", "On-ramp & NFTs"),
    "refi_social": ("🌱", "ReFi, Social & Infra"),
}

# Maps lowercase app_name (DB key) to human-readable display label.
APP_DISPLAY: dict[str, str] = {
    "minipay":      "MiniPay",
    "valora":       "Valora",
    "halofi":       "HaloFi",
    "hurupay":      "Hurupay",
    "ubeswap":      "Ubeswap",
    "moola":        "Moola",
    "mento":        "Mento",
    "symmetric":    "Symmetric",
    "mobius":       "Mobius",
    "knox":         "Knox",
    "equalizer":    "Equalizer",
    "uniswap":      "Uniswap",
    "celocashflow": "CeloCashflow",
    "unipos":       "Unipos",
    "octoplace":    "OctoPlace",
    "hypermove":    "Hypermove",
    "truefeedback": "TrueFeedBack",
    "toucan":       "Toucan",
    "toucanrefi":   "Toucan ReFi",
    "impactmarket": "ImpactMarket",
    "masa":         "Masa",
    "celonetwork":  "Celo Network",
    "celoreserve":  "Celo Reserve",
}


def get_main_keyboard(
    preferred_network: str = "mainnet",
    notifications_enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Build the main menu keyboard.

    Note:
        This keyboard is rendered dynamically per-user so it can display the current
        governance network preference and the notification toggle state.
    """
    row1 = [
        InlineKeyboardButton("💹 AI Trade", callback_data="digest:latest"),
        InlineKeyboardButton("⚙️ Settings", callback_data="settings:open"),
    ]
    row2 = [
        InlineKeyboardButton("🏛️ Governance", callback_data="governance:open"),
        InlineKeyboardButton("👛 Set Wallet", callback_data="wallet:open"),
    ]
    vote_alerts_label = (
        "🟢 Vote Alerts" if notifications_enabled else "🔴 Vote Alerts"
    )
    vote_alerts_row = [
        InlineKeyboardButton(vote_alerts_label, callback_data="notify:toggle"),
    ]

    # Show the active network — tap cycles mainnet → alfajores → sepolia (net:switch).
    switch_label = network_toggle_label(preferred_network)
    vote_alerts_row.append(
        InlineKeyboardButton(switch_label, callback_data="net:switch")
    )

    # [💎 Premium] replaced by [💰 Earnings] — Phase 9 Share & Earn preparation
    # premium:* callbacks remain in the codebase as legacy (§17 of ui_protection.mdc)
    row4 = [
        InlineKeyboardButton("💰 Earnings", callback_data="menu:earnings"),
        InlineKeyboardButton("❓ Help", callback_data="help:open"),
    ]
    row5 = [
        InlineKeyboardButton("📜 On-chain activity", callback_data="menu:onchain_hub"),
    ]
    return InlineKeyboardMarkup([row1, row2, vote_alerts_row, row4, row5])


def get_earnings_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Earnings dashboard: Back to main menu (same for /earnings and menu:earnings)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:main")],
    ])


def get_onchain_hub_keyboard() -> InlineKeyboardMarkup:
    """On-chain hub: tx history by scope (not governance:open / not digest:latest)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 All activity", callback_data="onchain:txlist:all"),
            InlineKeyboardButton("🏛️ Governance", callback_data="onchain:txlist:governance"),
            InlineKeyboardButton("💹 AI Trade", callback_data="onchain:txlist:aitrade"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:main")],
    ])


def get_onchain_txlist_keyboard(
    wallet: str | None = None,
    network: str | None = None,
) -> InlineKeyboardMarkup:
    """Full-history URL (own row) + Back to on-chain hub (§4 — no url+callback same row)."""
    rows: list[list[InlineKeyboardButton]] = []
    if wallet:
        n = (network or "mainnet").strip().lower()
        if n == "sepolia":
            explorer_url = explorer_address_url(wallet, network)
        else:
            explorer_url = blockscout_address_explorer_url(wallet, network)
        if explorer_url.startswith("https://"):
            rows.append([
                InlineKeyboardButton(
                    "🔗 Full history on explorer",
                    url=explorer_url,
                ),
            ])
    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="menu:onchain_hub"),
    ])
    return InlineKeyboardMarkup(rows)


def get_help_keyboard() -> InlineKeyboardMarkup:
    """Build the help screen keyboard with quick shortcuts."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏛️ Governance", callback_data="governance_menu"),
            InlineKeyboardButton("💎 Premium", callback_data="premium"),
        ],
        [
            InlineKeyboardButton("« Back to Main Menu", callback_data="main_menu"),
        ],
    ])


def _max_digest_keyboard_buttons() -> int:
    """Align with ``digest.max_daily_source_links`` in config (cap 25)."""
    raw = CONFIG.get("digest", {}).get("max_daily_source_links", 15)
    try:
        return max(1, min(int(raw), 25))
    except (TypeError, ValueError):
        return 15


def get_digest_keyboard(digest_id: str, link_count: int = 0) -> InlineKeyboardMarkup:
    """Build the keyboard for the AI Trade digest view (numbered sources + Back).

    Args:
        digest_id: Cached digest id for callbacks.
        link_count: Number of source rows. Only ``1..link_count`` buttons are shown
            (capped by ``max_daily_source_links``).
    """
    rows: list[list[InlineKeyboardButton]] = []

    rows.append([
        InlineKeyboardButton(
            "💹 AI Trade C-Sources",
            callback_data="noop:aitrade:header",
        ),
    ])

    cap = _max_digest_keyboard_buttons()
    n = max(0, min(int(link_count), cap))
    buttons: list[InlineKeyboardButton] = []
    for i in range(1, n + 1):
        buttons.append(
            InlineKeyboardButton(
                str(i),
                callback_data=f"digest:link:{digest_id}:{i}",
            )
        )
        if len(buttons) == 5:
            rows.append(buttons)
            buttons = []
    if buttons:
        rows.append(buttons)

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="menu:main"),
    ])

    return InlineKeyboardMarkup(rows)


def get_wallet_keyboard(user_network: str = "mainnet") -> InlineKeyboardMarkup:
    """Return the keyboard for the Wallet menu."""
    network_btn_text = network_toggle_label(user_network)
    keyboard = [
        [
            # Replace legacy Premium button with Earnings in setwallet keyboard
            InlineKeyboardButton("💰 Earnings", callback_data="menu:earnings"),
            InlineKeyboardButton("🔍 Gov Status", callback_data="gov:status"),
        ],
        [
            InlineKeyboardButton(network_btn_text, callback_data="net:switch"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_links_keyboard(digest_id: str) -> InlineKeyboardMarkup:
    """Keyboard shown on the links screen — single Back button."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Back to digest",
                callback_data=f"back:{digest_id}",
            )
        ]
    ])


def get_details_keyboard(digest_id: str) -> InlineKeyboardMarkup:
    """Build the keyboard shown on the expanded (full) digest view.

    Includes a Search button (inline mode) in addition to Links, Ask AI, Back and Settings.
    The switch_inline_query_current_chat="" opens inline mode in the current chat with an
    empty query field, ready for the user to type an app name.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Links",      callback_data=f"links:{digest_id}"),
            InlineKeyboardButton("🤖 Ask AI",     callback_data=f"ask:{digest_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back",       callback_data=f"back:{digest_id}"),
            InlineKeyboardButton("⚙️ Settings",   callback_data="settings:open"),
        ],
    ])


def get_settings_keyboard(
    user_apps_by_category: dict[str, list[str]],
    preferred_network: str = "mainnet",
    notifications_enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Root settings screen — 4 category buttons with aggregate enabled state.

    ✅ = all apps in category enabled, ☑️ = some enabled, ☐ = none enabled.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for cat_key, (emoji, label) in CATEGORY_DISPLAY.items():
        all_apps = APPS_AVAILABLE.get(cat_key, [])
        enabled_apps = user_apps_by_category.get(cat_key, [])
        enabled_set = set(enabled_apps)

        if not all_apps:
            continue

        if enabled_set >= set(all_apps):
            state = "✅"
        elif enabled_set:
            state = "☑️"
        else:
            state = "☐"

        rows.append([
            InlineKeyboardButton(
                f"{state} {emoji} {label}",
                callback_data=f"settings:category:{cat_key}",
            )
        ])

    vote_alerts_label = (
        "🟢 Vote Alerts" if notifications_enabled else "🔴 Vote Alerts"
    )
    rows.append(
        [
            InlineKeyboardButton(vote_alerts_label, callback_data="notify:toggle"),
        ]
    )

    # Active network — tap cycles mainnet → alfajores → sepolia (callback net:switch).
    switch_label = network_toggle_label(preferred_network)
    rows.append([
        InlineKeyboardButton(switch_label, callback_data="net:switch"),
    ])

    rows.append([
        InlineKeyboardButton("💾 Save & Close", callback_data="settings_close")
    ])
    # Back to main menu — mandatory last row per §9 of ui_protection.mdc
    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="menu:main"),
    ])
    return InlineKeyboardMarkup(rows)


def get_category_keyboard(
    cat_key: str,
    user_apps_by_category: dict[str, list[str]],
) -> InlineKeyboardMarkup:
    """Category submenu — apps for the selected category in a 2-column grid."""
    emoji, label = CATEGORY_DISPLAY.get(cat_key, ("⚙️", cat_key))
    all_apps = APPS_AVAILABLE.get(cat_key, [])
    enabled_set = set(user_apps_by_category.get(cat_key, []))

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(all_apps), 2):
        row: list[InlineKeyboardButton] = []
        for app in all_apps[i : i + 2]:
            state = "✅" if app in enabled_set else "☑️"
            label_app = APP_DISPLAY.get(app, app.capitalize())
            row.append(
                InlineKeyboardButton(
                    f"{state} {label_app}",
                    callback_data=f"toggle_app:{app}",
                )
            )
        rows.append(row)

    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="settings:open")
    ])
    return InlineKeyboardMarkup(rows)


def get_premium_keyboard() -> InlineKeyboardMarkup:
    """Build the premium purchase keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⭐ 7 days — 0.50 cUSD",
                callback_data="premium:7d",
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ 30 days — 1.50 cUSD",
                callback_data="premium:30d",
            )
        ],
        [
            InlineKeyboardButton(
                "✅ I sent — /confirmpayment",
                callback_data="premium:confirm",
            )
        ],
        # Back to main menu — mandatory last row per §9 of ui_protection.mdc
        [
            InlineKeyboardButton("⬅️ Back", callback_data="menu:main"),
        ],
    ])


def get_premium_plan_keyboard(days: int) -> InlineKeyboardMarkup:
    """Keyboard shown after user selects a specific premium plan (7 or 30 days)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ I sent — /confirmpayment",
                callback_data="premium:confirm",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to plans",
                callback_data="premium:back",
            )
        ],
        [
            InlineKeyboardButton(
                "« Back to Main Menu",
                callback_data="start",
            )
        ],
    ])


def governance_keyboard() -> InlineKeyboardMarkup:
    """Build the governance submenu keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Active Proposals", callback_data="govlist"),
        ],
        [
            InlineKeyboardButton("Voting History", callback_data="govhistory"),
        ],
        [
            InlineKeyboardButton("My Status & Delegate", callback_data="govstatus"),
        ],
        [
            InlineKeyboardButton("« Back to Main Menu", callback_data="start"),
        ],
    ])


def get_governance_keyboard(
    *,
    back_callback: str = "menu:main",
) -> InlineKeyboardMarkup:
    """Return the keyboard for the Governance Hub and governance sub-screens.

    Args:
        back_callback: Back target on the hub root and governance sub-screens.
            Use ``menu:main`` on the hub root (user arrived via /start or main
            menu). Use ``governance_menu`` on sub-screens so Back returns to the
            Governance hub instead of the main menu.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏛 Active Proposals", callback_data="gov:list"),
            InlineKeyboardButton("📜 History", callback_data="gov:history"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
            InlineKeyboardButton("📊 My Status", callback_data="gov:status"),
        ],
    ])


def build_govlist_keyboard(queued: list, active: list) -> InlineKeyboardMarkup:
    """Vote <id> buttons for each unique proposal plus governance hub rows."""
    seen: set[int] = set()
    ids: list[int] = []
    for collection in (queued, active):
        for x in collection:
            try:
                pid = int(x)
            except (TypeError, ValueError):
                continue
            if pid not in seen:
                seen.add(pid)
                ids.append(pid)
    ids.sort()
    ids = ids[:30]

    vote_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for pid in ids:
        row.append(
            InlineKeyboardButton(
                f"🗳 Vote {pid}",
                callback_data=f"gov:voteview:{pid}",
            )
        )
        if len(row) >= 2:
            vote_rows.append(row)
            row = []
    if row:
        vote_rows.append(row)

    hub = get_governance_keyboard(back_callback="governance_menu")
    return InlineKeyboardMarkup(vote_rows + list(hub.inline_keyboard))


def get_proposal_vote_keyboard(proposal_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for a single proposal: vote, auto-trade, AI quick, share, back.

    Layout is fixed per ui_protection §4 (P-ECO.4). Callback prefixes: vote:,
    autotrade:create:, ai_quick:, gov:share:, gov:list.
    """
    pid = proposal_id
    vote_buttons = [
        InlineKeyboardButton("✅ YES", callback_data=f"vote:YES:{pid}"),
        InlineKeyboardButton("❌ NO", callback_data=f"vote:NO:{pid}"),
        InlineKeyboardButton("🤷 ABSTAIN", callback_data=f"vote:ABSTAIN:{pid}"),
    ]
    auto_trade_button = [
        InlineKeyboardButton(
            "🤖 Create Auto-Trade if Approved",
            callback_data=f"autotrade:create:{pid}",
        )
    ]
    ai_quick_hint_row = [
        InlineKeyboardButton(
            "🎒Staking or 🔄Swap?",
            callback_data="noop",
        ),
    ]
    ai_quick_row = [
        InlineKeyboardButton(
            "🔄stCELO",
            callback_data=f"ai_quick:ubescelo:{pid}",
        ),
        InlineKeyboardButton(
            "🔄USDm",
            callback_data=f"ai_quick:mento:{pid}",
        ),
        InlineKeyboardButton(
            "🎒stCELO",
            callback_data=f"ai_quick:stcelo:{pid}",
        ),
    ]
    share_row = [
        InlineKeyboardButton(
            "🔗 Share & Earn",
            callback_data=f"gov:share:{pid}",
        ),
    ]
    return InlineKeyboardMarkup(
        [
            vote_buttons,
            auto_trade_button,
            ai_quick_hint_row,
            ai_quick_row,
            share_row,
            [InlineKeyboardButton("⬅️ Back", callback_data="gov:list")],
        ]
    )
