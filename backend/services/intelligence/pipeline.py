import asyncio
import time
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
from .meeting_summarizer import MeetingSummarizer
from backend.services.knowledge_retriever import build_llm_domain_pack, get_jira_context
from storage.db import get_latest_completed_project


@dataclass
class PipelineOutput:
    transcript_delta: str    # raw text from this chunk (for live display)
    jira_state: JiraState    # current full ticket + decision list
    context_snapshot: dict   # current ContextManager snapshot
    summary_md: str = ""     # current cumulative Markdown summary
    is_final: bool = True    # False for interim chunks, True for final


class Pipeline:

    _LLM_QUEUE_MAX = 50
    _CTX_CACHE_TTL = 300         # seconds

    def __init__(self, meeting_id: int, project_name: str | None = None):
        self.meeting_id = meeting_id
        self._project_name = project_name or get_latest_completed_project()
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
        self._summarizer_md = MeetingSummarizer()

        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._llm_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._LLM_QUEUE_MAX)
        self._running = False
        self._llm_worker_task: Optional[asyncio.Task] = None
        self._summary_worker_task: Optional[asyncio.Task] = None
        self._ctx_cache: dict = {}   # key -> (code_context_str, timestamp)
        self._transcript_buffer: list[str] = []

    async def start(self):
        self._running = True
        self._llm_worker_task = asyncio.create_task(self._llm_worker())
        self._summary_worker_task = asyncio.create_task(self._summary_worker())

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

    async def process_file(self, file_path: str) -> tuple[str, JiraState, str]:
        """
        Batch mode: transcribe file with Deepgram, run all chunks through pipeline.
        Returns (full_transcript, JiraState, summary_md).
        """
        chunks = await self._asr.transcribe_file(file_path)
        transcript = " ".join(c.text for c in chunks)
        for chunk in chunks:
            await self._process_chunk(chunk)
        await self._drain_llm_queue()
        await self._summarizer_md.flush()
        return transcript, self._jira_builder.get_state(), self._summarizer_md.get_summary()

    def get_summary(self) -> str:
        return self._summarizer_md.get_summary()

    def get_transcript_text(self) -> str:
        return " ".join(self._transcript_buffer)

    async def stop(self) -> JiraState:
        self._running = False
        await self._audio_queue.put(None)
        # Signal LLM worker to finish
        await self._llm_queue.put({"control": "stop"})

        if self._summary_worker_task:
            self._summary_worker_task.cancel()
            try:
                await self._summary_worker_task
            except asyncio.CancelledError:
                pass
        await self._summarizer_md.flush()

        if self._llm_worker_task:
            try:
                # Wait for worker to finish processing remaining items
                await asyncio.wait_for(self._llm_worker_task, timeout=15.0)
            except asyncio.TimeoutError:
                _log.warning("LLM worker did not stop gracefully, cancelling")
                self._llm_worker_task.cancel()
            except Exception as e:
                _log.error(f"Error while waiting for LLM worker: {e}")

        return self._jira_builder.get_state()

    async def _process_chunk(self, dg_chunk: DeepgramChunk) -> Optional[PipelineOutput]:
        if not dg_chunk.is_final:
            # Interim: skip all stages, just forward text for display
            return PipelineOutput(
                transcript_delta=dg_chunk.text,
                jira_state=self._jira_builder.get_state(),
                context_snapshot={},
                summary_md=self._summarizer_md.get_summary(),
                is_final=False,
            )
        
        self._transcript_buffer.append(dg_chunk.text)
        annotated = self._confidence.analyze(dg_chunk)
        processed = self._chunker.process(annotated)
        
        filter_result = self._filter.filter(processed)

        transcript_delta = dg_chunk.text + " "
        self._summarizer_md.add_chunk(processed.chunk_id, dg_chunk.text) 

        if filter_result.type == "noise":
            return PipelineOutput(
                transcript_delta=transcript_delta,
                jira_state=self._jira_builder.get_state(),
                context_snapshot={},
                summary_md=self._summarizer_md.get_summary(),
            )

        self._context.update(filter_result)
        self._segmenter.check_and_segment(filter_result, self._context)
        resolved_text = self._resolver.resolve(processed.text, self._context.get_snapshot())

        if await self._trigger.should_call(filter_result, resolved_text, self._context.get_snapshot()):
            llm_input = {
                "context": self._context.get_snapshot(),
                "chunk": resolved_text,
                "chunk_id": processed.chunk_id,
            }
            if not self._llm_queue.full():
                await self._llm_queue.put(llm_input)

        chunk_duration = processed.time_window["end"] - processed.time_window["start"]
        await self._summarizer.tick(chunk_duration, self._context)

        return PipelineOutput(
            transcript_delta=transcript_delta,
            jira_state=self._jira_builder.get_state(),
            context_snapshot=self._context.get_snapshot(),
            summary_md=self._summarizer_md.get_summary(),
        )

    async def _llm_worker(self):
        while True:
            try:
                llm_input = await self._llm_queue.get()
                if llm_input.get("control") == "stop":
                    break
            except Exception:
                break

            try:
                chunk_text = llm_input.get("chunk", "")
                chunk_id = llm_input.get("chunk_id")
                cache_key = " ".join(chunk_text.lower().split()[:6])
                cached = self._ctx_cache.get(cache_key)
                if cached and (time.time() - cached[1]) < self._CTX_CACHE_TTL:
                    code_context = cached[0]
                    pack = await asyncio.to_thread(
                        build_llm_domain_pack, self._project_name
                    )
                else:
                    code_context, pack = await asyncio.gather(
                        asyncio.to_thread(
                            get_jira_context,
                            [{"title": chunk_text, "description": ""}],
                            self._project_name,
                        ),
                        asyncio.to_thread(build_llm_domain_pack, self._project_name),
                    )
                    self._ctx_cache[cache_key] = (code_context, time.time())
                if code_context:
                    llm_input["code_context"] = code_context
                llm_input["project_name"] = self._project_name
                llm_input["domain_terms"] = pack.get("domain_terms", "")
                llm_input["entity_list"] = pack.get("entity_list", "")
                llm_input["confusion_map"] = pack.get("confusion_map", "(none)")
                
                result = await self._llm.process(llm_input)
                
                # Hybrid Summary: Update the summarizer with corrected text
                if chunk_id and result.get("corrected_text"):
                    self._summarizer_md.update_chunk(chunk_id, result["corrected_text"])

                cl = result.get("clarification")
                if cl:
                    merged = dict(cl)
                    ps = result.get("pipeline_status")
                    if ps is not None:
                        merged["pipeline_status"] = ps
                    self._jira_builder.add_clarification(merged)
                if result.get("task"):
                    self._jira_builder.update(result, self._context.get_snapshot())
            except Exception as exc:
                self._jira_builder.add_clarification(
                    {
                        "status": "PIPELINE_ERROR",
                        "message": str(exc)[:500],
                        "candidates": [],
                        "pipeline_status": "ERROR",
                    }
                )
            finally:
                self._llm_queue.task_done()

    async def _summary_worker(self):
        while self._running:
            await asyncio.sleep(30)
            if self._summarizer_md.should_update():
                await self._summarizer_md.update()

    async def _drain_llm_queue(self):
        await self._llm_queue.join()
