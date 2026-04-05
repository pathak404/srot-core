import re
import uuid
from dataclasses import dataclass

from .confidence_analyzer import AnnotatedChunk

_FILLER_PATTERNS = [
    r"\byou know\b",
    r"\bokay so\b",
    r"\bright so\b",
    r"\bi mean\b",
    r"\bkind of\b",
    r"\bsort of\b",
    r"\buh\b",
    r"\bum\b",
    r"\bhmm\b",
    r"\b like \b",
]

_FILLER_RE = re.compile("|".join(_FILLER_PATTERNS), flags=re.IGNORECASE)


@dataclass
class ProcessedChunk:
    chunk_id: str
    text: str            # normalized (lowercase, fillers removed)
    raw_text: str        # original text before normalization
    time_window: dict    # {"start": float, "end": float}
    confidence: float
    confidence_tier: str


class ChunkProcessor:
    """
    Converts an AnnotatedChunk into a ProcessedChunk by:
      - Removing filler words
      - Lowercasing
      - Collapsing whitespace
      - Adding chunk_id (UUID) and time_window metadata
    """

    def process(self, chunk: AnnotatedChunk) -> ProcessedChunk:
        raw = chunk.text
        normalized = _FILLER_RE.sub(" ", raw)
        normalized = normalized.lower()
        normalized = re.sub(r"\s{2,}", " ", normalized).strip()

        return ProcessedChunk(
            chunk_id=str(uuid.uuid4()),
            text=normalized,
            raw_text=raw,
            time_window={"start": chunk.start, "end": chunk.end},
            confidence=chunk.confidence,
            confidence_tier=chunk.confidence_tier,
        )
