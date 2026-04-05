import re
from typing import Optional


_ISSUE_PATTERNS = [
    (re.compile(r"\bthis issue\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bthat bug\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bthe issue\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bsame issue\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bthat issue\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bsame thing\b", re.IGNORECASE), "active_issue"),
    (re.compile(r"\bthat feature\b", re.IGNORECASE), "current_topic"),
    (re.compile(r"\bthis feature\b", re.IGNORECASE), "current_topic"),
]

_PRONOUN_PATTERNS = [
    (re.compile(r"(?<!\w)this(?!\w)", re.IGNORECASE), "active_issue"),
    (re.compile(r"(?<!\w)it(?!\w)", re.IGNORECASE), "current_topic"),
]


class ContextResolver:
    """
    Replaces ambiguous references in chunk text with the actual entity
    from the current MeetingContext snapshot before the text is sent to LLM.
    """

    def resolve(self, text: str, context: dict) -> str:
        result = text

        for pattern, ctx_key in _ISSUE_PATTERNS:
            value: Optional[str] = context.get(ctx_key)
            if value and pattern.search(result):
                result = pattern.sub(value, result)

        for pattern, ctx_key in _PRONOUN_PATTERNS:
            value = context.get(ctx_key)
            if value and pattern.search(result):
                result = pattern.sub(value, result)

        return result
