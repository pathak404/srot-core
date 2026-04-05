import re
from dataclasses import dataclass
from typing import Literal, Optional

from .chunk_processor import ProcessedChunk

_GREETING_PATTERNS = re.compile(
    r"\b(good morning|good afternoon|good evening|how are you|nice to meet|hello everyone|hi everyone|hey everyone)\b",
    re.IGNORECASE,
)

_PATTERNS = {
    "action": re.compile(r"\b(need|have|going|want|plan|ought)\s+to\b|\b(will|shall|should|must|let'?s|assign|fix)\b", re.IGNORECASE),
    "issue": re.compile(r"\b(bug|error|issue|problem|broken|crash|failing|not working|failed)\b", re.IGNORECASE),
    "time": re.compile(r"\b(deadline|eta|by (monday|tuesday|wednesday|thursday|friday)|next week|end of day|eod|tomorrow)\b", re.IGNORECASE),
    "decision": re.compile(r"\b(decided|agreed|going with|we'll go|confirmed|finalized|chose|settled on)\b", re.IGNORECASE),
}


@dataclass
class FilterResult:
    type: Literal["candidate", "noise"]
    keyword_type: Optional[str]
    chunk: ProcessedChunk


def _token_overlap(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


class RuleFilter:
    def __init__(self):
        self._last_accepted_text: Optional[str] = None

    def filter(self, chunk: ProcessedChunk) -> FilterResult:
        text = chunk.text

        # Near-duplicate check
        if self._last_accepted_text and _token_overlap(text, self._last_accepted_text) > 0.70:
            return FilterResult(type="noise", keyword_type=None, chunk=chunk)

        # Greeting check
        if _GREETING_PATTERNS.search(text):
            return FilterResult(type="noise", keyword_type=None, chunk=chunk)

        # Keyword detection via Regex (priority order: action > issue > time > decision)
        for ktype, pattern in _PATTERNS.items():
            if pattern.search(text):
                self._last_accepted_text = text
                return FilterResult(type="candidate", keyword_type=ktype, chunk=chunk)

        # Short filler with no keywords
        if len(text.split()) < 5:
            return FilterResult(type="noise", keyword_type=None, chunk=chunk)

        # Longer text with no keyword - still a candidate
        self._last_accepted_text = text
        return FilterResult(type="candidate", keyword_type=None, chunk=chunk)

