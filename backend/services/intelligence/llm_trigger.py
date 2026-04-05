import re
import os
import logging
from google import genai
from .rule_filter import FilterResult

_log = logging.getLogger(__name__)

_UNRESOLVED_PRONOUNS = re.compile(r"(?<!\w)(this|that|it)(?!\w)", re.IGNORECASE)

_INTENT_CLASSIFIER_PROMPT = """You are a high-speed intent classifier for a dev tool.
Determine if the following transcript chunk contains a technical action, bug report, engineering decision, or project timeline.
Ignore casual talk, greetings, or filler.

Transcript: "{TEXT}"

Answer 'YES' if it contains engineering intent, 'NO' otherwise. Answer ONLY 'YES' or 'NO'."""

class LLMTrigger:
    """
    Binary gate. Returns True (call LLM) if ANY condition is met:

    1. Fast Path (Regex): keyword_type is "action", "decision", "time", or "issue".
    2. Fallback (Lightweight LLM): If keyword_type is None, use a cheap LLM to classify intent.
    3. Safety: confidence_tier is "LOW" or contains unresolved pronouns.
    """

    def __init__(self, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        self._model = model

    async def _is_intent_llm(self, text: str) -> bool:
        """Call lightweight LLM for binary intent classification."""
        if len(text.split()) < 5:
            return False
            
        prompt = _INTENT_CLASSIFIER_PROMPT.format(TEXT=text)
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            answer = (response.text or "").strip().upper()
            return "YES" in answer
        except Exception as e:
            _log.warning(f"Lightweight intent classification failed: {e}")
            return False

    async def should_call(self, result: FilterResult, resolved_text: str, context: dict) -> bool:
        ktype = result.keyword_type
        tier = result.chunk.confidence_tier

        # 1. Fast Path: Known keywords
        if ktype in ("action", "decision", "time"):
            return True

        if ktype == "issue" and not context.get("active_issue"):
            return True

        # 2. Safety: Low confidence or unresolved context
        if tier == "LOW":
            return True

        if _UNRESOLVED_PRONOUNS.search(resolved_text):
            return True

        # 3. Hybrid Path: Lightweight LLM check for chunks with no regex keyword
        if ktype is None:
            return await self._is_intent_llm(resolved_text)

        return False
