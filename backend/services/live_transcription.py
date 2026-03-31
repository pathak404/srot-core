import asyncio
import os
from typing import AsyncGenerator, Optional

from deepgram import AsyncDeepgramClient
from deepgram.listen import ListenV1Results
from dotenv import load_dotenv

load_dotenv()


class LiveTranscriptionSession:
    def __init__(self, meeting_id: int):
        self.meeting_id = meeting_id
        self.buffer = ""
        self._client = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY", ""))
        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        # Each item: {"text": str, "is_final": bool} | {"error": str} | None (done sentinel)
        self._transcript_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        self._running = False
        self._stream_task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def _stream_loop(self):
        async def audio_source():
            while self._running:
                data = await self._audio_queue.get()
                if data is None:
                    return
                yield data

        try:
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
                    async for audio_bytes in audio_source():
                        await connection.send_media(audio_bytes)
                    await connection.send_close_stream()

                sender = asyncio.create_task(send_loop())
                try:
                    async for message in connection:
                        if not isinstance(message, ListenV1Results):
                            continue
                        alt = message.channel.alternatives[0]
                        text = alt.transcript.strip()
                        if not text:
                            continue
                        is_final = bool(message.is_final)
                        await self._transcript_queue.put({"text": text, "is_final": is_final})
                finally:
                    await sender
        except Exception as e:
            await self._transcript_queue.put({"error": str(e) or "Deepgram connection failed"})
        finally:
            await self._transcript_queue.put(None)  # signal done

    async def send_audio(self, audio_bytes: bytes):
        if self._running:
            await self._audio_queue.put(audio_bytes)

    async def receive_text(self) -> AsyncGenerator[dict, None]:
        """Yields {"text": str, "is_final": bool} or {"error": str}. None signals end."""
        while True:
            try:
                item = await asyncio.wait_for(self._transcript_queue.get(), timeout=0.5)
                if item is None:
                    return
                if item.get("is_final"):
                    self.buffer += item["text"] + " "
                yield item
            except asyncio.TimeoutError:
                if not self._running:
                    return
                continue

    async def stop(self) -> str:
        self._running = False
        await self._audio_queue.put(None)  # unblock audio_source
        if self._stream_task:
            try:
                await asyncio.wait_for(self._stream_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._stream_task.cancel()
        return self.buffer

    def get_buffer(self) -> str:
        return self.buffer
