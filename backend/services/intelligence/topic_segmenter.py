from .rule_filter import FilterResult
from .context_manager import ContextManager


def _keyword_overlap(text: str, entities: list[str]) -> float:
    if not entities:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for e in entities if e.lower() in text_lower)
    return matches / len(entities)


class TopicSegmenter:
    """
    Detects topic shifts (after 3 consecutive chunks with < 20% overlap) by monitoring keyword overlap between incoming
    chunks and the current context entities.
    
    On shift: resets current_topic, active_issue, last_entities in ContextManager.
    open_tasks and decisions are preserved across topic shifts.
    """

    _SHIFT_THRESHOLD = 3
    _OVERLAP_MINIMUM = 0.20

    def __init__(self):
        self._off_topic_count = 0

    def check_and_segment(self, result: FilterResult, context_mgr: ContextManager) -> bool:
        text = result.chunk.text
        entities = context_mgr.context.last_entities

        overlap = _keyword_overlap(text, entities)

        if overlap >= self._OVERLAP_MINIMUM:
            self._off_topic_count = 0
            return False

        self._off_topic_count += 1

        if self._off_topic_count > self._SHIFT_THRESHOLD:
            context_mgr.context.current_topic = None
            context_mgr.context.active_issue = None
            context_mgr.context.last_entities = []
            self._off_topic_count = 0
            return True

        return False
