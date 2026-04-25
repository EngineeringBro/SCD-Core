"""
Router — reads module_registry.yaml and matches a ticket to the correct module.
Ordered match: first matching rule wins. Falls through to General if no match.
"""
from __future__ import annotations
import importlib
import os
import pkgutil
import yaml
from pathlib import Path
from core.module_base import Module


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

    for rule in registry:
        # Deterministic topic field match
        if topic_id and topic_id in [str(t) for t in rule.get("topic_field_ids", [])]:
            return module_map.get(rule["module"])

        # Keyword match on subject or description
        for kw in rule.get("keywords", []):
            if kw.lower() in subject or kw.lower() in description:
                return module_map.get(rule["module"])

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
