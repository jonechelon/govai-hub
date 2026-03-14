from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.database.models import APPS_AVAILABLE


# Display metadata for each category key used in APPS_AVAILABLE.
CATEGORY_DISPLAY: dict[str, tuple[str, str]] = {
    # internal_key: (emoji, display_label)
    "payments": ("💳", "Payments & Wallets"),
    "defi":     ("🔄", "DeFi & Swaps"),
    "onramp":   ("🌍", "On-ramp / Off-ramp"),
    "nfts":     ("🎨", "NFTs & Games"),
    "refi":     ("🌱", "ReFi & Carbon"),
    "social":   ("🧑‍🤝‍🧑", "Social & Identity"),
    "network":  ("🧱", "Network & Infra"),
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


def get_main_keyboard() -> InlineKeyboardMarkup:
    """Build the main menu keyboard."""
    row1 = [
        InlineKeyboardButton("📰 Latest Digest", callback_data="digest:latest"),
        InlineKeyboardButton("⚙️ Settings", callback_data="settings:open"),
    ]
    row2 = [
        InlineKeyboardButton("💎 Premium", callback_data="premium:open"),
        InlineKeyboardButton("❓ Help", callback_data="help:open"),
    ]
    return InlineKeyboardMarkup([row1, row2])


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
    return InlineKeyboardMarkup([row1, row2])


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
            InlineKeyboardButton(
                "🔍 Search app...",
                switch_inline_query_current_chat="",
            ),
        ],
        [
            InlineKeyboardButton("⬅️ Back",       callback_data=f"back:{digest_id}"),
            InlineKeyboardButton("⚙️ Settings",   callback_data="settings:open"),
        ],
    ])


def get_settings_keyboard(
    user_apps_by_category: dict[str, list[str]],
) -> InlineKeyboardMarkup:
    """Build settings keyboard with app toggles grouped by category.

    Args:
        user_apps_by_category: {category: [app_name, ...]} with only enabled apps,
            as returned by db.get_user_apps_by_category(user_id).

    Returns:
        InlineKeyboardMarkup with category headers and app toggle buttons in a 2-column grid.
    """
    rows: list[list[InlineKeyboardButton]] = []

    for category, apps in APPS_AVAILABLE.items():
        emoji, label = CATEGORY_DISPLAY.get(category, ("📦", category.title()))

        # Category header — non-interactive label row
        rows.append([
            InlineKeyboardButton(
                text=f"{emoji} {label}",
                callback_data="noop",
            )
        ])

        enabled_in_category: list[str] = user_apps_by_category.get(category, [])

        app_buttons: list[InlineKeyboardButton] = []
        for app_name in apps:
            is_enabled = app_name in enabled_in_category
            prefix = "✅" if is_enabled else "☑️"
            display = APP_DISPLAY.get(app_name, app_name.title())
            app_buttons.append(
                InlineKeyboardButton(
                    text=f"{prefix} {display}",
                    callback_data=f"toggle_app:{app_name}",
                )
            )

        # 2-column grid for app buttons within this category
        for i in range(0, len(app_buttons), 2):
            rows.append(app_buttons[i : i + 2])

    rows.append([
        InlineKeyboardButton(text="💾 Save & Close", callback_data="settings_close")
    ])

    return InlineKeyboardMarkup(rows)


def get_premium_keyboard() -> InlineKeyboardMarkup:
    """Build the premium purchase keyboard."""
    rows = [
        [InlineKeyboardButton("⭐ 7 days — 0.50 cUSD", callback_data="premium:7d")],
        [InlineKeyboardButton("⭐ 30 days — 1.50 cUSD", callback_data="premium:30d")],
        [InlineKeyboardButton("✅ I sent — /confirmpayment", callback_data="premium:confirm")],
    ]
    return InlineKeyboardMarkup(rows)
