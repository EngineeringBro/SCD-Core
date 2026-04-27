"""
Pattern Store — data-driven confidence and resolution patterns mined from the
ticket cache by scripts/mine_patterns.py.

Usage in modules:
    from core.pattern_store import get_topic_pattern, get_email_pattern, get_combo_pattern

All functions return a PatternResult (or None if below confidence threshold).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATTERNS_FILE = Path("knowledge/mined_patterns.json")

# Minimum confidence to return a result (below this = "don't know")
DEFAULT_MIN_CONFIDENCE = 0.70


@dataclass(frozen=True)
class PatternResult:
    total: int
    top_resolution: str
    top_resolution_pct: float
    confidence: float          # min(0.99, top_resolution_pct)
    resolutions: dict[str, int]
    is_bot: bool = False       # only set on email patterns


@lru_cache(maxsize=1)
def _load() -> dict:
    if not PATTERNS_FILE.exists():
        return {}
    with open(PATTERNS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _make_result(p: dict) -> PatternResult:
    return PatternResult(
        total=p["total"],
        top_resolution=p["top_resolution"],
        top_resolution_pct=p["top_resolution_pct"],
        confidence=p["confidence"],
        resolutions=p["resolutions"],
        is_bot=p.get("is_bot", False),
    )


def get_topic_pattern(
    topic: str,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> PatternResult | None:
    """
    Return the mined pattern for *topic*, or None if confidence is below
    *min_confidence* or the topic has no recorded history.
    """
    data = _load()
    p = data.get("by_topic", {}).get(topic)
    if not p or p["confidence"] < min_confidence:
        return None
    return _make_result(p)


def get_email_pattern(
    email: str,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> PatternResult | None:
    """
    Return the mined pattern for reporter *email*, or None.
    is_bot=True means the email is a high-confidence automated sender with a
    consistent resolution pattern.
    """
    data = _load()
    p = data.get("by_reporter_email", {}).get(email.lower().strip())
    if not p or p["confidence"] < min_confidence:
        return None
    return _make_result(p)


def get_combo_pattern(
    topic: str,
    email: str,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> PatternResult | None:
    """
    Return the pattern for the specific (topic, reporter_email) combination.
    This is the most precise signal — prefer over topic-only or email-only when
    the combo has enough history.
    """
    data = _load()
    key = f"{topic}||{email.lower().strip()}"
    p = data.get("by_topic_and_email", {}).get(key)
    if not p or p["confidence"] < min_confidence:
        return None
    return _make_result(p)


def get_bot_emails(min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> list[str]:
    """
    Return emails flagged is_bot=True with confidence >= min_confidence.
    Used by the router to build its email-routing table dynamically from data.
    """
    data = _load()
    return [
        email
        for email, p in data.get("by_reporter_email", {}).items()
        if p.get("is_bot") and p.get("confidence", 0) >= min_confidence
    ]


def summarise(topic: str, email: str | None = None) -> str:
    """
    Return a one-line human-readable summary for use in evidence/diagnosis.
    Falls back gracefully when no pattern exists.
    """
    combo = get_combo_pattern(topic, email or "", min_confidence=0.0) if email else None
    topic_p = get_topic_pattern(topic, min_confidence=0.0)

    if combo and combo.total >= 10:
        return (
            f"Historical pattern ({combo.total} tickets with this topic+sender): "
            f"{combo.top_resolution_pct*100:.0f}% resolved as '{combo.top_resolution}' "
            f"(confidence {combo.confidence:.2f})"
        )
    if topic_p:
        return (
            f"Historical pattern ({topic_p.total} tickets with topic '{topic}'): "
            f"{topic_p.top_resolution_pct*100:.0f}% resolved as '{topic_p.top_resolution}' "
            f"(confidence {topic_p.confidence:.2f})"
        )
    return f"No historical pattern available for topic '{topic}'"
