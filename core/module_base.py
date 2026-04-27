"""
Module base class. Every module must inherit from this and implement matches() and run().
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.resolution_suggestion import ResolutionSuggestion
    from core.jira_clients import JiraReadClient


class Module(ABC):
    name: str = ""
    version: str = "1.0.0"
    needs_local_run: bool = False   # True = module requires Playwright + local Chrome session

    @abstractmethod
    def matches(self, ticket: dict) -> bool:
        """Return True if this module should handle the given ticket."""
        ...

    @abstractmethod
    def run(self, ticket: dict, jira: JiraReadClient) -> ResolutionSuggestion:
        """
        Analyse the ticket and return a complete ResolutionSuggestion.
        READ-ONLY. No writes. No side effects.
        """
        ...
