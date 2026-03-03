from __future__ import annotations

import json
import logging
from typing import Any, Dict

from ..models import NewsArticle

logger = logging.getLogger(__name__)


def _extract_text(resp: Any) -> str:
    text = getattr(resp, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    try:
        parts = resp.candidates[0].content.parts
        out = []
        for p in parts:
            t = getattr(p, "text", None)
            if isinstance(t, str) and t:
                out.append(t)
        if out:
            return "\n".join(out)
    except Exception:
        pass
    return str(resp)


def _safe_json_load(text: str) -> Dict[str, Any] | None:
    s = (text or "").strip()
    if s.startswith("```"):
        chunks = s.split("```")
        if len(chunks) >= 3:
            s = chunks[1].strip()
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _facts_sanity_guard(original: NewsArticle, rewritten: NewsArticle) -> bool:
    """Reject rewrites that appear to hallucinate facts.

    We keep this intentionally lightweight:
    - do not allow unrelated team IDs to appear
    - keep the original title if rewritten is empty
    """
    try:
        orig_teams = set(map(str, original.get("related_team_ids") or []))
        new_teams = set(map(str, rewritten.get("related_team_ids") or []))
        if new_teams and not new_teams.issubset(orig_teams):
            return False
    except Exception:
        return False

    if not str(rewritten.get("title") or "").strip():
        return False
    if not str(rewritten.get("summary") or "").strip():
        return False
    return True


def rewrite_article_with_gemini(
    api_key: str,
    article: NewsArticle,
    *,
    tone: str = "broadcast",
    model_name: str = "gemini-3-pro-preview",
) -> NewsArticle:
    """Rewrite an article in Korean while preserving facts.

    If Gemini is unavailable or returns invalid output, returns the original article.
    """
    if not api_key:
        return article

    try:
        import google.generativeai as genai
    except Exception:
        return article

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)

        prompt = {
            "task": "rewrite",
            "constraints": {
                "language": "ko",
                "tone": tone,
                "do_not_invent": True,
                "max_summary_words": 70,
            },
            "input": {
                "title": article.get("title"),
                "summary": article.get("summary"),
                "tags": article.get("tags"),
                "related_team_ids": article.get("related_team_ids"),
                "related_player_names": article.get("related_player_names"),
            },
            "output_format": {
                "title": "string",
                "summary": "string",
            },
        }

        resp = model.generate_content(
            "Rewrite the following JSON's title and summary. "
            "Return ONLY a JSON object with keys: title, summary.\n" + json.dumps(prompt, ensure_ascii=False)
        )
        raw = _extract_text(resp)
        obj = _safe_json_load(raw)
        if not obj:
            return article

        rewritten: NewsArticle = dict(article)
        rewritten["title"] = str(obj.get("title") or article.get("title") or "").strip()
        rewritten["summary"] = str(obj.get("summary") or article.get("summary") or "").strip()

        if not _facts_sanity_guard(article, rewritten):
            return article

        return rewritten
    except Exception as exc:
        logger.warning("Gemini rewrite failed: %s", exc, exc_info=True)
        return article
