from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.database.models import APPS_AVAILABLE


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
        InlineKeyboardButton("📰 Latest Digest", callback_data="digest:latest"),
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

    # Show the active network so users always know where they are.
    # Tapping the button toggles to the other network via net:switch.
    if preferred_network == "alfajores":
        switch_label = "🍪 Alfajores"
    else:
        switch_label = "🟡 Mainnet"

    vote_alerts_row.append(
        InlineKeyboardButton(switch_label, callback_data="net:switch")
    )

    row4 = [
        InlineKeyboardButton("💎 Premium", callback_data="premium:open"),
        InlineKeyboardButton("❓ Help", callback_data="help:open"),
    ]
    return InlineKeyboardMarkup([row1, row2, vote_alerts_row, row4])


def get_digest_keyboard(digest_id: str) -> InlineKeyboardMarkup:
    """Build the keyboard attached to a digest message."""
    row1 = [
        InlineKeyboardButton("📰 Details", callback_data=f"details:{digest_id}"),
        InlineKeyboardButton("🔗 Links", callback_data=f"links:{digest_id}"),
    ]
    row2 = [
        InlineKeyboardButton("🤖 Ask AI", callback_data=f"ask:{digest_id}"),
        InlineKeyboardButton("⚙️ Settings", callback_data="settings:open"),
    ]
    row3 = [
        InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main"),
    ]
    return InlineKeyboardMarkup([row1, row2, row3])


def get_wallet_keyboard(user_network: str = "mainnet") -> InlineKeyboardMarkup:
    """Return the keyboard for the Wallet menu."""
    network_btn_text = "🍪 Alfajores" if user_network == "alfajores" else "🟡 Mainnet"
    keyboard = [
        [
            InlineKeyboardButton("⭐️ Premium", callback_data="menu:premium"),
            InlineKeyboardButton("🔍 Gov Status", callback_data="gov:status"),
        ],
        [
            InlineKeyboardButton(
                f"{network_btn_text} Network",
                callback_data="net:switch",
            )
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

    # Show the active network so users always know where they are.
    # Tapping the button toggles to the other network via net:switch.
    if preferred_network == "alfajores":
        switch_label = "🍪 Alfajores"
    else:
        switch_label = "🟡 Mainnet"

    rows.append(
        [
            InlineKeyboardButton(switch_label, callback_data="net:switch"),
        ]
    )

    rows.append([
        InlineKeyboardButton("💾 Save & Close", callback_data="settings_close")
    ])
    rows.append([
        InlineKeyboardButton("« Back to Main Menu", callback_data="start")
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
        [
            InlineKeyboardButton(
                "« Back to Main Menu",
                callback_data="start",
            )
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


def get_governance_keyboard() -> InlineKeyboardMarkup:
    """Return the keyboard for the Governance Hub."""
    keyboard = [
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu:main")]
    ]
    return InlineKeyboardMarkup(keyboard)
