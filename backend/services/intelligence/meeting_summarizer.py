import os
import re
from typing import Optional

from google import genai
from dotenv import load_dotenv

load_dotenv()

_GLOSSARY = os.getenv("TRANSCRIPTION_GLOSSARY", "")
_EXTRA_PROMPT = os.getenv("TRANSCRIPTION_EXTRA_PROMPT", "")

_MEETING_SUMMARY_PROMPT = """You are a meeting summarization assistant.

Produce an updated cumulative meeting summary in Markdown based on the current summary and new transcript text.

Current summary (empty if meeting just started):
{current_summary}

New transcript:
{new_transcript}

Rules:
- Return a single cumulative Markdown document covering the entire meeting so far
- Use ## headings for distinct topics discussed
- Use bullet points for key discussion points
- Use **bold** for decisions made
- Preserve all content from the current summary and incorporate new information
- If current summary is empty, start fresh from the new transcript
- Omit greetings, filler words, and off-topic chatter

Return ONLY valid Markdown. No JSON, no code fences, no explanation."""


class MeetingSummarizer:
    """
    Incrementally builds a cumulative Markdown meeting summary.

    Call add_chunk() for every transcript chunk.
    When should_update() is True, call update() to trigger an LLM refresh.
    Call flush() at end-of-meeting to process any remaining buffer.
    """

    def __init__(self, chunk_threshold: int = 8):
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        self._model = "gemini-2.5-flash-lite"
        self._chunk_threshold = chunk_threshold
        self._buffer: list[str] = []
        self._summary: str = ""

    def add_chunk(self, text: str):
        if text.strip():
            self._buffer.append(text.strip())

    def should_update(self) -> bool:
        return len(self._buffer) >= self._chunk_threshold

    async def update(self):
        """Call LLM to update summary with buffered chunks. Buffer preserved on failure."""
        if not self._buffer:
            return
        new_text = " ".join(self._buffer)
        prompt = _MEETING_SUMMARY_PROMPT.format(
            current_summary=self._summary if self._summary else "(none yet — meeting just started)",
            new_transcript=new_text,
        )
        if _GLOSSARY or _EXTRA_PROMPT:
            prompt += "\nDomain glossary (use these exact spellings for all technical terms):\n"
            if _GLOSSARY:
                prompt += f"{_GLOSSARY}\n"
            if _EXTRA_PROMPT:
                prompt += f"{_EXTRA_PROMPT}\n"
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            raw = response.text.strip()
            # Strip accidental code fences
            raw = re.sub(r"^```(?:markdown|md)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            self._summary = raw.strip()
            self._buffer.clear()  # Only clear on success
        except Exception:
            pass  # Buffer preserved for next attempt

    async def flush(self):
        """Force update on any remaining buffer regardless of threshold."""
        if self._buffer:
            await self.update()

    def get_summary(self) -> str:
        return self._summary
