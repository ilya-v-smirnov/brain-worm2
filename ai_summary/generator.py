import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Mapping

from ai_summary.openai_client import get_openai_client


# -----------------------------
# Debug logging (enable with SUMMARY_DEBUG=1)
# -----------------------------
_SUMMARY_DEBUG = os.getenv("SUMMARY_DEBUG", "").strip().lower() in {"1", "true", "yes", "y", "on"}
_DBG_MAX_CONSOLE_CHARS = int(os.getenv("SUMMARY_DEBUG_MAX_CHARS", "8000"))  # console safety
_DBG_DIR = Path(os.getenv("SUMMARY_DEBUG_DIR", "/tmp/brain_worm_llm_logs"))
_DBG_DIR.mkdir(parents=True, exist_ok=True)
_DBG_CALL_ID = 0


def _dbg_print(msg: str) -> None:
    if _SUMMARY_DEBUG:
        print(msg)


def _log_llm_output(kind: str, model: str, text: str) -> None:
    """
    Writes full raw LLM output to /tmp (or SUMMARY_DEBUG_DIR).
    Also prints a truncated version to console if SUMMARY_DEBUG=1.
    """
    global _DBG_CALL_ID
    _DBG_CALL_ID += 1
    call_id = _DBG_CALL_ID

    # Always keep full text in file when debug enabled
    if _SUMMARY_DEBUG:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "_", kind)[:50]
        safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (model or "unknown"))[:60]
        out_path = _DBG_DIR / f"llm_{ts}_#{call_id}_{safe_kind}_{safe_model}.txt"
        out_path.write_text(text or "", encoding="utf-8")

        _dbg_print(f"[LLM-OUT] #{call_id} kind={kind} model={model!r} chars={len(text or '')} saved={out_path}")

        # Console output (truncated for safety)
        t = text or ""
        if len(t) > _DBG_MAX_CONSOLE_CHARS:
            _dbg_print(t[:_DBG_MAX_CONSOLE_CHARS] + "\n--- [TRUNCATED] ---\n")
        else:
            _dbg_print(t + "\n--- [END] ---\n")


MINI_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "section_title": {"type": "string"},
        "mini_summary": {"type": "string"},
    },
    "required": ["section_title", "mini_summary"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "header": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "year": {},
                "source_path": {"type": "string"},
                "model": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["title", "year", "source_path", "model", "language"],
        },
        "key_points": {"type": "array", "items": {"type": "string"}},
        "introduction": {"type": "string"},
        "results": {"type": "array", "items": MINI_RESULT_SCHEMA},
        "discussion": {"type": "string"},
        "figures": {
            "type": "object",
            "properties": {
                "narrative": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "figure": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["figure", "summary"],
                    },
                },
            },
            "required": ["narrative", "items"],
        },
        "abbreviations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "abbr": {"type": "string"},
                    "expanded": {"type": "string"},
                },
                "required": ["abbr", "expanded"],
            },
        },
    },
    "required": [
        "header",
        "key_points",
        "introduction",
        "results",
        "discussion",
        "figures",
        "abbreviations",
    ],
}


FIGURES_CHUNK_SCHEMA = {
    "type": "object",
    "properties": {
        "chunk_id": {"type": "integer"},
        "narrative": {"type": "string"},
    },
    "required": ["chunk_id", "narrative"],
}


# -----------------------------
# Helpers: hard cap for paid LLM calls (runaway cost protection)
# -----------------------------
_LLM_CALL_LIMITER = None  # type: Optional[callable]


def _set_llm_call_limiter(fn) -> None:
    """Install a per-run limiter (set by generate_summary)."""
    global _LLM_CALL_LIMITER
    _LLM_CALL_LIMITER = fn


def _clear_llm_call_limiter() -> None:
    global _LLM_CALL_LIMITER
    _LLM_CALL_LIMITER = None


def _bump_llm_call() -> None:
    """Count ONE paid API call attempt (including retries) and abort if over limit."""
    if _LLM_CALL_LIMITER is not None:
        _LLM_CALL_LIMITER()



# -----------------------------
# Helpers: usage aggregation
# -----------------------------
def _usage_to_dict(usage_obj: Any) -> Dict[str, Any]:
    if usage_obj is None:
        return {}
    # OpenAI SDK usage is often pydantic-like
    if hasattr(usage_obj, "model_dump"):
        return usage_obj.model_dump()
    if hasattr(usage_obj, "to_dict"):
        return usage_obj.to_dict()
    if isinstance(usage_obj, dict):
        return usage_obj
    return {"raw": str(usage_obj)}


def _merge_usage(total: Dict[str, Any], add: Any) -> Dict[str, Any]:
    add_d = _usage_to_dict(add)
    if not add_d:
        return total

    # Common token fields; keep generic merge too
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        if k in add_d:
            total[k] = int(total.get(k, 0)) + int(add_d.get(k, 0))

    # Keep per-call raw usages if you want later debugging
    total.setdefault("calls", [])
    total["calls"].append(add_d)
    return total


# -----------------------------
# Helpers: language formatting
# -----------------------------
def _lang_label(language: str) -> str:
    lang = (language or "").strip().upper()
    if lang in ("RU", "RUS", "RUSSIAN"):
        return "Russian"
    if lang in ("EN", "ENG", "ENGLISH"):
        return "English"
    # fallback: pass through as-is
    return language


# -----------------------------
# Helpers: model capabilities
# -----------------------------
def _model_supports_schema(model: str) -> bool:
    """
    Returns True if the model is expected to reliably support
    strict JSON-only outputs via response_format / schema prompting.

    IMPORTANT:
    - GPT-4 / GPT-4.1 class models are UNRELIABLE for strict JSON schemas
      in long / hierarchical generation → treat as False.
    - GPT-5.x models are designed for this → True.
    """
    if not model:
        return False

    m = model.lower()

    # Explicit allow-list
    if m.startswith("gpt-5"):
        return True

    # Everything else (gpt-4, gpt-4.1, mini, etc.)
    return False



def _get_results_titles_from_input(article_json: Dict[str, Any]) -> List[str]:
    def _get_res_title(item: dict) -> str:
        return (item.get("title") or item.get("section_title") or "").strip()

    titles = [_get_res_title(r) for r in (article_json.get("results") or []) if _get_res_title(r)]
    return titles



def _normalize_summary_output(
    article_json: Dict[str, Any],
    summary: Any,
    *,
    model: str,
    language: str,
    header_defaults: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Enforces a stable output contract for downstream writers/UI.
    - header: guarantees required keys
    - results: 1:1 ordered by input Results titles
    - figures: ensures figures.items list exists
    """
    if not isinstance(summary, dict):
        summary = {}

    out: Dict[str, Any] = dict(summary)

    # ---------- header ----------
    hdr = out.get("header")
    if not isinstance(hdr, dict):
        hdr = {}

    if header_defaults:
        for k, v in header_defaults.items():
            if k not in hdr or hdr.get(k) in (None, ""):
                hdr[k] = v

    if not hdr.get("title"):
        hdr["title"] = str(article_json.get("title", "") or "")
    if "year" not in hdr or hdr.get("year") in (None, ""):
        hdr["year"] = article_json.get("year", "")

    hdr["model"] = model
    hdr["language"] = (language or "").strip().upper()
    hdr.setdefault("source_path", "")
    out["header"] = hdr

    # ---------- key_points ----------
    kp = out.get("key_points")
    if not isinstance(kp, list):
        kp = []
    out["key_points"] = [x.strip() for x in kp if isinstance(x, str) and x.strip()]

    # ---------- introduction / discussion ----------
    intro = out.get("introduction")
    disc = out.get("discussion")

    out["introduction"] = intro.strip() if isinstance(intro, str) else ""
    out["discussion"] = disc.strip() if isinstance(disc, str) else ""
    
    # ---------- results ----------
    expected_titles = _get_results_titles_from_input(article_json)
    if not expected_titles:
        raise ValueError("No Results subsections found in input JSON.")

    raw_results = out.get("results")

    by_title: Dict[str, str] = {}

    # Case A: model returned results as a dict: {"Title 1": "...", "Title 2": "..."}
    if isinstance(raw_results, dict):
        for k, v in raw_results.items():
            t = str(k or "").strip()
            s = str(v or "").strip()
            if t:
                by_title[t] = s

    # Case B: model returned results as a list of objects
    elif isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            t = (item.get("section_title") or item.get("title") or item.get("section") or "").strip()
            s = (item.get("mini_summary") or item.get("summary") or item.get("text") or item.get("content") or "").strip()
            if t:
                by_title[t] = s


    out["results"] = [
        {"section_title": t, "mini_summary": by_title.get(t, "—") or "—"}
        for t in expected_titles
    ]

    # ---------- figures ----------
    figs = out.get("figures")
    if not isinstance(figs, dict):
        figs = {}

    # narrative (string, always present)
    narrative = figs.get("narrative")
    if not isinstance(narrative, str):
        # backward/legacy fallbacks
        legacy = out.get("figures_narrative")
        narrative = legacy if isinstance(legacy, str) else ""
    narrative = narrative.strip() if isinstance(narrative, str) else ""

    # items (list[{"figure","summary"}], always present)
    items = figs.get("items")
    if not isinstance(items, list):
        items = []
    else:
        items = [
            {
                "figure": (it.get("figure") or it.get("id") or it.get("name") or "").strip(),
                "summary": (it.get("summary") or it.get("text") or it.get("caption_summary") or "").strip(),
            }
            for it in items
            if isinstance(it, dict)
        ]
        items = [it for it in items if it["figure"] and it["summary"]]

    figs["narrative"] = narrative
    figs["items"] = items
    out["figures"] = figs

    # ---------- abbreviations ----------
    # Accept:
    # - list[{"abbr": "...", "expanded": "..."}]
    # - dict {"ABBR": "expanded", ...} (legacy/convenience)
    raw_abbr = out.get("abbreviations")
    pairs: list[tuple[str, str]] = []

    if isinstance(raw_abbr, dict):
        for k, v in raw_abbr.items():
            ab = str(k).strip()
            ex = str(v).strip() if v is not None else ""
            if ab and ex:
                pairs.append((ab, ex))
    elif isinstance(raw_abbr, list):
        for it in raw_abbr:
            if not isinstance(it, dict):
                continue
            ab = (it.get("abbr") or it.get("abbreviation") or it.get("short") or "").strip()
            ex = (it.get("expanded") or it.get("expansion") or it.get("long") or "").strip()
            if ab and ex:
                pairs.append((ab, ex))

    # de-dup by abbr (case-insensitive), keep first non-empty expanded
    dedup: dict[str, tuple[str, str]] = {}
    for ab, ex in pairs:
        key = ab.casefold()
        if key not in dedup:
            dedup[key] = (ab, ex)

    abbr_list = [{"abbr": ab, "expanded": ex} for (ab, ex) in dedup.values()]
    abbr_list.sort(key=lambda d: d["abbr"].casefold())
    out["abbreviations"] = abbr_list

    return out


def _split_text_into_chunks(text: str, *, max_chars: int = 6000) -> list[str]:
    """
    Split text into chunks <= max_chars, preferably on paragraph boundaries.
    """
    t = (text or "").strip()
    if not t:
        return []
    paras = [p.strip() for p in t.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf).strip())
            buf = []
            buf_len = 0

    for p in paras:
        if buf_len + len(p) + 2 <= max_chars:
            buf.append(p)
            buf_len += len(p) + 2
        else:
            flush()
            # paragraph might be huge; hard-split
            if len(p) <= max_chars:
                buf.append(p)
                buf_len = len(p)
            else:
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i:i + max_chars].strip())
    flush()
    return chunks


def _summarize_section_chunk(
    client,
    *,
    model: str,
    language: str,
    section_name: str,
    chunk_text: str,
) -> tuple[str, dict]:
    """
    Map step: produce a concise mini-summary for one chunk.
    """
    schema = {
        "type": "object",
        "properties": {"mini_summary": {"type": "string"}},
        "required": ["mini_summary"],
    }
    lang = (language or "").strip().upper()
    if lang not in ("EN", "RU"):
        lang = "EN"

    prompt = f"""
You summarize scientific text in {lang}.
SECTION: {section_name}

Task:
- Write a mini-summary of THIS chunk in your own words.
- Preserve key concepts and causal links.
- Do NOT copy sentences verbatim.
- Do NOT include citations like [1], (1), etc.
- Keep it concise but information-dense.

Return ONLY valid JSON matching the schema.
"""
    out, usage = _call_json_schema(
        client,
        model=model,
        prompt=prompt,
        payload_obj={"chunk": chunk_text},
        schema=schema,
    )
    ms = (out.get("mini_summary") or "").strip() if isinstance(out, dict) else ""
    return ms, usage


def _reduce_section_summaries(
    client,
    *,
    model: str,
    language: str,
    section_name: str,
    source_len: int,
    mini_summaries: list[str],
    target_ratio: float,
) -> tuple[str, dict]:
    """
    Reduce step: merge mini-summaries into a section summary of ~target_ratio of source length.
    """
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    lang = (language or "").strip().upper()
    if lang not in ("EN", "RU"):
        lang = "EN"

    # target size in characters (rough but effective; docx is text-based)
    target_chars = max(300, int(source_len * target_ratio))
    hard_cap = int(target_chars * 1.15)  # allow a bit

    joined = "\n".join(f"- {s}" for s in mini_summaries if s.strip())

    prompt = f"""
You write a structured scientific summary in {lang}.
SECTION: {section_name}

Input:
- A list of mini-summaries (bullet points), each summarizing a chunk.

Task:
- Merge them into a coherent section summary.
- Use your own words; do NOT copy from source.
- Do NOT include citations like [1], (1), etc.
- Target length: about {target_chars} characters (±15%).
- Hard cap: {hard_cap} characters.

Return ONLY valid JSON matching the schema.
"""
    out, usage = _call_json_schema(
        client,
        model=model,
        prompt=prompt,
        payload_obj={"mini_summaries": joined},
        schema=schema,
    )
    txt = (out.get("text") or "").strip() if isinstance(out, dict) else ""
    if len(txt) > hard_cap:
        txt = txt[:hard_cap].rstrip() + "…"
    return txt, usage


def _summarize_long_section_map_reduce(
    client,
    *,
    model: str,
    language: str,
    section_name: str,
    source_text: str,
    target_ratio: float,
    chunk_chars: int = 6000,
) -> tuple[str, dict]:
    """
    Full map-reduce for one long section.
    """
    usage_total: dict = {}
    chunks = _split_text_into_chunks(source_text, max_chars=chunk_chars)
    if not chunks:
        return "", usage_total

    minis: list[str] = []
    for ch in chunks:
        ms, u = _summarize_section_chunk(
            client,
            model=model,
            language=language,
            section_name=section_name,
            chunk_text=ch,
        )
        usage_total = _merge_usage(usage_total, u)
        if ms:
            minis.append(ms)

    # If something went wrong, still return something non-empty
    if not minis:
        minis = [source_text[:500].strip()]

    reduced, u2 = _reduce_section_summaries(
        client,
        model=model,
        language=language,
        section_name=section_name,
        source_len=len(source_text),
        mini_summaries=minis,
        target_ratio=target_ratio,
    )
    usage_total = _merge_usage(usage_total, u2)
    return reduced, usage_total

def _ensure_key_points(
    client,
    *,
    model: str,
    language: str,
    summary: dict,
) -> tuple[list[str], dict]:
    """
    If key_points is empty, generate 3–8 bullets from existing summary content.
    """
    usage_total: dict = {}
    kp = summary.get("key_points")
    if isinstance(kp, list) and any(isinstance(x, str) and x.strip() for x in kp):
        return [x.strip() for x in kp if isinstance(x, str) and x.strip()], usage_total

    schema = {
        "type": "object",
        "properties": {"key_points": {"type": "array", "items": {"type": "string"}}},
        "required": ["key_points"],
    }
    lang = (language or "").strip().upper()
    if lang not in ("EN", "RU"):
        lang = "EN"

    payload = {
        "introduction": summary.get("introduction", ""),
        "results": summary.get("results", []),
        "discussion": summary.get("discussion", ""),
    }

    prompt = f"""
You write key points in {lang}.
Task:
- Produce 3–8 bullet points capturing the most important findings and takeaways.
- Do NOT copy sentences verbatim.
- No citations like [1], (1), etc.

Return ONLY valid JSON matching the schema.
"""
    out, u = _call_json_schema(client, model=model, prompt=prompt, payload_obj=payload, schema=schema)
    usage_total = _merge_usage(usage_total, u)

    pts = out.get("key_points") if isinstance(out, dict) else []
    if not isinstance(pts, list):
        pts = []
    pts = [x.strip() for x in pts if isinstance(x, str) and x.strip()]
    return pts, usage_total


# -----------------------------
# Helpers: figure references
# -----------------------------
_FIG_REF_RE = re.compile(
    r"\b(?:Supplementary\s+)?(?:Fig(?:ure)?s?)\.?\s*"
    r"(?:S\s*)?\d+[A-Za-z]?(?:\s*[–-]\s*\d+[A-Za-z]?)?(?:[a-z])?\b",
    flags=re.IGNORECASE,
)


def _normalize_fig_ref(s: str) -> str:
    # normalize whitespace and dashes, keep case-insensitive compare
    s2 = re.sub(r"\s+", " ", s.strip())
    s2 = s2.replace("–", "-")
    return s2.lower()


def _is_supplementary_ref(ref: str) -> bool:
    r = ref.lower()
    # ignore anything explicitly marked supplementary or Fig S...
    if "supplementary" in r:
        return True
    # detect "Fig. S1", "Figure S2", "Figs. S3-S4", "Fig S1"
    if re.search(r"\bfig(?:ure)?s?\.?\s*s\s*\d", r):
        return True
    return False


def extract_non_supp_figure_refs(text: str) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    seen_norm: set[str] = set()
    for m in _FIG_REF_RE.finditer(text):
        ref = m.group(0).strip()
        if _is_supplementary_ref(ref):
            continue
        n = _normalize_fig_ref(ref)
        if n in seen_norm:
            continue
        seen_norm.add(n)
        found.append(ref)
    return found


def _contains_all_refs(text: str, required_refs: List[str]) -> Tuple[bool, List[str]]:
    if not required_refs:
        return True, []
    tnorm = _normalize_fig_ref(text or "")
    missing: List[str] = []
    for ref in required_refs:
        if _normalize_fig_ref(ref) not in tnorm:
            missing.append(ref)
    return (len(missing) == 0), missing


# -----------------------------
# OpenAI calls (JSON schema)
# -----------------------------
def _extract_response_text(resp: Any) -> str:
    """
    Tries to extract plain text from different OpenAI SDK response shapes.
    Works for both Responses API and Chat Completions.
    """
    # Responses API (newer): resp.output_text
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str) and resp.output_text.strip():
        return resp.output_text

    # Responses API: resp.output[].content[].text
    if hasattr(resp, "output"):
        try:
            for item in resp.output:
                content = getattr(item, "content", None)
                if not content:
                    continue
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str) and text.strip():
                        return text
        except Exception:
            pass

    # Chat Completions: resp.choices[0].message.content
    try:
        choices = getattr(resp, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content
    except Exception:
        pass

    return str(resp)


def _strip_json_fence(txt: str) -> str:
    """
    Removes ```json ... ``` wrappers if model returns fenced code.
    """
    if not isinstance(txt, str):
        return txt
    t = txt.strip()
    # ```json ... ```
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _call_json_schema(
    client,
    *,
    model: str,
    prompt: str,
    payload_obj: Any,
    schema: Dict[str, Any],
) -> Tuple[Dict[str, Any], Any]:
    """
    HARD RULE: exactly ONE paid API call per request.
    We use Chat Completions only (no Responses API fallback) to avoid double-billing.
    """
    payload_text = json.dumps(payload_obj, ensure_ascii=False)

    enforced_prompt = (
        prompt
        + "\n\n"
        + "CRITICAL OUTPUT RULE:\n"
        + "- Return ONLY a single valid JSON object.\n"
        + "- No markdown, no code fences, no commentary.\n"
        + "- Ensure the JSON is strictly parseable by json.loads.\n"
    )

    # One call only
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": enforced_prompt},
                {"role": "user", "content": payload_text},
            ],
            response_format={"type": "json_object"},
            timeout=60,
        )
    except TypeError:
        # Older SDKs may not support response_format/timeout kwargs
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": enforced_prompt},
                {"role": "user", "content": payload_text},
            ],
        )

    usage = getattr(resp, "usage", None)

    # Extract text
    txt = ""
    try:
        # OpenAI-style: resp.choices[0].message.content
        choices = getattr(resp, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            txt = (getattr(msg, "content", None) or "").strip()
    except Exception:
        txt = ""

    txt = _strip_json_fence(txt)

    # DEBUG: show raw model output
    _log_llm_output(kind="json_schema", model=model, text=txt)

    try:
        parsed = json.loads(txt)
    except Exception as ex:
        # Important: dump raw output for debugging (already saved), then raise
        raise RuntimeError(f"Failed to parse model JSON output. Raw output saved to {_DBG_DIR}.") from ex

    # DEBUG: minimal structure info
    try:
        if isinstance(parsed, dict):
            _dbg_print(f"[LLM-PARSED] keys={sorted(list(parsed.keys()))}")
            if "results" in parsed and isinstance(parsed["results"], list):
                _dbg_print(f"[LLM-PARSED] results_items={len(parsed['results'])}")
    except Exception:
        pass

    return parsed, usage



def _call_text(
    client,
    *,
    model: str,
    prompt: str,
    timeout_s: int = 60,
) -> tuple[str, Any]:
    """
    Text-only call via Chat Completions (single call, no Responses API).
    """
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_s,
        )
    except TypeError:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )

    usage = getattr(resp, "usage", None)

    txt = ""
    try:
        choices = getattr(resp, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            txt = (getattr(msg, "content", None) or "").strip()
    except Exception:
        txt = ""

    # DEBUG: show raw model output
    _log_llm_output(kind="text", model=model, text=txt)

    return txt, usage



# -----------------------------
# MAP step: results mini summaries
# -----------------------------
def _generate_result_mini_summary(
    client,
    *,
    model: str,
    language: str,
    section_title: str,
    section_text: str,
) -> Tuple[Dict[str, str], Any]:
    """
    MAP step for one Results subsection.
    - GPT-5.x: JSON-only via _call_json_schema
    - others: text-only via _call_text
    """
    lang = _lang_label(language)

    required_refs = extract_non_supp_figure_refs(section_text)
    refs_clause = ""
    if required_refs:
        refs_clause = (
            "\nFIGURE REFS (mandatory if present in section_text):\n"
            + "- Include these NON-supplementary refs verbatim: "
            + "; ".join(sorted(set(required_refs)))
            + "\n- Do NOT include supplementary refs (Fig. S..., Supplementary Fig...).\n"
        )

    # ---------- text-only fallback ----------
    if not _model_supports_schema(model):
        prompt_text = f"""
You write a compact scientific mini-summary in {lang} for ONE Results subsection.

SECTION TITLE:
{section_title}

SECTION TEXT:
{section_text}

RULES:
- 2–5 sentences.
- Do NOT output placeholders like "—" or "-".
- Do NOT repeat the title.
- Do NOT include supplementary figure refs.
{refs_clause}

Return ONLY the mini-summary text.
"""
        text, usage = _call_text(client, model=model, prompt=prompt_text, timeout_s=60)
        mini = (text or "").strip()
        if not mini or mini in {"—", "-", "–"} or len(mini) < 10:
            mini = "Summary generation failed."
        return {"section_title": section_title, "mini_summary": mini}, usage

    # ---------- GPT-5.x JSON path ----------
    prompt = f"""
You write a compact scientific mini-summary in {lang} for ONE Results subsection.

INPUT JSON contains:
- section_title: str
- section_text: str

HARD RULES:
- Preserve section_title EXACTLY.
- mini_summary must be 2–5 sentences.
- Do NOT output placeholders like "—" or "-" or empty output.
- Do NOT include supplementary figure refs.
{refs_clause}

Return ONLY valid JSON:
{{"section_title": "...", "mini_summary": "..."}}
"""
    payload = {"section_title": section_title, "section_text": section_text}
    out, usage = _call_json_schema(
        client,
        model=model,
        prompt=prompt,
        payload_obj=payload,
        schema=MINI_RESULT_SCHEMA,
    )

    ms = (out.get("mini_summary") or "").strip() if isinstance(out, dict) else ""
    if not ms or ms in {"—", "-", "–"} or len(ms) < 10:
        # one retry
        regen_prompt = f"""
Your previous mini_summary was empty/placeholder or too short.

Return ONLY valid JSON:
{{"section_title": "{section_title}", "mini_summary": "..."}}

Rules:
- 2–5 sentences, based ONLY on section_text.
- No supplementary refs.
{refs_clause}
"""
        out2, usage2 = _call_json_schema(
            client,
            model=model,
            prompt=regen_prompt,
            payload_obj=payload,
            schema=MINI_RESULT_SCHEMA,
        )
        out = out2
        usage = (usage, usage2)

    # Enforce refs if needed
    if required_refs:
        ok, missing = _contains_all_refs(out.get("mini_summary", ""), required_refs)
        if not ok and missing:
            repair_prompt = f"""
You missed required NON-supplementary figure references.

Return ONLY valid JSON:
{{"section_title": "{section_title}", "mini_summary": "..."}}

Include these refs verbatim:
{"; ".join(missing)}

No supplementary refs. 2–5 sentences.
"""
            repaired, usage3 = _call_json_schema(
                client,
                model=model,
                prompt=repair_prompt,
                payload_obj={
                    "section_title": section_title,
                    "mini_summary": out.get("mini_summary", ""),
                },
                schema=MINI_RESULT_SCHEMA,
            )
            return repaired, (usage, usage3)

    return out, usage



# -----------------------------
# Figures narrative (Approach 2)
# -----------------------------
def _generate_figures_narrative_chunks(
    client,
    *,
    model: str,
    language: str,
    figures: List[Dict[str, Any]],
    results_mini: List[Dict[str, str]],
    batch_size: int = 10,
) -> Tuple[List[str], List[Any]]:
    """
    Approach 2:
    - For each captions batch:
      - extract figure refs from captions
      - include ONLY those results mini-summaries that mention these refs
    """
    lang = _lang_label(language)

    # Precompute: result mini -> refs set
    mini_with_refs: List[Tuple[Dict[str, str], set[str]]] = []
    for item in results_mini:
        refs = extract_non_supp_figure_refs(item.get("mini_summary", ""))
        mini_with_refs.append((item, set(_normalize_fig_ref(r) for r in refs)))

    chunks: List[str] = []
    usages: List[Any] = []

    # Split figures into batches
    for i in range(0, len(figures), batch_size):
        batch = figures[i : i + batch_size]

        # Build captions block and refs set
        captions_lines: List[str] = []
        batch_refs_norm: set[str] = set()

        for f in batch:
            cap = (f.get("caption") or "").strip()
            if not cap:
                continue
            captions_lines.append(cap)
            for r in extract_non_supp_figure_refs(cap):
                batch_refs_norm.add(_normalize_fig_ref(r))

        # Select relevant mini-summaries
        relevant: List[Dict[str, str]] = []
        if batch_refs_norm:
            for item, refs_norm in mini_with_refs:
                if refs_norm & batch_refs_norm:
                    relevant.append(item)

        prompt = f"""
Write a coherent Figures narrative in {lang} for a scientific article.

INPUT JSON will contain:
- chunk_id: integer
- captions: list of figure captions (main figures only)
- relevant_results_mini: list of mini-summaries for Results subsections that mention the same figure references

RULES:
- Use ONLY the provided captions + relevant_results_mini as evidence.
- Keep figure references as they appear (do not invent new ones).
- Ignore supplementary figures (Fig. S..., Supplementary Fig...).
- Produce a narrative that links what each figure shows to the corresponding results claims.

OUTPUT:
Return ONLY valid JSON following the schema:
{{"chunk_id": <int>, "narrative": "<text>"}}
"""

        payload = {
            "chunk_id": (i // batch_size) + 1,
            "captions": captions_lines,
            "relevant_results_mini": relevant,
        }
        out, usage = _call_json_schema(client, model=model, prompt=prompt, payload_obj=payload, schema=FIGURES_CHUNK_SCHEMA)
        chunks.append(out.get("narrative", "").strip())
        usages.append(usage)

    return chunks, usages


# -----------------------------
# REDUCE step: final summary
# -----------------------------
def _generate_final_summary_reduce(
    client,
    *,
    model: str,
    language: str,
    article_json: Dict[str, Any],
    results_mini: List[Dict[str, str]],
    figures_narrative: str,
) -> Tuple[Dict[str, Any], Any]:
    lang = _lang_label(language)

    # Keep strict title list and order
    def _get_res_title(item: dict) -> str:
        return (item.get("title") or item.get("section_title") or "").strip()
    results_titles = [_get_res_title(r) for r in article_json.get("results", []) if _get_res_title(r)]
    # user asked: if empty -> error earlier, but keep safe guard
    if not results_titles:
        raise ValueError("No Results subsections found in input JSON.")

    prompt = f"""
Generate a structured scientific summary in {lang}.

You are given:
- The article JSON (Introduction/Methods/Results/Discussion/Figures captions).
- Pre-generated mini-summaries for EACH Results subsection (1:1).
- A figures narrative assembled from captions + relevant results mini-summaries.

IMPORTANT RULES:
- You MUST preserve Results subsection titles exactly as provided by the parser.
- You MUST output exactly one Results summary per input Results title, in the same order.
- Do NOT invent/merge/split/rename any Results subsections.

CONTENT REQUIREMENTS (do NOT leave empty):
- key_points: 3–8 bullet points.
- introduction: 1 short paragraph summarizing background + objective.
- discussion: 1–2 short paragraphs summarizing interpretation/implications/limitations.
- results: must be filled for every Results title.
- figures.items: list figure takeaways (can be empty list if no figures).

FIGURE REFERENCES:
- Preserve NON-supplementary figure references present in mini-summaries/captions.
- Do NOT include supplementary figure references (Fig. S..., Supplementary Fig...).

OUTPUT:
- Return ONLY valid JSON following the provided schema (no extra text).
"""

    compact_article = {
    "title": article_json.get("title", ""),
    "year": article_json.get("year", ""),
    "introduction": (article_json.get("introduction") or "")[:4000],  # small context only
    "discussion": (article_json.get("discussion") or "")[:4000],      # small context only
    }

    payload = {
        "article": compact_article,
        "results_titles": results_titles,
        "results_mini": results_mini,
        "figures_narrative": '',
    }

    out, usage = _call_json_schema(client, model=model, prompt=prompt, payload_obj=payload, schema=SUMMARY_SCHEMA)
    return out, usage


# -----------------------------
# Public API: Variant A (auto)
# -----------------------------
def generate_summary(
    article_json: dict,
    model: str,
    language: str,
    *,
    strategy: str = "auto",
    auto_threshold_chars: int = 60000,
    figures_batch_size: int = 10,
    header_defaults: Optional[Mapping[str, Any]] = None,
) -> tuple[dict, dict]:
    """
    strategy:
      - "auto": single_shot if input small else hierarchical
      - "single_shot": old behavior (one request)
      - "hierarchical": map-reduce
    """
    client = get_openai_client()
    # SAFETY: hard cap on number of LLM calls per run
    # Prevents runaway costs if something goes wrong upstream.
    MAX_LLM_CALLS = 25
    llm_calls = 0

    def _bump_calls():
        nonlocal llm_calls
        llm_calls += 1
        if llm_calls > MAX_LLM_CALLS:
            raise RuntimeError(
                f"Safety stop: exceeded MAX_LLM_CALLS={MAX_LLM_CALLS}. "
                "Generation aborted to prevent runaway costs."
            )

    _set_llm_call_limiter(_bump_calls)
    try:
        usage_total: Dict[str, Any] = {}

        results_titles = _get_results_titles_from_input(article_json)
        if not results_titles:
            raise ValueError("No Results subsections found in input JSON.")

        strat = (strategy or "auto").strip().lower()

        # Decide auto
        if strat == "auto":
            # IMPORTANT:
            # GPT-4 class models are much less reliable in "single_shot" for preserving 1:1 Results subsections.
            # Force hierarchical map-reduce to guarantee non-empty per-subsection Results.
            if not _model_supports_schema(model):
                strat = "hierarchical"
            else:
                approx_size = len(json.dumps(article_json, ensure_ascii=False))
                strat = "single_shot" if approx_size < auto_threshold_chars else "hierarchical"

        if strat == "single_shot":
            # Keep existing single-shot prompt but make language consistent label
            lang = _lang_label(language)
            prompt = f"""
Generate a structured scientific summary in {lang}.

You are given a scientific article already parsed into a structured JSON object.

IMPORTANT:
- The article JSON already contains a list of Results subsections.
- Each Results subsection has an original title provided by the parser.
- You MUST preserve these titles exactly.
- You MUST generate exactly one mini-summary for EACH Results subsection.
- You MUST NOT invent, merge, split, rename, or omit any Results subsections.

STRICT PROCEDURE (mandatory):
1. First, read the input JSON and extract the ordered list of Results subsection titles.
2. Use this list as the ONLY allowed Results sections.
3. Generate the Results summary strictly following this list, one-to-one and in the same order.

VALIDATION RULES:
- The number of Results summaries in the output MUST equal the number of Results subsections in the input.
- Every output Results section_title MUST exactly match one input Results title.

FIGURE REFERENCES:
- Preserve NON-supplementary figure references in Results/Figures narrative.
- Ignore supplementary references (Fig. S..., Supplementary Fig...).

OUTPUT FORMAT:
- Return ONLY valid JSON.
- The JSON MUST strictly follow the provided schema.
- Do NOT include any explanatory text outside the JSON.
"""
            out, usage = _call_json_schema(client, model=model, prompt=prompt, payload_obj=article_json, schema=SUMMARY_SCHEMA)
            usage_total = _merge_usage(usage_total, usage)

            out = _normalize_summary_output(
                article_json,
                out,
                model=model,
                language=language,
                header_defaults=header_defaults,
            )

            # --- Introduction/Discussion: map-reduce like Results (target 25–33%) ---

            # If the reduce step already produced non-empty intro/discussion, keep them.
            # Otherwise, fall back to map-reduce.
            if not final.get("introduction", "").strip():
                src_intro = str(article_json.get("introduction") or "")
                intro_txt, u_intro = _summarize_long_section_map_reduce(
                    client,
                    model=model,
                    language=language,
                    section_name="Introduction",
                    source_text=src_intro,
                    target_ratio=0.30,
                )
                usage_total = _merge_usage(usage_total, u_intro)
                if intro_txt:
                    final["introduction"] = intro_txt

            if not final.get("discussion", "").strip():
                src_disc = str(article_json.get("discussion") or "")
                disc_txt, u_disc = _summarize_long_section_map_reduce(
                    client,
                    model=model,
                    language=language,
                    section_name="Discussion",
                    source_text=src_disc,
                    target_ratio=0.30,
                )
                usage_total = _merge_usage(usage_total, u_disc)
                if disc_txt:
                    final["discussion"] = disc_txt

            usage_total = _merge_usage(usage_total, u_disc)
            if disc_txt:
                out["discussion"] = disc_txt

            # --- Ensure key_points are not empty ---
            kp, u_kp = _ensure_key_points(
                client,
                model=model,
                language=language,
                summary=out,
            )
            usage_total = _merge_usage(usage_total, u_kp)
            out["key_points"] = kp

            return out, usage_total


        if strat != "hierarchical":
            raise ValueError(f"Unknown strategy: {strategy!r}")

        # -------------------------
        # MAP: results mini-summaries
        # -------------------------
        results_mini: List[Dict[str, str]] = []
        for r in article_json.get("results", []):
            title = (r.get("title") or r.get("section_title") or "").strip()
            text = (r.get("text") or r.get("section_text") or "").strip()
            if not title:
                continue

            mini, usage = _generate_result_mini_summary(
                client,
                model=model,
                language=language,
                section_title=title,
                section_text=text,
            )
            # usage may be tuple (usage1, usage2) after repair
            if isinstance(usage, tuple):
                for u in usage:
                    usage_total = _merge_usage(usage_total, u)
            else:
                usage_total = _merge_usage(usage_total, usage)

            results_mini.append(
                {"section_title": mini["section_title"], "mini_summary": mini["mini_summary"]}
            )

        # Hard guard: 1:1 titles
        got_titles = [x["section_title"] for x in results_mini]
        if got_titles != results_titles:
            raise RuntimeError(
                "Internal error: Results mini-summaries titles/order mismatch.\n"
                f"Expected: {results_titles}\nGot: {got_titles}"
            )

        # -------------------------
        # MAP: figures narrative chunks (Approach 2)
        # -------------------------
        figures = ""
        # -------------------------
        # REDUCE: final structured summary
        # -------------------------
        final, usage = _generate_final_summary_reduce(
            client,
            model=model,
            language=language,
            article_json=article_json,
            results_mini=results_mini,
            figures_narrative="",
        )
        usage_total = _merge_usage(usage_total, usage)

        final = _normalize_summary_output(
            article_json,
            final,
            model=model,
            language=language,
            header_defaults=header_defaults,
        )

        # --- Introduction/Discussion: map-reduce like Results (target 25–33%) ---
        src_intro = str(article_json.get("introduction") or "")
        src_disc = str(article_json.get("discussion") or "")

        intro_ratio = 0.30
        disc_ratio = 0.30

        intro_txt, u_intro = _summarize_long_section_map_reduce(
            client,
            model=model,
            language=language,
            section_name="Introduction",
            source_text=src_intro,
            target_ratio=intro_ratio,
        )
        usage_total = _merge_usage(usage_total, u_intro)
        if intro_txt:
            final["introduction"] = intro_txt

        disc_txt, u_disc = _summarize_long_section_map_reduce(
            client,
            model=model,
            language=language,
            section_name="Discussion",
            source_text=src_disc,
            target_ratio=disc_ratio,
        )
        usage_total = _merge_usage(usage_total, u_disc)
        if disc_txt:
            final["discussion"] = disc_txt

        # --- Ensure key_points are not empty ---
        kp, u_kp = _ensure_key_points(
            client,
            model=model,
            language=language,
            summary=final,
        )
        usage_total = _merge_usage(usage_total, u_kp)
        final["key_points"] = kp

        return final, usage_total
    finally:
        _clear_llm_call_limiter()


