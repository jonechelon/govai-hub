from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


APPS_BY_CATEGORY: dict[str, list[str]] = {
    "payments": ["MiniPay", "Valora", "HaloFi", "Hurupay"],
    "defi": ["Ubeswap", "Moola", "Mento", "Symmetric", "Mobius", "Knox", "Equalizer", "Uniswap"],
    "onramp": ["CeloCashflow", "Unipos"],
    "nfts": ["OctoPlace", "Hypermove", "TrueFeedBack"],
    "refi": ["Toucan", "ToucanReFi"],
    "social": ["ImpactMarket", "Masa"],
    "network": ["CeloNetwork", "CeloReserve"],
}

CATEGORY_LABELS: dict[str, str] = {
    "payments": "💳 Payments & Wallets",
    "defi": "🔁 DeFi & Swaps",
    "onramp": "🏦 On-ramp / Off-ramp",
    "nfts": "🎨 NFTs & Games",
    "refi": "🌱 ReFi & Carbon",
    "social": "🤝 Social & Identity",
    "network": "🌐 Network & Infra",
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


def get_settings_keyboard(user_apps: dict[str, bool]) -> InlineKeyboardMarkup:
    """Build the settings keyboard with app toggles grouped by category."""
    rows: list[list[InlineKeyboardButton]] = []

    for category, apps in APPS_BY_CATEGORY.items():
        # Category header (non-interactive)
        rows.append([InlineKeyboardButton(CATEGORY_LABELS[category], callback_data="noop")])

        # App toggle buttons for this category
        app_buttons: list[InlineKeyboardButton] = []
        for app_name in apps:
            enabled = user_apps.get(app_name, True)
            prefix = "✅" if enabled else "☑️"
            app_buttons.append(
                InlineKeyboardButton(
                    f"{prefix} {app_name}",
                    callback_data=f"toggle_app:{app_name}",
                )
            )

        # Distribute app buttons in rows of two
        for i in range(0, len(app_buttons), 2):
            rows.append(app_buttons[i : i + 2])

    # Final action row
    rows.append([InlineKeyboardButton("💾 Save & Close", callback_data="settings:close")])

    return InlineKeyboardMarkup(rows)


def get_premium_keyboard() -> InlineKeyboardMarkup:
    """Build the premium purchase keyboard."""
    rows = [
        [InlineKeyboardButton("⭐ 7 days — 0.50 cUSD", callback_data="premium:7d")],
        [InlineKeyboardButton("⭐ 30 days — 1.50 cUSD", callback_data="premium:30d")],
        [InlineKeyboardButton("✅ I sent — /confirmpayment", callback_data="premium:confirm")],
    ]
    return InlineKeyboardMarkup(rows)
