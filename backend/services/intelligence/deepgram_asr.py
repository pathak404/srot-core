import asyncio
import os
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

from deepgram import AsyncDeepgramClient
from deepgram.listen import ListenV1Results
from dotenv import load_dotenv

load_dotenv()


@dataclass
class DeepgramChunk:
    text: str
    confidence: float
    start: float    # seconds from audio start
    end: float      # seconds from audio start
    is_final: bool


class DeepgramASR:
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("DEEPGRAM_API_KEY", "")
        self._client = AsyncDeepgramClient(api_key=self._api_key)

    async def stream(
        self,
        audio_source: AsyncGenerator[bytes, None],
    ) -> AsyncGenerator[DeepgramChunk, None]:
        """
        Consumes an async generator of raw PCM bytes (16kHz, 16-bit mono).
        Yields DeepgramChunk for each final (is_final=True) transcript event
        immediately as each message arrives.
        Interim results are discarded, only speech_final events flow downstream.
        """
        async with self._client.listen.v1.connect(
            model="nova-2",
            language="en-US",
            smart_format="true",
            interim_results="true",
            punctuate="true",
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        ) as connection:

            async def send_loop():
                async for audio_bytes in audio_source:
                    await connection.send_media(audio_bytes)
                await connection.send_close_stream()

            sender = asyncio.create_task(send_loop())

            try:
                async for message in connection:
                    if not isinstance(message, ListenV1Results):
                        continue
                    if not message.is_final:
                        continue
                    alt = message.channel.alternatives[0]
                    transcript = alt.transcript.strip()
                    if not transcript:
                        continue
                    words = alt.words or []
                    if words:
                        conf = sum(w.confidence for w in words) / len(words)
                        start = words[0].start
                        end = words[-1].end
                    else:
                        conf = float(getattr(alt, "confidence", 0.9))
                        start = message.start
                        end = message.start + message.duration
                    yield DeepgramChunk(
                        text=transcript,
                        confidence=conf,
                        start=start,
                        end=end,
                        is_final=True,
                    )
            finally:
                await sender

    async def transcribe_file(self, file_path: str) -> list[DeepgramChunk]:
        """
        Transcribes an audio file using Deepgram prerecorded API.
        Returns a list of DeepgramChunks split into ~15-second windows.
        """
        with open(file_path, "rb") as f:
            audio_bytes = f.read()

        response = await self._client.listen.v1.media.transcribe_file(
            request=audio_bytes,
            model="nova-2",
            language="en-US",
            smart_format=True,
            punctuate=True,
        )

        channel = response.results.channels[0]
        alt = channel.alternatives[0]
        words = alt.words or []

        if not words:
            full_text = alt.transcript.strip()
            conf = float(getattr(alt, "confidence", 0.9))
            return [DeepgramChunk(text=full_text, confidence=conf, start=0.0, end=0.0, is_final=True)] if full_text else []

        # Split into ~15-second chunks by word timestamps
        chunks: list[DeepgramChunk] = []
        current_words = []
        chunk_start = words[0].start

        for word in words:
            current_words.append(word)
            if word.end - chunk_start >= 15.0:
                text = " ".join(
                    getattr(w, "punctuated_word", None) or w.word
                    for w in current_words
                )
                conf = sum(w.confidence for w in current_words) / len(current_words)
                chunks.append(DeepgramChunk(
                    text=text,
                    confidence=conf,
                    start=chunk_start,
                    end=word.end,
                    is_final=True,
                ))
                current_words = []
                chunk_start = word.end

        # Remaining words
        if current_words:
            text = " ".join(
                getattr(w, "punctuated_word", None) or w.word
                for w in current_words
            )
            conf = sum(w.confidence for w in current_words) / len(current_words)
            chunks.append(DeepgramChunk(
                text=text,
                confidence=conf,
                start=chunk_start,
                end=current_words[-1].end,
                is_final=True,
            ))

        return chunks
