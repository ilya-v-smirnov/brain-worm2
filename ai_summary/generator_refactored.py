# =========================
# LLM debug + accounting infra
# =========================

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# NOTE: we rely on get_openai_client() wrapper (it already logs + has MAX_CALLS=20)
# see ai_summary/openai_client.py
# from ai_summary.openai_client import get_openai_client


@dataclass
class LLMRunStats:
    """In-memory stats (NO file write), for quick efficiency checks."""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMDebugSession:
    """
    Per-article session:
    - hard cap on LLM calls (<=10)
    - request/response logging to a technical file
    - in-memory token usage accounting
    """
    article_id: str
    out_dir: Path
    max_calls: int = 10
    stats: LLMRunStats = field(default_factory=LLMRunStats)
    tech_log_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(self.article_id))[:80] or "article"
        self.tech_log_path = self.out_dir / f"llm_debug_{safe_id}_{ts}.log"

    def _append(self, payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload["_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.tech_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    def bump_call(self) -> None:
        self.stats.calls += 1
        if self.stats.calls > self.max_calls:
            raise RuntimeError(
                f"Safety stop: exceeded per-article MAX_CALLS={self.max_calls}. "
                "Aborting to prevent runaway costs."
            )

    def add_usage(self, usage: Any) -> None:
        """
        Compatible with chat.completions usage:
        response.usage.prompt_tokens / completion_tokens / total_tokens
        If absent, we just skip.
        """
        if not usage:
            return
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        tt = getattr(usage, "total_tokens", 0) or 0
        self.stats.prompt_tokens += int(pt)
        self.stats.completion_tokens += int(ct)
        self.stats.total_tokens += int(tt)


def _safe_json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return json.dumps(str(obj), ensure_ascii=False, indent=2)


def _extract_chat_text(resp: Any) -> str:
    """
    For client.chat.completions.create(): resp.choices[0].message.content
    """
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def llm_chat_json(
    *,
    client: Any,
    session: LLMDebugSession,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """
    Single controlled entrypoint to LLM:
    - enforces <=10 calls per article
    - logs request/response
    - parses JSON output (with minimal recovery)
    """
    session.bump_call()

    req = {
        "where": "chat.completions.create",
        "model": model,
        "temperature": temperature,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    session._append({"type": "request", **req})

    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    session.add_usage(getattr(resp, "usage", None))

    raw = _extract_chat_text(resp)
    session._append(
        {
            "type": "response",
            "model": model,
            "raw_text": raw,
            "usage": {
                "prompt_tokens": getattr(getattr(resp, "usage", None), "prompt_tokens", None),
                "completion_tokens": getattr(getattr(resp, "usage", None), "completion_tokens", None),
                "total_tokens": getattr(getattr(resp, "usage", None), "total_tokens", None),
            },
        }
    )

    # --- parse JSON with tiny recovery ---
    if not raw:
        raise RuntimeError("LLM returned empty response text.")

    # Try direct JSON
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Try extracting first {...} block
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise RuntimeError("Failed to parse LLM response as JSON. See tech log for raw output.")


def save_final_summary_json_txt(final_json: Dict[str, Any], out_path: str | Path) -> Path:
    """
    Save final summary JSON as plain text file (UTF-8).
    """
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_safe_json_dumps(final_json) + "\n", encoding="utf-8")
    return p
