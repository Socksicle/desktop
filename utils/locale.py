"""User-visible display-time formatting.

Stores keep raw epoch ints; this module formats at display time
in the active language.
"""
import time
from datetime import datetime

from .i18n import tr, trn


def format_subscription_subtitle(expires_at: float | int | None, now: float | None = None) -> str:
    """Group card expiry line for raw ``expires_at`` epoch."""
    if not expires_at or expires_at <= 0:
        return ""
    remaining = expires_at - (now if now is not None else time.time())
    if remaining <= 0:
        return tr("sub.expired")
    days = int(remaining // 86400)
    if days > 0:
        return tr("sub.expiring_days").format(days=days)
    return tr("sub.unlimited")


def format_days_remaining(expire_ts: float, now: float | None = None) -> str:
    """Expiry card line for raw ``expire_ts`` epoch."""
    remaining = expire_ts - (now if now is not None else time.time())
    days = int(remaining // 86400)
    if days > 0:
        return trn("sub.days_left", days).format(n=days)
    return tr("sub.expired_long")


def format_traffic(used_bytes: int, total_bytes: int) -> str:
    """Traffic card line for raw byte counters."""
    used_gb = used_bytes / (1024 ** 3)
    total_gb = total_bytes / (1024 ** 3)
    rem_gb = max(0.0, total_gb - used_gb)
    return tr("sub.traffic").format(used=used_gb, left=rem_gb, total=total_gb)


def format_expire_date(expire_ts: int) -> str | None:
    """ISO date for raw ``expire`` epoch; None when absent."""
    if not expire_ts:
        return None
    return datetime.fromtimestamp(expire_ts).strftime("%Y-%m-%d")
