import json
import re
from typing import Any, Dict, List, Tuple, Optional

from ai_summary.openai_client import get_openai_client


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "header": {"type": "object"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "introduction": {"type": "string"},
        "results": {"type": "array"},
        "discussion": {"type": "string"},
        "figures": {"type": "object"},
    },
    "required": ["header", "key_points", "introduction", "results", "discussion", "figures"],
}

MINI_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "section_title": {"type": "string"},
        "mini_summary": {"type": "string"},
    },
    "required": ["section_title", "mini_summary"],
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
    SDK compatibility:
    - If Responses API supports json_schema formatting, great (not in your SDK).
    - If not, we request "JSON only" and parse with json.loads.
    - Fallback to Chat Completions if Responses API is unavailable.
    """
    # We still keep schema arg to preserve call sites, but we enforce JSON via prompt.
    payload_text = json.dumps(payload_obj, ensure_ascii=False)

    # Make the prompt self-enforcing: JSON only, no markdown.
    enforced_prompt = (
        prompt.strip()
        + "\n\n"
        + "CRITICAL OUTPUT RULE:\n"
        + "- Return ONLY a single valid JSON object.\n"
        + "- No markdown, no code fences, no commentary.\n"
        + "- Ensure the JSON is strictly parseable by json.loads.\n"
    )

    # First try Responses API with minimal args (no text_format/response_format)
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": enforced_prompt},
                        {"type": "input_text", "text": payload_text},
                    ],
                }
            ],
        )
        usage = getattr(resp, "usage", None)
        txt = _extract_response_text(resp)
        txt = _strip_json_fence(txt)
        parsed = json.loads(txt)
        return parsed, usage
    except TypeError:
        # This happens if the SDK's responses.create signature is different.
        pass
    except Exception:
        # If the call succeeded but parsing failed, re-raise with raw output below.
        # We'll handle by trying chat completions; if that also fails, raise.
        try_chat = True
    else:
        try_chat = False

    # Fallback: Chat Completions
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": enforced_prompt},
                {"role": "user", "content": payload_text},
            ],
            # Some SDKs support forcing JSON object; if not, will error and we retry without it.
            response_format={"type": "json_object"},
        )
    except TypeError:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": enforced_prompt},
                {"role": "user", "content": payload_text},
            ],
        )

    usage = getattr(resp, "usage", None)
    txt = _extract_response_text(resp)
    txt = _strip_json_fence(txt)
    try:
        parsed = json.loads(txt)
    except Exception as ex:
        raise RuntimeError(f"Failed to parse model JSON output. Raw output:\n{txt}") from ex
    return parsed, usage


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
) -> Tuple[Dict[str, Any], Any]:
    lang = _lang_label(language)

    required_refs = extract_non_supp_figure_refs(section_text)

    refs_clause = ""
    if required_refs:
        # Provide exact refs and ask model to keep them unchanged.
        refs_joined = "; ".join(required_refs)
        refs_clause = f"""
FIGURE REFERENCES (MANDATORY):
- The source text contains these NON-supplementary figure references:
  {refs_joined}
- You MUST include EVERY ONE of them in the mini-summary.
- Keep them in the SAME textual form (copy/paste), do not reformat, renumber, or paraphrase.
- Do NOT include Supplementary/Appendix/Extended Data figure references.
"""

    prompt = f"""
Write a concise scientific mini-summary in {lang} for ONE Results subsection.

INPUT:
- You will receive a JSON with:
  - section_title (string)
  - section_text (string)

HARD RULES (non-negotiable):
- Output MUST be valid JSON following the provided schema, and nothing else.
- section_title in output MUST exactly equal the input section_title.
- Do NOT invent any data not present in section_text.
- Preserve all NON-supplementary figure references that appear in the source text.
- Ignore any supplementary figure references (e.g., Fig. S1, Supplementary Fig. 2).

{refs_clause}

CONTENT:
- State the main claim(s).
- Briefly mention the evidence/measurements/observations supporting the claim(s).
- Keep it compact and technical.

OUTPUT JSON SCHEMA:
{{"section_title": "...", "mini_summary": "..."}}
"""

    payload = {"section_title": section_title, "section_text": section_text}
    out, usage = _call_json_schema(client, model=model, prompt=prompt, payload_obj=payload, schema=MINI_RESULT_SCHEMA)

    # Post-check / repair: ensure figure refs preserved
    ok, missing = _contains_all_refs(out.get("mini_summary", ""), required_refs)
    if ok:
        return out, usage

    repair_prompt = f"""
You previously wrote a mini-summary but missed some REQUIRED non-supplementary figure references.

TASK:
- Return ONLY valid JSON following the schema.
- Keep section_title EXACTLY the same.
- Update mini_summary to include the missing figure references EXACTLY as provided.
- Do NOT add any supplementary figure references.
- Keep length similar; do not add new claims.

MISSING REFS (must appear verbatim):
{"; ".join(missing)}
"""
    repair_payload = {
        "section_title": out.get("section_title", section_title),
        "mini_summary": out.get("mini_summary", ""),
    }
    repaired, usage2 = _call_json_schema(client, model=model, prompt=repair_prompt, payload_obj=repair_payload, schema=MINI_RESULT_SCHEMA)
    return repaired, (usage, usage2)


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
        raise ValueError("No Results subsections found in article JSON.")

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

FIGURE REFERENCES:
- Preserve NON-supplementary figure references present in mini-summaries/captions.
- Do NOT include supplementary figure references (Fig. S..., Supplementary Fig...).

OUTPUT:
- Return ONLY valid JSON following the provided schema (no extra text).
"""

    payload = {
        "article_json": article_json,
        "results_titles": results_titles,
        "results_mini": results_mini,
        "figures_narrative": figures_narrative,
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
) -> tuple[dict, dict]:
    """
    strategy:
      - "auto": single_shot if input small else hierarchical
      - "single_shot": old behavior (one request)
      - "hierarchical": map-reduce
    """
    client = get_openai_client()
    usage_total: Dict[str, Any] = {}

    def _get_res_title(item: dict) -> str:
        return (item.get("title") or item.get("section_title") or "").strip()

    results_titles = [_get_res_title(r) for r in article_json.get("results", []) if _get_res_title(r)]
    if not results_titles:
        # as you requested: stop and let user fill Results manually
        raise ValueError("No Results subsections found in article JSON.")

    strat = (strategy or "auto").strip().lower()

    # Decide auto
    if strat == "auto":
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
    figures = article_json.get("figures", []) or []
    chunks: List[str] = []
    if figures:
        chunks, usages = _generate_figures_narrative_chunks(
            client,
            model=model,
            language=language,
            figures=figures,
            results_mini=results_mini,
            batch_size=figures_batch_size,
        )
        for u in usages:
            usage_total = _merge_usage(usage_total, u)
    figures_narrative = "\n\n".join([c for c in chunks if c]).strip()

    # -------------------------
    # REDUCE: final structured summary
    # -------------------------
    final, usage = _generate_final_summary_reduce(
        client,
        model=model,
        language=language,
        article_json=article_json,
        results_mini=results_mini,
        figures_narrative=figures_narrative,
    )
    usage_total = _merge_usage(usage_total, usage)

    return final, usage_total
