import re
from .rule_filter import FilterResult

_UNRESOLVED_PRONOUNS = re.compile(r"(?<!\w)(this|that|it)(?!\w)", re.IGNORECASE)


class LLMTrigger:
    """
    Binary gate. Returns True (call LLM) if ANY condition is met:

    1. keyword_type is "action" or "decision"
    2. keyword_type is "time" — ETA extraction needed
    3. keyword_type is "issue" AND active_issue is None
    4. confidence_tier is "LOW"
    5. Resolved text still contains unresolved pronouns (this/that/it)
    """

    def should_call(self, result: FilterResult, resolved_text: str, context: dict) -> bool:
        ktype = result.keyword_type
        tier = result.chunk.confidence_tier

        if ktype in ("action", "decision"):
            return True

        if ktype == "time":
            return True

        if ktype == "issue" and not context.get("active_issue"):
            return True

        if tier == "LOW":
            return True

        if _UNRESOLVED_PRONOUNS.search(resolved_text):
            return True

        return False
