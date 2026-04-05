import json
import os

from google import genai
from dotenv import load_dotenv

from .context_manager import ContextManager

load_dotenv()

_SUMMARIZE_PROMPT = """Summarize the following meeting discussion history in 3-5 sentences.
Also list the key entities (people, systems, features) mentioned.

History:
{history}

Decisions so far:
{decisions}

Return ONLY valid JSON (no markdown fences):
{{
  "summary": "3-5 sentence summary",
  "active_entities": ["entity1", "entity2", ...]
}}"""


class PeriodicSummarizer:
    """
    Every SUMMARIZE_INTERVAL_MINUTES (default 5), compresses the context history
    by sending it to Gemini and replacing history with the summary + key entities.

    Call tick() on every pipeline cycle; it handles timing internally.
    """

    def __init__(self, interval_minutes: int = None):
        env_interval = os.getenv("SUMMARIZE_INTERVAL_MINUTES")
        self._interval_minutes = interval_minutes or (int(env_interval) if env_interval else 5)
        self._interval_seconds = self._interval_minutes * 60
        self._elapsed_seconds = 0.0
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        self._model = "gemini-2.5-flash-lite"

    async def tick(self, elapsed_since_last_tick: float, context_mgr: ContextManager):
        """
        Call once per processed chunk with the chunk's audio duration.
        Runs summarization when accumulated time exceeds the interval.
        """
        self._elapsed_seconds += elapsed_since_last_tick
        if self._elapsed_seconds >= self._interval_seconds:
            await self._summarize(context_mgr)
            self._elapsed_seconds = 0.0

    async def _summarize(self, context_mgr: ContextManager):
        history_text = "\n".join(context_mgr.context.history)
        decisions_text = "\n".join(context_mgr.context.decisions) or "None yet"

        prompt = _SUMMARIZE_PROMPT.format(
            history=history_text,
            decisions=decisions_text,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            raw = response.text.strip().lstrip("```json").rstrip("```").strip()
            result = json.loads(raw)
            summary = result.get("summary", "")
            entities = result.get("active_entities", [])

            context_mgr.context.history.clear()
            if summary:
                context_mgr.context.history.append(summary)
            context_mgr.context.last_entities = entities
        except Exception:
            pass  # On failure, keep existing history unchanged
