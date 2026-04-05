from dataclasses import dataclass
from typing import Literal

from .deepgram_asr import DeepgramChunk

ConfidenceTier = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class AnnotatedChunk:
    text: str
    confidence: float
    start: float
    end: float
    confidence_tier: ConfidenceTier


# HIGH   - confidence >= 0.90
# MEDIUM - 0.75 <= confidence < 0.90
# LOW    - confidence < 0.75

class ConfidenceAnalyzer:

    def analyze(self, chunk: DeepgramChunk) -> AnnotatedChunk:
        if chunk.confidence >= 0.90:
            tier: ConfidenceTier = "HIGH"
        elif chunk.confidence >= 0.75:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        return AnnotatedChunk(
            text=chunk.text,
            confidence=chunk.confidence,
            start=chunk.start,
            end=chunk.end,
            confidence_tier=tier,
        )
