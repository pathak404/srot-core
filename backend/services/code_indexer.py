import asyncio
from pathlib import Path
from storage.db import update_index_job
from backend.services.code_parser import parse_project
from backend.services.graph_store import build_graph
from backend.services import entity_vector_index

_SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".next", "coverage"}
_SKIP_FILES = {"graphql.ts", "graphql.schema.ts"}


def _count_ts_files(root_path: str) -> int:
    root = Path(root_path)
    return sum(
        1
        for ext in ("*.ts", "*.tsx")
        for f in root.rglob(ext)
        if not any(part.startswith(".") or part in _SKIP_DIRS for part in f.parts) and f.name not in _SKIP_FILES
    )


async def run_indexing(job_id: int, root_path: str, project_name: str):

    def _save(status, progress, step, node_count=0, edge_count=0, error=None):
        update_index_job(
            job_id,
            status=status,
            progress=progress,
            current_step=step,
            node_count=node_count,
            edge_count=edge_count,
            error=error,
        )

    try:
        _save("running", 0, "Counting TypeScript files…")
        total_files = await asyncio.to_thread(_count_ts_files, root_path)
        _save("running", 5, f"Found {total_files} files, parsing…")

        # Tree-sitter parsing
        entities = await asyncio.to_thread(parse_project, root_path)
        fn_count   = sum(1 for e in entities if e["type"] == "function")
        enum_count = sum(1 for e in entities if e["type"] == "enum")
        svc_count  = sum(1 for e in entities if e["type"] in ("service", "controller", "entity"))
        _save("running", 40,
              f"Parsed {len(entities)} entities ({fn_count} functions, {enum_count} enums, "
              f"{svc_count} services), building graph…")

        # Neo4j
        node_count, edge_count = await asyncio.to_thread(build_graph, project_name, entities)
        _save("running", 55,
              f"Graph built ({node_count} nodes, {edge_count} edges), indexing vectors…",
              node_count=node_count, edge_count=edge_count)

        # Qdrant multi-vector entity index
        doc_count = 0
        if entity_vector_index.is_available():
            def on_chunk_done(done: int, total: int):
                pct = 55 + int((done / total) * 45) if total > 0 else 100
                _save("running", pct,
                      f"Indexing entity vectors: {done}/{total}",
                      node_count=node_count, edge_count=edge_count)

            doc_count = await asyncio.to_thread(
                entity_vector_index.index_entities, entities, project_name, on_chunk_done
            )
        else:
            _save("running", 100,
                  "Qdrant unavailable, skipping vector index",
                  node_count=node_count, edge_count=edge_count)

        _save("completed", 100,
              f"Done: {node_count} nodes, {edge_count} edges, {doc_count} chunks indexed",
              node_count=node_count, edge_count=edge_count)

    except Exception as e:
        update_index_job(job_id, status="failed", error=str(e)[:500])
