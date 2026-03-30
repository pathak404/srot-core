
import os
from pydantic import Field
from llama_index.core.embeddings import BaseEmbedding

_qdrant_client = None


class _GeminiEmbedding(BaseEmbedding):
    """LlamaIndex-compatible embedding backed by google-genai SDK (v1 API)."""

    model_name: str = Field(default="models/gemini-embedding-001")

    def _embed(self, text: str) -> list[float]:
        from google import genai as _genai
        client = _genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        result = client.models.embed_content(model=self.model_name, contents=text)
        return list(result.embeddings[0].values)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)


def _get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY") or None
        _qdrant_client = QdrantClient(url=url, api_key=api_key)
    return _qdrant_client


def is_available() -> bool:
    try:
        client = _get_qdrant_client()
        client.get_collections()
        return True
    except Exception:
        return False


def _get_embed_model() -> _GeminiEmbedding:
    return _GeminiEmbedding(model_name="models/gemini-embedding-001")


def _collection_name(project_name: str) -> str:
    # Qdrant collection names must be alphanumeric + underscore
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in project_name)
    return f"code_{safe}"


def index_project(
    entities: list[dict],
    project_name: str,
    progress_callback=None,  # callable(done: int, total: int)
) -> int:
    """
    Embed each entity's source_chunk with Gemini and store in Qdrant.
    One vector per function / class / enum chunk - not per file.
    Calls progress_callback(done, total) after each chunk.
    Returns the number of documents indexed.
    """
    from llama_index.core import VectorStoreIndex, Document, StorageContext, Settings
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    documents = []
    for e in entities:
        chunk = e.get("source_chunk", "")
        if not chunk or len(chunk.strip()) < 20:
            continue
        # Stable doc_id so re-indexing replaces rather than duplicates
        doc_id = f"{project_name}::{e.get('file_path', '')}::{e.get('name', '')}::{e['type']}"
        documents.append(Document(
            text=chunk,
            metadata={
                "function_name": e.get("name", ""),
                "class_name": e.get("class_name") or "",
                "file_path": e.get("file_path", ""),
                "project": project_name,
                "chunk_type": e["type"],
            },
            doc_id=doc_id,
        ))

    if not documents:
        return 0

    Settings.embed_model = _get_embed_model()

    collection = _collection_name(project_name)
    vector_store = QdrantVectorStore(client=_get_qdrant_client(), collection_name=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Insert one by one so we can report granular progress
    index = VectorStoreIndex([], storage_context=storage_context)
    total = len(documents)
    for i, doc in enumerate(documents):
        index.insert(doc)
        if progress_callback:
            progress_callback(i + 1, total)

    return total


def search(query: str, project_name: str | None = None, top_k: int = 5) -> list[str]:
    """
    Semantic search over indexed code. Returns list of matching code snippets.
    Each snippet is prefixed with [function_name in file_path].
    """
    from llama_index.core import VectorStoreIndex, StorageContext, Settings
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    Settings.embed_model = _get_embed_model()

    try:
        client = _get_qdrant_client()
        if project_name:
            collections = [_collection_name(project_name)]
        else:
            all_cols = client.get_collections().collections
            collections = [c.name for c in all_cols if c.name.startswith("code_")]

        snippets = []
        for collection in collections:
            try:
                vector_store = QdrantVectorStore(client=client, collection_name=collection)
                storage_context = StorageContext.from_defaults(vector_store=vector_store)
                index = VectorStoreIndex.from_vector_store(
                    vector_store=vector_store,
                    storage_context=storage_context,
                )
                retriever = index.as_retriever(similarity_top_k=top_k)
                nodes = retriever.retrieve(query)
                for n in nodes:
                    meta = n.metadata or {}
                    fn_name = meta.get("function_name", "")
                    label = f"[{fn_name}]" if fn_name else ""
                    content = n.get_content()[:500]
                    snippets.append(f"{label}\n{content}" if label else content)
            except Exception:
                continue

        return snippets[:top_k]
    except Exception:
        return []
