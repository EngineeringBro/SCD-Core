"""
Router — reads module_registry.yaml and matches a ticket to the correct module.
Ordered match: first matching rule wins. Falls through to General if no match.

Email-based routing for the bot_filter module is driven dynamically from
core.pattern_store (mined from 50K+ historical tickets) rather than a
hardcoded list.  Only emails where is_bot=True AND confidence >= 0.85 in
the mined data are routed to bot_filter.
"""
from __future__ import annotations
import importlib
import pkgutil
import yaml
from pathlib import Path
from core.module_base import Module
from core.pattern_store import get_bot_emails


REGISTRY_FILE = Path("configs/module_registry.yaml")


def load_registry() -> list[dict]:
    return yaml.safe_load(REGISTRY_FILE.read_text()).get("modules", [])


def discover_modules() -> dict[str, Module]:
    """Auto-import all Module subclasses from modules/ subpackages."""
    import modules as modules_pkg
    found: dict[str, Module] = {}
    for finder, name, _ in pkgutil.iter_modules(modules_pkg.__path__):
        mod = importlib.import_module(f"modules.{name}.module")
        for attr in dir(mod):
            cls = getattr(mod, attr)
            if (
                isinstance(cls, type)
                and issubclass(cls, Module)
                and cls is not Module
                and cls.name
            ):
                found[cls.name] = cls()
    return found


def route(ticket: dict, registry: list[dict], module_map: dict[str, Module]) -> Module | None:
    topic_id = str(
        (ticket.get("fields", {}).get("customfield_10170") or {}).get("id", "")
    )
    subject = (ticket.get("fields", {}).get("summary") or "").lower()
    description = _extract_plain_text(
        ticket.get("fields", {}).get("description") or {}
    ).lower()
    reporter_email = (
        (ticket.get("fields", {}).get("reporter") or {}).get("emailAddress", "") or ""
    ).lower().strip()

    # Build bot email set once per call (pattern_store uses lru_cache — fast).
    # Only emails where is_bot=True AND confidence >= 0.85 in historical data.
    bot_email_set = set(get_bot_emails(min_confidence=0.85))

    for rule in registry:
        module_name = rule.get("module", "")

        # Deterministic topic field match
        if topic_id and topic_id in [str(t) for t in rule.get("topic_field_ids", [])]:
            return module_map.get(module_name)

        # auto_senders: email list comes from mined patterns, not registry
        if module_name == "auto_senders":
            if reporter_email and reporter_email in bot_email_set:
                return module_map.get(module_name)
            continue  # skip keyword/email checks for auto_senders

        # Reporter email match for other modules (exact address or @domain suffix)
        for pattern in rule.get("reporter_emails", []):
            p = pattern.lower().strip()
            if p.startswith("@"):
                if reporter_email.endswith(p):
                    return module_map.get(module_name)
            elif reporter_email == p:
                return module_map.get(module_name)

        # Keyword match on subject or description
        for kw in rule.get("keywords", []):
            if kw.lower() in subject or kw.lower() in description:
                return module_map.get(module_name)

    # Fallback to General module
    return module_map.get("general")


def _extract_plain_text(adf: dict) -> str:
    """Recursively extract plain text from Jira ADF content."""
    if not adf:
        return ""
    parts = []
    if adf.get("type") == "text":
        parts.append(adf.get("text", ""))
    for child in adf.get("content", []):
        parts.append(_extract_plain_text(child))
    return " ".join(parts)
