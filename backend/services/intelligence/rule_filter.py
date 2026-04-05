import re
from dataclasses import dataclass
from typing import Literal, Optional

from .chunk_processor import ProcessedChunk

_GREETING_PATTERNS = re.compile(
    r"\b(good morning|good afternoon|good evening|how are you|nice to meet|hello everyone|hi everyone|hey everyone)\b",
    re.IGNORECASE,
)

_PATTERNS = {
    "action": re.compile(r"\b(need|have|going|want|plan|ought|supposed)\s+to\b|\b(gonna|will|shall|should|must|let'?s|assign|fix|take care of|handle|look into|follow up|check|verify|implement|update|create|build|push|deploy|test|make sure|ensure|try to|attempt to)\b|\b(karna (hai|padega)|kar dena|kar lunga( main)?|dekh lunga|sambhal lunga|try karta hu|kar diya jayega|dekh lo|dekh lena|check karna|implement karna|bana dena|dal dena|update kar dena|nikalna hai|change kar denge|add karo|karo|dekhna (hai|padega)|banana hai)\b", re.IGNORECASE),
    "issue": re.compile(r"\b(bug|error|issue|problem|broken|crash|failing|not working|failed|glitch|lag|slow|stuck|breaking|inconsistent|not loading|not responding|blocked|blocker|failing intermittently|unexpected behavior|mismatch)\b|\b(fat gaya|chal nahi raha|dikkat|masla|atak raha hai|ruk gaya|load nahi ho raha|slow chal raha hai|hang ho raha hai|issue aa raha hai|dikkat aa rahi hai|fix karo|scene kharab hai|kaam nahi kar raha|toot gaya|fix kar denge)\b", re.IGNORECASE),
    "time": re.compile(r"\b(deadline|eta|by (monday|tuesday|wednesday|thursday|friday)|next week|end of day|eod|tomorrow|asap|today|tonight|this evening|this week|early next week|before release|before deploy|in 2 hours|in a bit|right away|immediately|urgently)\b|\b(kal tak|aaj shaam tak|abhi|thodi der mein|jaldi|turant|abhi ke abhi|shaam tak|subah tak|is week|next sprint|release se pehle)\b", re.IGNORECASE),
    "decision": re.compile(r"\b(decided|agreed|going with|we'll go|confirmed|finalized|chose|settled on|let's do this|we'll proceed with|sounds good|makes sense|okay with this|locked|approved|agreed on this|this works)\b|\b(final ho gaya|done hai|fix ho gaya|theek hai|theek lag raha hai|ho jayega|yehi karte hain|isko le lete hain|final hai|pakka|done hai bhai|chal ye karte hain)\b", re.IGNORECASE),
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

