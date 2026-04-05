import json
import logging
import os
import re
import time
from typing import Any

from google import genai
from dotenv import load_dotenv

from backend.services import entity_vector_index

load_dotenv()

_log = logging.getLogger(__name__)

_GLOSSARY = os.getenv("TRANSCRIPTION_GLOSSARY", "")
_EXTRA_PROMPT = os.getenv("TRANSCRIPTION_EXTRA_PROMPT", "")

_MIN_INTENT_CONFIDENCE = 0.5
_FALLBACK_MAX_GROUNDING_CONF = 0.6
_MIN_CATALOG_LINES_FOR_ENTITY_GATE = 8


def _empty_out(pipeline_status: str = "REJECTED") -> dict[str, Any]:
    return {
        "task": None,
        "description": None,
        "eta": None,
        "type": None,
        "assignee": None,
        "pipeline_status": pipeline_status,
    }


def _eval_trace(
    action: str,
    conf: float,
    entity_hint: str,
    candidates: list[dict],
    *,
    grounded: str | None = None,
) -> dict[str, Any]:
    return {
        "intent_entity": entity_hint,
        "intent_action": action,
        "intent_confidence": round(conf, 4),
        "grounded_entity": grounded,
        "candidates": _structured_candidates(candidates),
    }


def _structured_candidates(ranked: list[dict], limit: int = 8) -> list[dict[str, Any]]:
    """Usable clarification payloads for UI."""
    out: list[dict[str, Any]] = []
    for c in ranked[:limit]:
        pl = c.get("payload") or {}
        name = pl.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "module": pl.get("module") or "",
                "type": pl.get("type") or "",
                "confidence": round(float(c.get("final_score", 0.0)), 4),
            }
        )
    return out


def _with_clarification(
    status: str,
    message: str,
    candidates: list[dict[str, Any]],
    *,
    pipeline_status: str = "CLARIFICATION",
    audit: dict[str, Any] | None = None,
    eval_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = _empty_out(pipeline_status)
    out["clarification"] = {
        "status": status,
        "message": message,
        "candidates": candidates,
    }
    if eval_trace is not None:
        out["eval_trace"] = eval_trace
    log_row: dict[str, Any] = {
        "event": "llm_pipeline",
        "pipeline_status": pipeline_status,
        "clarification_status": status,
    }
    if audit:
        log_row.update(audit)
    _log.info(json.dumps(log_row))
    return out


def _catalog_symbol_set(entity_list: str) -> set[str]:
    """Parse graph catalog lines like 'Enum:USER_TYPES'."""
    found: set[str] = set()
    for raw in (entity_list or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("("):
            continue
        if ":" in s:
            s = s.split(":", 1)[1].strip()
        if s:
            found.add(s)
            found.add(s.lower())
            found.add(s.replace(" ", "_"))
            found.add(s.replace(" ", "_").lower())
    return found


def _entity_in_catalog(entity: str, catalog: set[str]) -> bool:
    if not entity.strip():
        return True
    e = entity.strip()
    candidates = (
        e,
        e.lower(),
        e.replace(" ", "_"),
        e.replace(" ", "_").lower(),
        e.replace("-", "_"),
    )
    return any(x in catalog for x in candidates if x)


def _hard_substring_aligns(intent_entity: str, best_match: str) -> bool:
    e = (intent_entity or "").strip().lower()
    b = (best_match or "").strip().lower()
    if not e or not b:
        return True
    return e in b or b in e


def _payload_to_symbol_type(pt: str) -> str:
    if pt == "enum":
        return "enum"
    if pt in ("graphql_type", "graphql_input", "graphql_interface", "graphql_args_type"):
        return "class"
    if pt == "function":
        return "field"
    if pt in ("service", "controller", "resolver", "module", "entity"):
        return "service"
    return "service"


def _grounding_from_top_candidate(top: dict, has_vector_hits: bool) -> dict[str, Any]:
    pl = top.get("payload") or {}
    best = (pl.get("name") or "").strip()
    fs = float(top.get("final_score") or 0.0)
    gconf = min(fs, 1.0)
    if not has_vector_hits:
        gconf = min(gconf, _FALLBACK_MAX_GROUNDING_CONF)
    return {
        "entity_found": True,
        "best_match": best,
        "file_path": pl.get("file_path") or "",
        "symbol_type": _payload_to_symbol_type(pl.get("type") or ""),
        "confidence": gconf,
    }


def _rule_based_impact(_intent: dict, pl: dict) -> dict[str, Any]:
    pt = pl.get("type") or ""
    name = pl.get("name") or ""
    mod = pl.get("module") or ""
    components: list[dict[str, str]] = []
    if pt == "enum":
        components.append({"name": name, "type": "enum", "reason": "Enum symbol change"})
        if mod:
            components.append(
                {"name": mod, "type": "service", "reason": "Containing module / services"}
            )
    elif pt == "function":
        components.append(
            {"name": name, "type": "resolver", "reason": "Function / resolver logic"}
        )
    elif pt in ("service", "controller"):
        components.append({"name": name, "type": "service", "reason": "Service layer"})
    elif pt in ("graphql_type", "graphql_input", "graphql_interface", "graphql_args_type"):
        components.append({"name": name, "type": "resolver", "reason": "GraphQL schema type"})
    else:
        components.append({"name": name or "(grounded)", "type": "entity", "reason": "Grounded symbol"})
    return {"affected_components": components}


# Stage prompts

_STAGE1_CORRECT = """You are a domain-aware transcript correction system.

Your job is to fix transcription errors using domain context.

Rules:
- Prefer domain terms over phonetically similar generic words
- Do NOT invent new meaning
- Keep original intent unchanged
- Fix numbers (e.g., "five" → "5") if used as identifiers
- If ambiguity exists, choose the most likely engineering intent

Known domain terms:
{DOMAIN_TERMS}

Common confusions:
{CONFUSION_MAP}

Input:
"{TRANSCRIPT}"

Output:
Return ONLY the corrected sentence."""

_STAGE2_INTENT = """You are an intent extraction engine.

Extract structured intent from the sentence.

Rules:
- Output STRICT JSON only
- Do NOT hallucinate entities
- If unsure, lower confidence
- Prefer known domain entities only
- Normalize numbers and enum values
- Include module_hint: short folder/module name if inferable from text (e.g. payout), else ""

Allowed actions:
- ADD_ENUM_VALUE
- UPDATE_ENUM
- CREATE_FIELD
- UPDATE_LOGIC
- UNKNOWN

Known entities:
{ENTITY_LIST}

Input:
"{CORRECTED_TEXT}"

Output JSON format:
{
  "action": "",
  "entity": "",
  "value": "",
  "confidence": 0.0,
  "raw_text": "",
  "module_hint": ""
}"""

_STAGE5_JIRA = """You are a senior backend engineer writing a Jira ticket.

Rules:
- Be precise and minimal
- Do NOT include unrelated components
- Only include grounded + validated entities
- No hallucinations

Input:
Intent:
{INTENT_JSON}

Grounded Entity:
{GROUNDING_JSON}

Impact:
{IMPACT_JSON}

Output:

Title:
<short>

Description:
- bullet points of exact changes"""


def _strip_json_fence(raw: str) -> str:
    t = raw.strip()
    t = re.sub(r"^```json\s*", "", t)
    t = re.sub(r"^```\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _parse_json_obj(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return None


def _parse_jira_title_body(text: str) -> tuple[str | None, str | None]:
    raw = text.strip()
    title_m = re.search(r"(?is)Title:\s*(.+?)(?:\n\s*\n|\nDescription:)", raw)
    if not title_m:
        title_m = re.search(r"(?i)Title:\s*(.+)$", raw, re.MULTILINE)
    desc_m = re.search(r"(?is)Description:\s*(.+)", raw)
    title = title_m.group(1).strip() if title_m else None
    desc = desc_m.group(1).strip() if desc_m else None
    if title:
        title = title.split("\n")[0].strip()
    return title, desc


class GeminiLLM:

    def __init__(self, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
        self._model = model

    async def _generate_text(self, prompt: str) -> str:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
        )
        return (response.text or "").strip()

    def _domain_terms(self, llm_input: dict) -> str:
        parts = [llm_input.get("domain_terms") or ""]
        if _GLOSSARY:
            parts.append(_GLOSSARY)
        if _EXTRA_PROMPT:
            parts.append(_EXTRA_PROMPT)
        merged = "\n".join(p.strip() for p in parts if p and str(p).strip())
        return merged or "(none)"

    async def process(self, llm_input: dict) -> dict[str, Any]:
        timings: dict[str, float] = {}
        t_pipeline = time.perf_counter()
        chunk = (llm_input.get("chunk") or "").strip()
        project_name = llm_input.get("project_name")
        entity_list = llm_input.get("entity_list") or "(index project to populate)"
        confusion_map = llm_input.get("confusion_map") or "(none)"
        code_context = llm_input.get("code_context") or ""
        if not chunk:
            return _with_clarification(
                "EMPTY_INPUT",
                "No transcript text to process.",
                [],
                pipeline_status="CLARIFICATION",
                audit={"project": project_name},
            )

        # Correction
        s1_prompt = (
            _STAGE1_CORRECT.replace("{DOMAIN_TERMS}", self._domain_terms(llm_input))
            .replace("{CONFUSION_MAP}", confusion_map)
            .replace("{TRANSCRIPT}", chunk)
        )
        t0 = time.perf_counter()
        try:
            corrected = await self._generate_text(s1_prompt)
        except Exception as exc:
            _log.warning("llm_stage1_correction_failed: %s", exc)
            corrected = chunk
        timings["s1_correction_ms"] = (time.perf_counter() - t0) * 1000
        corrected = corrected.strip() or chunk

        # Intent JSON
        s2_prompt = (
            _STAGE2_INTENT.replace("{ENTITY_LIST}", entity_list).replace("{CORRECTED_TEXT}", corrected)
        )
        t0 = time.perf_counter()
        try:
            intent_raw = await self._generate_text(s2_prompt)
        except Exception as exc:
            _log.warning("llm_stage2_intent_failed: %s", exc)
            return _with_clarification(
                "INTENT_EXTRACT_FAILED",
                "Could not extract intent from the transcript.",
                [],
                pipeline_status="ERROR",
                audit={"project": project_name},
                eval_trace=_eval_trace("UNKNOWN", 0.0, "", []),
            )
        timings["s2_intent_ms"] = (time.perf_counter() - t0) * 1000
        intent = _parse_json_obj(intent_raw) or {}
        action = (intent.get("action") or "UNKNOWN").strip()
        if action == "UNKNOWN":
            return _with_clarification(
                "UNKNOWN_ACTION",
                "Could not classify the requested change; try naming a concrete action and symbol.",
                [],
                pipeline_status="REJECTED",
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(
                    action,
                    float(intent.get("confidence") or 0),
                    (intent.get("entity") or "").strip(),
                    [],
                ),
            )
        conf = float(intent.get("confidence") or 0)
        if conf < _MIN_INTENT_CONFIDENCE:
            return _with_clarification(
                "LOW_INTENT_CONFIDENCE",
                "Intent confidence is below threshold; rephrase with a concrete symbol, module, or action.",
                [],
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(
                    action,
                    conf,
                    (intent.get("entity") or "").strip(),
                    [],
                ),
            )

        entity_hint = (intent.get("entity") or "").strip()
        module_hint = (intent.get("module_hint") or "").strip() or None

        catalog = _catalog_symbol_set(entity_list)
        catalog_lines = sum(1 for ln in entity_list.splitlines() if ln.strip() and not ln.strip().startswith("("))

        # Hybrid retrieval
        candidates: list[dict] = []
        t0 = time.perf_counter()
        if project_name and entity_vector_index.is_available():
            qtext = f"{corrected} {entity_hint}".strip()
            candidates = entity_vector_index.hybrid_search_candidates(
                query_text=qtext,
                query_entity=entity_hint or corrected[:120],
                project_name=project_name,
                action=action,
                inferred_module=module_hint,
                top_k=10,
            )
        timings["retrieval_ms"] = (time.perf_counter() - t0) * 1000

        if catalog_lines >= _MIN_CATALOG_LINES_FOR_ENTITY_GATE and entity_hint:
            if not _entity_in_catalog(entity_hint, catalog):
                return _with_clarification(
                    "UNKNOWN_ENTITY",
                    f"Extracted entity '{entity_hint}' is not in the known symbol catalog.",
                    _structured_candidates(candidates),
                    audit={"project": project_name, "action": action},
                    eval_trace=_eval_trace(action, conf, entity_hint, candidates),
                )

        has_vector_hits = bool(candidates)
        top_score = candidates[0]["final_score"] if candidates else 0.0
        tier: str | None
        if has_vector_hits:
            tier = entity_vector_index.top_match_tier(top_score)
            if tier == "reject":
                sc = _structured_candidates(candidates)
                top_n = sc[0]["name"] if sc else ""
                msg = (
                    f"Did you mean {top_n}?"
                    if top_n
                    else "No confident match for this intent in the indexed codebase."
                )
                return _with_clarification(
                    "LOW_CONFIDENCE",
                    msg,
                    sc,
                    audit={"project": project_name, "action": action},
                    eval_trace=_eval_trace(action, conf, entity_hint, candidates),
                )
        else:
            tier = None

        if not candidates:
            if not code_context:
                return _with_clarification(
                    "NO_INDEX_HITS",
                    "No indexed entity matches and no code context; index the project or speak more specifically.",
                    [],
                    audit={"project": project_name, "action": action},
                    eval_trace=_eval_trace(action, conf, entity_hint, candidates),
                )
            return _with_clarification(
                "NO_VECTOR_GROUNDING",
                "No indexed entity match for this intent; refresh the code index. "
                "Some code context was retrieved but cannot be grounded without vector hits.",
                [],
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(action, conf, entity_hint, candidates),
            )

        top_pl = candidates[0].get("payload") or {}
        if action in ("ADD_ENUM_VALUE", "UPDATE_ENUM") and top_pl.get("type") != "enum":
            return _with_clarification(
                "ENUM_ACTION_MISMATCH",
                f"Action {action} requires an enum symbol; top match is {top_pl.get('type') or 'unknown'}.",
                _structured_candidates(candidates),
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(action, conf, entity_hint, candidates),
            )

        grounding = _grounding_from_top_candidate(candidates[0], has_vector_hits)
        best_match = (grounding.get("best_match") or "").strip()
        if not best_match:
            return _with_clarification(
                "LOW_CONFIDENCE",
                "Top candidate has no symbol name.",
                _structured_candidates(candidates),
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(action, conf, entity_hint, candidates),
            )

        if entity_hint and best_match and not _hard_substring_aligns(entity_hint, best_match):
            return _with_clarification(
                "ALIGNMENT_FAILED",
                f"Grounded symbol '{best_match}' does not align with intent entity '{entity_hint}'.",
                _structured_candidates(candidates),
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(
                    action, conf, entity_hint, candidates, grounded=best_match
                ),
            )

        impact = _rule_based_impact(intent, top_pl)

        s5_prompt = (
            _STAGE5_JIRA.replace("{INTENT_JSON}", json.dumps(intent, indent=2))
            .replace("{GROUNDING_JSON}", json.dumps(grounding, indent=2))
            .replace("{IMPACT_JSON}", json.dumps(impact, indent=2))
        )
        t0 = time.perf_counter()
        try:
            jira_raw = await self._generate_text(s5_prompt)
        except Exception as exc:
            _log.warning("llm_stage5_jira_failed: %s", exc)
            return _with_clarification(
                "JIRA_GENERATION_FAILED",
                "Could not generate Jira text from grounded intent.",
                _structured_candidates(candidates),
                pipeline_status="ERROR",
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(
                    action, conf, entity_hint, candidates, grounded=best_match
                ),
            )
        timings["s5_jira_ms"] = (time.perf_counter() - t0) * 1000

        title, description = _parse_jira_title_body(jira_raw)
        if not title:
            sc = _structured_candidates(candidates) if candidates else []
            return _with_clarification(
                "LOW_CONFIDENCE",
                "Could not produce a ticket title from the pipeline output.",
                sc,
                audit={"project": project_name, "action": action},
                eval_trace=_eval_trace(
                    action, conf, entity_hint, candidates, grounded=best_match
                ),
            )

        if tier == "confirm" and has_vector_hits:
            description = (
                "[Grounding score 0.65-0.85, please confirm entity match]\n\n"
                + (description or "")
            )

        ticket_type = "feature"
        if action in ("ADD_ENUM_VALUE", "UPDATE_ENUM"):
            ticket_type = "feature"
        elif action == "UPDATE_LOGIC":
            ticket_type = "bug"

        gconf = float(grounding.get("confidence") or 0)
        timings["total_ms"] = (time.perf_counter() - t_pipeline) * 1000
        jira_metadata: dict[str, Any] = {
            "retrieval_top_score": round(top_score, 4) if has_vector_hits else None,
            "grounding_confidence": round(gconf, 4),
            "source": "fallback" if not has_vector_hits else "vector",
            "tier": tier,
            "intent_grounding_aligned": True,
            "stage_timings_ms": {k: round(v, 2) for k, v in timings.items()},
        }
        if not has_vector_hits:
            jira_metadata["grounding_confidence_capped"] = True
            jira_metadata["max_grounding_confidence"] = _FALLBACK_MAX_GROUNDING_CONF

        out: dict[str, Any] = {
            "task": title,
            "description": description,
            "eta": None,
            "type": ticket_type,
            "assignee": None,
            "jira_metadata": jira_metadata,
            "pipeline_status": "SUCCESS",
            "eval_trace": _eval_trace(
                action, conf, entity_hint, candidates, grounded=best_match
            ),
        }
        _log.info(
            json.dumps(
                {
                    "event": "llm_pipeline",
                    "pipeline_status": "SUCCESS",
                    "project": project_name,
                    "action": action,
                    "timings_ms": jira_metadata["stage_timings_ms"],
                }
            )
        )
        return out
