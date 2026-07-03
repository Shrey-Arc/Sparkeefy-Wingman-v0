"""
pricing_calendar.py
--------------------
DeepSeek announced (2026-06-29) that the official V4 release — landing
mid-July 2026, replacing today's preview pricing — introduces 2x peak-hour
surge pricing:

    Peak:     Beijing 09:00-12:00 and 14:00-18:00 (UTC+8)
              => UTC 01:00-04:00 and 06:00-10:00
    Off-peak: everything else, stays at today's baseline rate

This module answers "is it peak right now" so the app can surface real-time
cost awareness instead of a static number in a README that goes stale the
moment the surge actually goes live. Source: DeepSeek's official pricing
docs + June 29 announcement — re-verify against api-docs.deepseek.com once
the official V4 release actually ships, in case the windows shift.
"""

from datetime import datetime, timezone

PEAK_WINDOWS_UTC = [(1, 4), (6, 10)]  # (start_hour, end_hour), UTC, half-open
PEAK_MULTIPLIER = 2.0


def is_peak_hour(now_utc: datetime = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60
    return any(start <= hour < end for start, end in PEAK_WINDOWS_UTC)


def current_rate_note() -> dict:
    """Human-readable pricing status for the sidebar / /health endpoint."""
    peak = is_peak_hour()
    return {
        "peak_pricing_active": peak,
        "multiplier": PEAK_MULTIPLIER if peak else 1.0,
        "note": (
            "DeepSeek peak-hour pricing (2x) is likely active right now"
            if peak
            else "Off-peak — standard DeepSeek rates apply"
        ),
        "caveat": "Surge pricing takes effect with DeepSeek's official V4 release (~mid-July 2026); "
                  "not active during preview. Verify against api-docs.deepseek.com once it ships.",
    }
