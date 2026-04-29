"""
telegram_bot.py
---------------
Minimal Telegram alert helper for the Immo Scraper project.

Environment variables (loaded from .env via python-dotenv):
  TELEGRAM_BOT_TOKEN  – Bot token obtained from @BotFather
  TELEGRAM_CHAT_ID    – Target chat/channel ID
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

_TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(message: str) -> bool:
    """
    Send a Markdown-formatted message to the configured Telegram chat.

    Args:
        message: The text to send. Supports Telegram MarkdownV2 syntax.

    Returns:
        ``True`` if the message was delivered successfully, ``False`` otherwise.

    Raises:
        EnvironmentError: If ``TELEGRAM_BOT_TOKEN`` or ``TELEGRAM_CHAT_ID``
                          are not set.
    """
    if not _BOT_TOKEN or not _CHAT_ID:
        raise EnvironmentError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set "
            "(check your .env file)."
        )

    url = _TELEGRAM_API_URL.format(token=_BOT_TOKEN)
    payload = {
        "chat_id": _CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Telegram alert sent successfully (chat_id=%s).", _CHAT_ID)
        return True
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to send Telegram alert: %s", exc)
        return False


def format_new_property_message(prop: dict) -> str:
    """
    Build a Markdown-formatted alert for a *newly discovered* property.

    Args:
        prop: Dictionary with property data (same shape as the DB row).

    Returns:
        Formatted string ready to pass to :func:`send_alert`.
    """
    pool_emoji = "🏊" if prop.get("has_pool") else ""
    ac_emoji = "❄️" if prop.get("has_ac") else ""
    extras = " ".join(filter(None, [pool_emoji, ac_emoji]))
    similarity_score = prop.get("similarity_score")
    similarity_profile = prop.get("similarity_profile")
    similarity_line = ""
    if similarity_score is not None:
        profile_text = f" · {similarity_profile}" if similarity_profile else ""
        similarity_line = f"\n🎯 Similitud: *{similarity_score}/100*{profile_text}"

    return (
        f"🏠 *Nova propietat detectada* {extras}\n\n"
        f"*{_escape(prop.get('title', 'Sense títol'))}*\n"
        f"💰 Preu: *{_format_price(prop.get('price'))} €*\n"
        f"🛏 Habitacions: {prop.get('rooms', '?')}\n"
        f"🚿 Banys: {prop.get('bathrooms', '?')}\n"
        f"📐 Superfície: {prop.get('sqm', '?')} m²\n"
        f"{similarity_line}"
        f"🔗 [Veure anunci]({prop.get('url', '')})"
    )


def format_price_drop_message(prop: dict, old_price: int) -> str:
    """
    Build a Markdown-formatted alert for a *price drop* on an existing property.

    Args:
        prop:      Dictionary with the *updated* property data.
        old_price: The previous price (before the drop).

    Returns:
        Formatted string ready to pass to :func:`send_alert`.
    """
    new_price = prop.get("price", 0)
    drop = old_price - new_price
    pct = (drop / old_price * 100) if old_price else 0
    similarity_score = prop.get("similarity_score")
    similarity_profile = prop.get("similarity_profile")
    similarity_line = ""
    if similarity_score is not None:
        profile_text = f" · {similarity_profile}" if similarity_profile else ""
        similarity_line = f"\n🎯 Similitud: *{similarity_score}/100*{profile_text}"

    return (
        f"📉 *Baixada de preu detectada!*\n\n"
        f"*{_escape(prop.get('title', 'Sense títol'))}*\n"
        f"💰 Preu antic: ~~{_format_price(old_price)} €~~\n"
        f"💰 Preu nou:  *{_format_price(new_price)} €*\n"
        f"📉 Baixada: -{_format_price(drop)} € ({pct:.1f}%)\n"
        f"🛏 Habitacions: {prop.get('rooms', '?')}\n"
        f"🚿 Banys: {prop.get('bathrooms', '?')}\n"
        f"{similarity_line}"
        f"🔗 [Veure anunci]({prop.get('url', '')})"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape special characters for Telegram Markdown (v1)."""
    # Telegram Markdown v1 only needs a few characters escaped inside bold/italic
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


def _format_price(value: object) -> str:
    if value is None:
        return "?"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)

    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")
