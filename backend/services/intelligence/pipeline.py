import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

from .deepgram_asr import DeepgramASR, DeepgramChunk
from .confidence_analyzer import ConfidenceAnalyzer
from .chunk_processor import ChunkProcessor
from .rule_filter import RuleFilter
from .context_manager import ContextManager
from .topic_segmenter import TopicSegmenter
from .context_resolver import ContextResolver
from .llm_trigger import LLMTrigger
from .llm_processor import GeminiLLM
from .jira_state_builder import JiraStateBuilder, JiraState
from .periodic_summarizer import PeriodicSummarizer
from backend.services.knowledge_retriever import get_jira_context


@dataclass
class PipelineOutput:
    transcript_delta: str    # raw text from this chunk (for live display)
    jira_state: JiraState    # current full ticket + decision list
    context_snapshot: dict   # current ContextManager snapshot


class Pipeline:

    _LLM_QUEUE_MAX = 50

    def __init__(self, meeting_id: int):
        self.meeting_id = meeting_id
        self._asr = DeepgramASR()
        self._confidence = ConfidenceAnalyzer()
        self._chunker = ChunkProcessor()
        self._filter = RuleFilter()
        self._context = ContextManager()
        self._segmenter = TopicSegmenter()
        self._resolver = ContextResolver()
        self._trigger = LLMTrigger()
        self._llm = GeminiLLM()
        self._jira_builder = JiraStateBuilder()
        self._summarizer = PeriodicSummarizer()

        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._llm_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._LLM_QUEUE_MAX)
        self._running = False
        self._llm_worker_task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._llm_worker_task = asyncio.create_task(self._llm_worker())

    async def feed_audio(self, audio_bytes: bytes):
        await self._audio_queue.put(audio_bytes)

    async def run_streaming(self) -> AsyncGenerator[PipelineOutput, None]:
        async def audio_source():
            while self._running:
                data = await self._audio_queue.get()
                if data is None:
                    return
                yield data

        async for dg_chunk in self._asr.stream(audio_source()):
            output = await self._process_chunk(dg_chunk)
            if output:
                yield output

    async def process_file(self, file_path: str) -> tuple[str, JiraState]:
        """
        Batch mode: transcribe file with Deepgram, run all chunks through pipeline.
        Returns (full_transcript, JiraState). Transcribes once.
        """
        chunks = await self._asr.transcribe_file(file_path)
        transcript = " ".join(c.text for c in chunks)
        for chunk in chunks:
            await self._process_chunk(chunk)
        await self._drain_llm_queue()
        return transcript, self._jira_builder.get_state()

    async def stop(self) -> JiraState:
        self._running = False
        await self._audio_queue.put(None)
        if self._llm_worker_task:
            try:
                await asyncio.wait_for(self._drain_llm_queue(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            self._llm_worker_task.cancel()
            try:
                await self._llm_worker_task
            except asyncio.CancelledError:
                pass
        return self._jira_builder.get_state()

    async def _process_chunk(self, dg_chunk: DeepgramChunk) -> Optional[PipelineOutput]:
        annotated = self._confidence.analyze(dg_chunk)
        processed = self._chunker.process(annotated)
        filter_result = self._filter.filter(processed)

        transcript_delta = dg_chunk.text + " "

        if filter_result.type == "noise":
            return PipelineOutput(
                transcript_delta=transcript_delta,
                jira_state=self._jira_builder.get_state(),
                context_snapshot={},
            )

        self._context.update(filter_result)
        self._segmenter.check_and_segment(filter_result, self._context)
        resolved_text = self._resolver.resolve(processed.text, self._context.get_snapshot())

        if self._trigger.should_call(filter_result, resolved_text, self._context.get_snapshot()):
            llm_input = {
                "context": self._context.get_snapshot(),
                "chunk": resolved_text,
            }
            if not self._llm_queue.full():
                await self._llm_queue.put(llm_input)

        chunk_duration = processed.time_window["end"] - processed.time_window["start"]
        await self._summarizer.tick(chunk_duration, self._context)

        return PipelineOutput(
            transcript_delta=transcript_delta,
            jira_state=self._jira_builder.get_state(),
            context_snapshot=self._context.get_snapshot(),
        )

    async def _llm_worker(self):
        while self._running or not self._llm_queue.empty():
            try:
                llm_input = await asyncio.wait_for(self._llm_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                code_context = await asyncio.to_thread(
                    get_jira_context,
                    [{"title": llm_input.get("chunk", ""), "description": ""}],
                    None,
                )
                if code_context:
                    llm_input["code_context"] = code_context
                result = await self._llm.process(llm_input)
                if result.get("task"):
                    self._jira_builder.update(result, self._context.get_snapshot())
            except Exception:
                pass

    async def _drain_llm_queue(self):
        while not self._llm_queue.empty():
            await asyncio.sleep(0.1)
