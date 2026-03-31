import json
import os
import re
from typing import Optional

from google import genai
from dotenv import load_dotenv

load_dotenv()

_SYSTEM_PROMPT = """You are a meeting intelligence assistant that extracts ONLY developer-actionable tasks.

Return ONLY valid JSON. No markdown fences. No explanation.

Schema:
{
  "task": "short imperative title or null",
  "description": "bullet points of actual development work only, or null",
  "eta": "deadline string or null",
  "type": "bug | feature | question | decision | null",
  "assignee": "person name or null"
}

Rules:
- ONLY create a task if actual engineering/development work is required
- Return all null fields for: greetings, status checks, questions about process/setup, QA-only tasks, informational sharing, general chatter
- Description must be bullet points of concrete development steps
- Mention the affected NestJS service name in description if identifiable (e.g. payout.service.ts)
- For "decision" type: set task to the decision, description to the rationale

If nothing in the chunk requires engineering work, return all fields as null."""


class GeminiLLM:
    """
    Calls Gemini with a structured {context, chunk} input.
    Returns a parsed dict with keys: task, description, eta, type, assignee.
    All values may be null.

    Never receives raw full transcript, only resolved chunk text + context snapshot.
    """

    def __init__(self, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        self._model = model

    async def process(self, llm_input: dict) -> dict:
        """
        llm_input: {"context": {...}, "chunk": "resolved text", "code_context": "optional"}
        Returns: {"task": ..., "description": ..., "eta": ..., "type": ..., "assignee": ...}
        """
        code_context = llm_input.get("code_context", "")
        prompt = f"{_SYSTEM_PROMPT}\n\n"
        if code_context:
            prompt += (
                f"{code_context}\n\n"
                "Apply the code context above to:\n"
                "- Use code context to understand the context of the tasks.\n"
                "- NEVER mention graphql.ts or graphql.schema.ts file names.\n"
                "- NEVER mention secret keys, credentials, or any other sensitive information.\n\n"
            )
        prompt += (
            f"Context: {json.dumps(llm_input.get('context', {}))}\n"
            f"Chunk: {llm_input.get('chunk', '')}"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except (json.JSONDecodeError, Exception):
            return {"task": None, "description": None, "eta": None, "type": None, "assignee": None}
