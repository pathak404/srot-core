import asyncio
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
from backend.services.transcription import SYSTEM_INSTRUCTION, GLOSSARY, EXTRA_PROMPT, refine_transcript

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
HINGLISH_MODEL = "gemini-2.5-flash-lite"

_HINGLISH_PROMPT = (
    "Convert to Hinglish. English words must stay exactly in English "
    "(e.g. इज→is, इट→it, पॉसिबल→possible, चेंज→change, स्टेटस→status, "
    "फाइव→five, फोर→four). Hindi words in Roman script. "
    "Output ONLY the converted text:\n{text}"
)


async def _to_hinglish_async(text: str) -> str:
    if not any("\u0900" <= c <= "\u097F" for c in text):
        return text
    try:
        resp = await client.aio.models.generate_content(
            model=HINGLISH_MODEL,
            contents=_HINGLISH_PROMPT.format(text=text),
        )
        return resp.text.strip() + " "
    except Exception:
        return text


def _build_live_config():
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
    )


class LiveTranscriptionSession:
    def __init__(self, meeting_id: int):
        self.meeting_id = meeting_id
        self.buffer = ""
        self._ctx = None
        self._running = False

    async def start(self):
        config = _build_live_config()
        self._session_mgr = client.aio.live.connect(model=LIVE_MODEL, config=config)
        self._ctx = await self._session_mgr.__aenter__()
        self._running = True

    async def send_audio(self, audio_bytes: bytes):
        if not self._running or not self._ctx:
            return
        blob = types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
        await self._ctx.send_realtime_input(audio=blob)

    async def receive_text(self):
        if not self._running or not self._ctx:
            return
        pending = ""
        async for response in self._ctx.receive():
            sc = response.server_content
            if not sc:
                continue
            if sc.input_transcription and sc.input_transcription.text:
                pending += sc.input_transcription.text
            if sc.turn_complete and pending:
                converted = await _to_hinglish_async(pending)
                pending = ""
                self.buffer += converted
                yield converted

    async def stop(self) -> str:
        self._running = False
        if self._ctx:
            try:
                await self._session_mgr.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx = None
        return self.buffer

    def get_buffer(self) -> str:
        return self.buffer
