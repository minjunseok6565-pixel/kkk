from __future__ import annotations

"""LLM writer for scouting reports.

This module is intentionally isolated so the DB/service logic can be tested
without requiring a network call. In production, report generation happens at
month-end checkpoint (Option A).

Implementation uses Google Generative AI (Gemini), matching existing project
modules (news_ai.py, season_report_ai.py).
"""

import json
from typing import Any, Dict, Tuple

import game_time

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - optional dependency in offline/local env
    genai = None


DEFAULT_MODEL_NAME = "gemini-3-pro-preview"
PROMPT_VERSION = "scouting_report_v1"


def _now_iso() -> str:
    return game_time.now_utc_like_iso()


def _extract_text_from_gemini_response(resp: Any) -> str:
    """Robustly extract plain text from gemini response."""
    # Common shape: response.text
    try:
        t = getattr(resp, "text", None)
        if isinstance(t, str) and t.strip():
            return t
    except Exception:
        pass

    # Fallback: iterate candidates/parts
    try:
        cands = getattr(resp, "candidates", None)
        if isinstance(cands, list) and cands:
            parts = getattr(cands[0].content, "parts", None)
            if isinstance(parts, list) and parts:
                texts = []
                for p in parts:
                    tx = getattr(p, "text", None)
                    if isinstance(tx, str) and tx:
                        texts.append(tx)
                if texts:
                    return "\n".join(texts)
    except Exception:
        pass

    # Ultimate fallback
    return str(resp) if resp is not None else ""


def _build_prompt(payload: Dict[str, Any]) -> str:
    """Return a Korean scouting report prompt with the structured payload."""
    # Keep JSON fairly compact to reduce tokens.
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    return (
        "당신은 NBA 구단 스카우터다. 아래의 구조화된 데이터(payload)를 바탕으로 '스카우팅 리포트'를 작성하라.\n"
        "- 출력 언어: 한국어\n"
        "- 형식: Markdown 텍스트(제목/소제목/불릿). 표(테이블)는 사용하지 말 것.\n"
        "- 길이: 700~1400자 내외(핵심만, 과도하게 길게 쓰지 말 것).\n"
        "- 데이터에 없는 사실을 단정/추가하지 말 것(특히 플레이 스타일/상대 레벨/부상/성격).\n"
        "- 숫자 0~100 점수(mu/sigma)를 그대로 노출하지 말 것. 대신 tier(특급/강점/평균권/우려/경고) + confidence(high/medium/low) + range_text를 사용.\n"
        "- evidence_tags는 반드시 근거로 활용하라. PLUS/MINUS/QUESTION/META의 kind를 존중하고, tags에 없는 근거를 만들어내지 말 것.\n"
        "- college_context.stat_line과 notes는 '문장에 넣어도 되는 유일한 박스스코어 근거'로 간주한다.\n"
        "- 이 리포트는 scout.focus_signals 중심의 부분 관찰일 수 있다. 그 점을 요약에 한 번 언급하라.\n"
        "\n"
        "반드시 포함할 섹션(순서 유지):\n"
        "1) 요약 (2~3문장)\n"
        "2) 이번 달에 확실해진 것 (delta_since_last.new_info 기반, 없으면 '큰 변화 없음/표본 부족'로)\n"
        "3) 프로스펙트 신호 카드 (signals를 group(offense/defense/physical)별로 묶고, 각 signal마다 아래 포맷)\n"
        "   - **{label}** — {tier} · 신뢰도 {confidence} · {range_text}\n"
        "     - ✅ (PLUS 근거 1~2개)\n"
        "     - ⚠️ (MINUS 근거 0~1개)\n"
        "     - ❓ (QUESTION 0~1개)\n"
        "     - 🧾 (META 0~1개; 표본/툴-생산 괴리 등)\n"
        "4) NBA 번역 (1~2문단: 어떤 역할로 쓰일지/무엇이 트리거인지/무엇이 상한을 막는지)\n"
        "5) 리스크/질문 (watchlist_questions를 불릿 2~3개로 그대로 사용)\n"
        "6) 다음 체크 포인트 (2~3개: 다음 달에 무엇을 보면 신뢰도가 오르는지)\n"
        "\n"
        "[payload JSON]\n"
        f"{payload_json}\n"
    )


class ScoutingReportWriter:
    """Thin wrapper around Gemini model to generate report_text."""

    def __init__(self, *, api_key: str, model_name: str = DEFAULT_MODEL_NAME):
        if genai is None:
            raise ImportError(
                "google.generativeai is required to generate scouting reports. "
                "Install the optional dependency to enable this feature."
            )
        if not api_key or not str(api_key).strip():
            raise ValueError("api_key is required")
        self.api_key = str(api_key).strip()
        self.model_name = str(model_name).strip() or DEFAULT_MODEL_NAME

        # Configure globally for this process. This matches other modules.
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)

    def write(self, payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        prompt = _build_prompt(payload)

        # Note: We intentionally keep generation config default for now.
        # If you need more deterministic output, set temperature=0.2 etc.
        resp = self.model.generate_content(prompt)
        text = _extract_text_from_gemini_response(resp).strip()

        meta: Dict[str, Any] = {
            "model": self.model_name,
            "prompt_version": PROMPT_VERSION,
            "generated_at": _now_iso(),
        }

        # Try to capture usage metadata if available
        try:
            usage = getattr(resp, "usage_metadata", None)
            if usage is not None:
                # Convert to JSON-serializable dict-ish
                meta["usage_metadata"] = json.loads(json.dumps(usage, default=str))
        except Exception:
            pass

        return text, meta
