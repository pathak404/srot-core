import re
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .rule_filter import FilterResult

_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_]+|\"[^\"]+\")\b")
_COMMON_WORDS = {"I", "We", "They", "He", "She", "It", "The", "A", "An", "This", "That"}


@dataclass
class MeetingContext:
    current_topic: Optional[str] = None
    active_issue: Optional[str] = None
    open_tasks: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    last_entities: list = field(default_factory=list)
    history: deque = field(default_factory=lambda: deque(maxlen=10))


class ContextManager:
    """
    Maintains conversation state for a single meeting session.
    Updated once per candidate FilterResult.
    """

    def __init__(self):
        self.context = MeetingContext()

    def update(self, result: FilterResult):
        text = result.chunk.text
        raw = result.chunk.raw_text

        # History (use normalized text)
        self.context.history.append(text)

        # Entity extraction from raw text (preserves capitalization)
        entities = [e for e in _ENTITY_RE.findall(raw) if e not in _COMMON_WORDS]
        for entity in entities:
            if entity not in self.context.last_entities:
                self.context.last_entities.append(entity)
        # Keep last_entities to 20 most recent
        self.context.last_entities = self.context.last_entities[-20:]

        # Active issue: set when issue keyword detected
        if result.keyword_type == "issue":
            # Use entity if available, otherwise use first part of text
            self.context.active_issue = entities[0] if entities else text.split()[0] if text else None

        # Current topic: most common entity across last 3 history entries
        if len(self.context.history) >= 3:
            recent = " ".join(list(self.context.history)[-3:])
            freq: dict[str, int] = {}
            for e in _ENTITY_RE.findall(recent):
                if e not in _COMMON_WORDS:
                    freq[e] = freq.get(e, 0) + 1
            if freq:
                self.context.current_topic = max(freq, key=lambda k: freq[k])

        # Decisions
        if result.keyword_type == "decision":
            self.context.decisions.append(text)

    def get_snapshot(self) -> dict:
        return {
            "current_topic": self.context.current_topic,
            "active_issue": self.context.active_issue,
            "open_tasks": list(self.context.open_tasks),
            "decisions": list(self.context.decisions),
            "last_entities": list(self.context.last_entities),
        }
