"""
Multi-vector Qdrant indexing for codebase entities.

Collection: codebase_entities
- name_vector: 384-dim (Gemini embedding on normalized symbol name)
- semantic_vector: 768-dim (Gemini embedding on description + code)

Hybrid retrieval uses metadata filters plus the weighted scoring formula.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from typing import Callable, Optional

_log = logging.getLogger(__name__)

from google import genai
from google.genai import types as genai_types
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from backend.services import graph_store
from backend.services.graph_store import _infer_service_for_entity 

CODEBASE_ENTITIES_COLLECTION = "codebase_entities"
NAME_VECTOR_DIM = 384
SEMANTIC_VECTOR_DIM = 768

_EMBED_MODEL = "models/gemini-embedding-001"
_NS_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Parser types that are persisted as searchable entities
_INDEX_TYPES = frozenset({
    "enum",
    "service",
    "controller",
    "entity",
    "resolver",
    "module",
    "function",
    "graphql_type",
    "graphql_input",
    "graphql_interface",
    "graphql_args_type",
    "graphql_field",
    "interface",
    "entity_column",
    "endpoint",
})

_ACTION_EXPECTED_TYPES: dict[str, list[str]] = {
    "ADD_ENUM_VALUE": ["enum"],
    "UPDATE_ENUM": ["enum"],
    "CREATE_FIELD": ["entity_column", "graphql_field"],
    "UPDATE_LOGIC": ["function", "service", "resolver"],
    "UNKNOWN": [],
}

# FINAL_SCORE
_P_SEM = 0.30
_P_NV = 0.25
_P_NAME_STR = 0.15
_P_MOD = 0.10
_P_TYPE = 0.10
_P_REL = 0.10

# normalized manual fusion
_FUSION_SEM = 0.7
_FUSION_NAME_VEC = 0.3

_MODULE_MATCH = 1.0
_MODULE_MISMATCH = 0.1
_MODULE_NEUTRAL = 0.5

_PHASE1_TOP = 50

_EMBED_CACHE: dict[tuple[str, int], tuple[float, list[float]]] = {}
_EMBED_CACHE_MAX = 10000
_EMBED_TTL_SEC = 3600

_QCACHE: dict[str, tuple[float, list[dict]]] = {}
_QCACHE_TTL_SEC = 420

_qdrant_client: QdrantClient | None = None
_genai_client: genai.Client | None = None


def _get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        api_key = os.getenv("QDRANT_API_KEY") or None
        _qdrant_client = QdrantClient(url=url, api_key=api_key)
    return _qdrant_client


def _get_genai() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    return _genai_client


def _embed(text: str, output_dim: int) -> list[float]:
    text = (text or "").strip()[:8000]
    if not text:
        text = " "
    now = time.time()
    key = (text, output_dim)
    hit = _EMBED_CACHE.get(key)
    if hit and (now - hit[0]) < _EMBED_TTL_SEC:
        return list(hit[1])
    r = _get_genai().models.embed_content(
        model=_EMBED_MODEL,
        contents=text,
        config=genai_types.EmbedContentConfig(output_dimensionality=output_dim),
    )
    vec = list(r.embeddings[0].values)
    _EMBED_CACHE[key] = (now, vec)
    if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
        for k, (ts, _) in list(_EMBED_CACHE.items())[: max(500, _EMBED_CACHE_MAX // 5)]:
            if now - ts > _EMBED_TTL_SEC:
                del _EMBED_CACHE[k]
    return vec


def is_available() -> bool:
    try:
        _get_qdrant().get_collections()
        return True
    except Exception:
        return False


def ensure_collection() -> None:
    client = _get_qdrant()
    names = {c.name for c in client.get_collections().collections}
    if CODEBASE_ENTITIES_COLLECTION in names:
        return
    client.create_collection(
        collection_name=CODEBASE_ENTITIES_COLLECTION,
        vectors_config={
            "name_vector": qm.VectorParams(size=NAME_VECTOR_DIM, distance=qm.Distance.COSINE),
            "semantic_vector": qm.VectorParams(size=SEMANTIC_VECTOR_DIM, distance=qm.Distance.COSINE),
        },
    )


def _normalize_name(name: str) -> str:
    return " ".join((name or "").replace("_", " ").lower().split())


def normalize_cosine(score: float) -> float:
    return max(0.0, min(1.0, (float(score) - 0.5) / 0.5))


def _normalize_score(score: float, min_s: float, max_s: float) -> float:
    return (score - min_s) / (max_s - min_s + 1e-6)


def _fused_vector_normalized(semantic_score: float, name_score: float) -> float:
    sem_n = max(0.0, min(1.0, _normalize_score(semantic_score, 0.5, 1.0)))
    name_n = max(0.0, min(1.0, _normalize_score(name_score, 0.0, 1.0)))
    return _FUSION_SEM * sem_n + _FUSION_NAME_VEC * name_n


def _token_overlap_query_entity(query_entity: str, payload: dict) -> float:
    q_tokens = set(re.findall(r"[a-zA-Z0-9_]+", (query_entity or "").lower()))
    if not q_tokens:
        return 0.0
    en = f"{payload.get('name', '')} {payload.get('normalized_name', '')}"
    e_tokens = set(re.findall(r"[a-zA-Z0-9_]+", en.lower()))
    inter = len(q_tokens & e_tokens)
    return inter / len(q_tokens)


def _name_string_score(query_entity: str, payload: dict) -> float:
    q = _normalize_name(query_entity)
    if not q:
        return 0.2
    name = (payload.get("name") or "").lower()
    nn = (payload.get("normalized_name") or "").strip().lower()
    pn = _normalize_name(payload.get("name") or "")
    if q == name or q == nn or q == pn:
        return 1.0
    if _token_overlap_query_entity(query_entity, payload) > 0.7:
        return 0.8
    if q in name or name in q or q in nn:
        return 0.6
    return 0.2


def _infer_module(file_path: str) -> str:
    parts = [p for p in (file_path or "").replace("\\", "/").split("/") if p]
    try:
        i = parts.index("src")
        if i + 1 < len(parts):
            return parts[i + 1]
    except ValueError:
        pass
    return parts[0] if parts else "root"


def _build_service_maps(entities: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    from pathlib import Path

    file_services: dict[str, list[str]] = {}
    dir_services: dict[str, list[str]] = {}
    for e in entities:
        et = e.get("type", "")
        if et not in ("service", "controller", "entity", "resolver", "module"):
            continue
        name = e.get("name", "")
        fp = e.get("file_path", "")
        if not name or not fp:
            continue
        file_services.setdefault(fp, []).append(name)
        dir_services.setdefault(str(Path(fp).parent), []).append(name)
    return file_services, dir_services


def _relations_for(
    e: dict,
    file_services: dict[str, list[str]],
    dir_services: dict[str, list[str]],
) -> list[str]:
    out: list[str] = []
    et = e.get("type", "")
    fp = e.get("file_path", "")
    if et == "function" and e.get("class_name"):
        out.append(f"{e['class_name']}.{e['name']}")
    if et == "enum":
        svc = _infer_service_for_entity(fp, file_services, dir_services)
        if svc:
            out.append(svc)
    return out


def _semantic_source_text(e: dict) -> str:
    name = e.get("name", "")
    chunk = (e.get("source_chunk") or "").strip()
    et = e.get("type", "")
    bits = [f"{et} {name}".strip()]
    if chunk:
        bits.append(chunk[:1200])
    return "\n".join(bits)


def _stable_point_id(project_name: str, e: dict) -> str:
    key = f"{project_name}|{e.get('file_path', '')}|{e.get('name', '')}|{e.get('type', '')}"
    return str(uuid.uuid5(_NS_UUID, key))


def index_entities(
    entities: list[dict],
    project_name: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> int:
    """
    Upsert parsed entities into codebase_entities. Returns count indexed.
    """
    ensure_collection()
    file_services, dir_services = _build_service_maps(entities)
    batch: list[qm.PointStruct] = []
    indexed = 0
    candidates: list[dict] = []
    for e in entities:
        if e.get("type") not in _INDEX_TYPES:
            continue
        chunk = (e.get("source_chunk") or "").strip()
        short_ok = ("enum", "entity_column", "endpoint", "graphql_field")
        if len(chunk) < 12 and e.get("type") not in short_ok:
            continue
        candidates.append(e)

    total = len(candidates)
    client = _get_qdrant()

    for i, e in enumerate(candidates):
        name = e.get("name", "") or "unknown"
        norm = _normalize_name(name)
        sem_text = _semantic_source_text(e)
        try:
            nv = _embed(norm, NAME_VECTOR_DIM)
            sv = _embed(sem_text, SEMANTIC_VECTOR_DIM)
        except Exception:
            continue

        fp = e.get("file_path", "")
        et = e.get("type", "")
        svc = e.get("service") or e.get("class_name") or ""
        if not svc and et == "function":
            svc = e.get("class_name") or ""
        if not svc:
            inf = _infer_service_for_entity(fp, file_services, dir_services)
            svc = inf or ""

        src_chunk = (e.get("source_chunk") or "").strip()
        payload = {
            "id": _stable_point_id(project_name, e),
            "name": name,
            "normalized_name": norm,
            "type": et,
            "language": "typescript",
            "file_path": fp,
            "module": _infer_module(fp),
            "service": svc,
            "description": (sem_text[:240] + "…") if len(sem_text) > 240 else sem_text,
            "code_snippet": src_chunk[:2000],
            "relations": _relations_for(e, file_services, dir_services),
            "project": project_name,
        }

        batch.append(
            qm.PointStruct(
                id=_stable_point_id(project_name, e),
                vector={"name_vector": nv, "semantic_vector": sv},
                payload=payload,
            )
        )
        indexed += 1

        if len(batch) >= 24:
            client.upsert(collection_name=CODEBASE_ENTITIES_COLLECTION, points=batch)
            batch.clear()
        if progress_callback:
            progress_callback(i + 1, total)

    if batch:
        client.upsert(collection_name=CODEBASE_ENTITIES_COLLECTION, points=batch)

    return indexed


def _module_score(inferred_module: str | None, payload: dict) -> float:
    if not inferred_module:
        return _MODULE_NEUTRAL
    m = (payload.get("module") or "").lower()
    return _MODULE_MATCH if m == inferred_module.strip().lower() else _MODULE_MISMATCH


def _type_score(action: str, payload_type: str) -> float:
    expected = _ACTION_EXPECTED_TYPES.get(action, [])
    if not expected:
        return 0.3
    if payload_type in expected:
        return 1.0
    if action == "CREATE_FIELD" and payload_type in ("entity_column", "graphql_field"):
        return 1.0
    return 0.3


def _clamp_unit_score(score: float | None) -> float:
    if score is None:
        return 0.0
    s = float(score)
    if s > 1.0:
        return max(0.0, min(1.0, s))
    return max(0.0, s)


def _production_final_score(
    sem_norm: float,
    nv_norm: float,
    query_entity: str,
    action: str,
    inferred_module: str | None,
    payload: dict,
    relation_score: float,
) -> float:
    ns = _name_string_score(query_entity, payload)
    ms = _module_score(inferred_module, payload)
    ts = _type_score(action, payload.get("type") or "")
    return max(
        0.0,
        min(
            1.0,
            _P_SEM * sem_norm
            + _P_NV * nv_norm
            + _P_NAME_STR * ns
            + _P_MOD * ms
            + _P_TYPE * ts
            + _P_REL * relation_score,
        ),
    )


def _prefetch_fusion_search(
    client: QdrantClient,
    q_sem: list[float],
    q_name: list[float],
    flt: qm.Filter | None,
    limit: int,
) -> tuple[str, list[dict]] | None:
    """
    Single Qdrant round-trip: prefetch both vectors + DBSF or weighted RRF.
    Returns (mode, rows) where each row is {payload, fusion_score}; mode is 'dbsf'|'rrf'.
    """
    prefetch_limit = max(limit * 2, 80)
    try:
        res = client.query_points(
            collection_name=CODEBASE_ENTITIES_COLLECTION,
            prefetch=[
                qm.Prefetch(query=q_sem, using="semantic_vector", limit=prefetch_limit, filter=flt),
                qm.Prefetch(query=q_name, using="name_vector", limit=prefetch_limit, filter=flt),
            ],
            query=qm.FusionQuery(fusion="dbsf"),
            limit=limit,
            with_payload=True,
        )
        rows = []
        for p in res.points or []:
            pl = p.payload or {}
            if not isinstance(pl, dict):
                continue
            rows.append({"payload": pl, "fusion_score": _clamp_unit_score(p.score)})
        return ("dbsf", rows) if rows else None
    except Exception as e:
        _log.debug("Qdrant DBSF fusion failed: %s", e)
    try:
        res = client.query_points(
            collection_name=CODEBASE_ENTITIES_COLLECTION,
            prefetch=[
                qm.Prefetch(query=q_sem, using="semantic_vector", limit=prefetch_limit, filter=flt),
                qm.Prefetch(query=q_name, using="name_vector", limit=prefetch_limit, filter=flt),
            ],
            query=qm.RrfQuery(rrf=qm.Rrf(weights=[0.7, 0.3])),
            limit=limit,
            with_payload=True,
        )
        rows = []
        for p in res.points or []:
            pl = p.payload or {}
            if not isinstance(pl, dict):
                continue
            rows.append({"payload": pl, "fusion_score": _clamp_unit_score(p.score)})
        return ("rrf", rows) if rows else None
    except Exception as e:
        _log.debug("Qdrant RRF fusion failed: %s", e)
    return None


def _merge_dual_vector_hits(
    client: QdrantClient,
    query_semantic: list[float],
    query_name: list[float],
    flt: qm.Filter | None,
    limit: int,
) -> dict[str, dict]:
    """Point id -> payload, semantic_score, name_score (cosine similarity 0-1)."""
    merged: dict[str, dict] = {}
    try:
        res_sem = client.query_points(
            collection_name=CODEBASE_ENTITIES_COLLECTION,
            query=query_semantic,
            using="semantic_vector",
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        for p in res_sem.points or []:
            pid = str(p.id)
            pl = p.payload or {}
            if not isinstance(pl, dict):
                continue
            merged[pid] = {
                "payload": pl,
                "semantic_score": _clamp_unit_score(p.score),
                "name_score": 0.0,
            }
    except Exception:
        pass

    try:
        res_name = client.query_points(
            collection_name=CODEBASE_ENTITIES_COLLECTION,
            query=query_name,
            using="name_vector",
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        for p in res_name.points or []:
            pid = str(p.id)
            pl = p.payload or {}
            if not isinstance(pl, dict):
                continue
            ns = _clamp_unit_score(p.score)
            if pid in merged:
                merged[pid]["name_score"] = ns
            else:
                merged[pid] = {
                    "payload": pl,
                    "semantic_score": 0.0,
                    "name_score": ns,
                }
    except Exception:
        pass

    return merged


def _must_filter(project_name: str, action: str, inferred_module: str | None) -> qm.Filter | None:
    must: list[qm.Condition] = [
        qm.FieldCondition(key="project", match=qm.MatchValue(value=project_name)),
    ]
    if inferred_module:
        must.append(
            qm.FieldCondition(key="module", match=qm.MatchValue(value=inferred_module.lower()))
        )
    expected = _ACTION_EXPECTED_TYPES.get(action, [])
    if expected:
        must.append(
            qm.Filter(
                should=[
                    qm.FieldCondition(key="type", match=qm.MatchValue(value=t)) for t in expected
                ],
            )
        )
    return qm.Filter(must=must) if must else None


def hybrid_search_candidates(
    query_text: str,
    query_entity: str,
    project_name: str | None,
    action: str,
    inferred_module: str | None,
    top_k: int = 10,
) -> list[dict]:
    if not project_name or not is_available():
        return []
    ensure_collection()
    qtext = (query_text or "").strip() or query_entity
    name_q = _normalize_name(query_entity or qtext[:200])
    cache_key = hashlib.sha256(
        f"{project_name}|{action}|{inferred_module or ''}|{qtext}|{name_q}".encode()
    ).hexdigest()
    now = time.time()
    qc = _QCACHE.get(cache_key)
    if qc and (now - qc[0]) < _QCACHE_TTL_SEC:
        return qc[1][:top_k]

    try:
        q_sem = _embed(qtext, SEMANTIC_VECTOR_DIM)
        q_name = _embed(name_q or qtext[:200], NAME_VECTOR_DIM)
    except Exception:
        return []

    client = _get_qdrant()
    flt = _must_filter(project_name, action, inferred_module)
    t0 = time.perf_counter()
    fusion = _prefetch_fusion_search(client, q_sem, q_name, flt, _PHASE1_TOP)
    phase1: list[dict] = []

    if fusion:
        _mode, frows = fusion
        for fr in frows:
            pl = fr["payload"]
            fs = fr["fusion_score"]
            cn = normalize_cosine(fs)
            phase1.append(
                {
                    "payload": pl,
                    "semantic_similarity": fs,
                    "name_vector_similarity": fs,
                    "fused_vector_score": cn,
                    "_sem_norm": cn,
                    "_nv_norm": cn,
                    "_fusion_mode": _mode,
                }
            )
    else:
        merged = _merge_dual_vector_hits(client, q_sem, q_name, flt, limit=120)
        for row in merged.values():
            pl = row["payload"]
            sem = row["semantic_score"]
            nv = row["name_score"]
            phase1.append(
                {
                    "payload": pl,
                    "semantic_similarity": sem,
                    "name_vector_similarity": nv,
                    "fused_vector_score": _fused_vector_normalized(sem, nv),
                    "_sem_norm": normalize_cosine(sem),
                    "_nv_norm": normalize_cosine(nv),
                    "_fusion_mode": "merge",
                }
            )
        phase1.sort(key=lambda x: x["fused_vector_score"], reverse=True)
        phase1 = phase1[:_PHASE1_TOP]

    phase1.sort(key=lambda x: x["fused_vector_score"], reverse=True)

    rel_scores = graph_store.batch_relation_scores(
        project_name, [x["payload"] for x in phase1]
    )
    ranked: list[dict] = []
    for i, item in enumerate(phase1):
        pl = item["payload"]
        fs = _production_final_score(
            item["_sem_norm"],
            item["_nv_norm"],
            query_entity,
            action,
            inferred_module,
            pl,
            rel_scores[i] if i < len(rel_scores) else 0.0,
        )
        ranked.append(
            {
                "payload": pl,
                "semantic_similarity": item["semantic_similarity"],
                "name_vector_similarity": item["name_vector_similarity"],
                "fused_vector_score": item["fused_vector_score"],
                "final_score": fs,
            }
        )

    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    out = ranked[:top_k]
    _QCACHE[cache_key] = (now, ranked)
    if len(_QCACHE) > 500:
        for k, (ts, _) in list(_QCACHE.items())[:50]:
            if now - ts > _QCACHE_TTL_SEC:
                del _QCACHE[k]
    _log.debug(
        "hybrid_search ms=%.1f mode=%s phase1=%d",
        (time.perf_counter() - t0) * 1000,
        phase1[0].get("_fusion_mode") if phase1 else "none",
        len(phase1),
    )
    return out


def format_candidates_for_prompt(candidates: list[dict]) -> str:
    import json

    out = []
    for i, c in enumerate(candidates):
        pl = c["payload"]
        out.append(
            {
                "rank": i + 1,
                "name": pl.get("name"),
                "type": pl.get("type"),
                "file_path": pl.get("file_path"),
                "module": pl.get("module"),
                "service": pl.get("service"),
                "semantic_similarity": round(c["semantic_similarity"], 4),
                "name_vector_similarity": round(c.get("name_vector_similarity", 0.0), 4),
                "fused_vector_score": round(c.get("fused_vector_score", c["semantic_similarity"]), 4),
                "final_score": round(c["final_score"], 4),
                "code_snippet": (pl.get("code_snippet") or "")[:600],
                "relations": pl.get("relations") or [],
            }
        )
    return json.dumps(out, indent=2)


def search_snippets_for_context(query: str, project_name: str | None, top_k: int = 5) -> list[str]:
    """Project-scoped retrieval using fused semantic + name_vector scores."""
    if not project_name or not is_available():
        return []
    ensure_collection()
    try:
        q_sem = _embed(query, SEMANTIC_VECTOR_DIM)
        q_name = _embed(_normalize_name(query) or query[:200], NAME_VECTOR_DIM)
    except Exception:
        return []
    client = _get_qdrant()
    flt = qm.Filter(must=[qm.FieldCondition(key="project", match=qm.MatchValue(value=project_name))])
    merged = _merge_dual_vector_hits(client, q_sem, q_name, flt, limit=max(top_k * 4, 16))
    scored: list[tuple[float, dict]] = []
    for row in merged.values():
        pl = row["payload"]
        fused = _fused_vector_normalized(row["semantic_score"], row["name_score"])
        scored.append((fused, pl))
    scored.sort(key=lambda x: x[0], reverse=True)
    snippets = []
    for _, pl in scored[:top_k]:
        name = pl.get("name", "")
        fp = pl.get("file_path", "")
        body = (pl.get("code_snippet") or "")[:500]
        label = f"[{name} in {fp}]" if name else ""
        snippets.append(f"{label}\n{body}".strip())
    return snippets


def top_match_tier(final_score: float) -> str:
    if final_score > 0.85:
        return "auto"
    if final_score >= 0.65:
        return "confirm"
    return "reject"
