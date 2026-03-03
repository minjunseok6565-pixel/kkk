from __future__ import annotations

import json
from typing import Any, Optional

import google.generativeai as genai
from fastapi import APIRouter, HTTPException

from news_ai import refresh_playoff_news, refresh_weekly_news
from season_report_ai import generate_season_report
from app.schemas.common import ApiKeyRequest, ChatMainRequest, EmptyRequest, SeasonReportRequest, WeeklyNewsRequest

router = APIRouter()









def extract_text_from_gemini_response(resp: Any) -> str:
    """google-generativeai 응답 객체에서 텍스트만 안전하게 뽑아낸다."""
    text = getattr(resp, "text", None)
    if text:
        return text

    try:
        parts = resp.candidates[0].content.parts
        texts = []
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                texts.append(t)
        if texts:
            return "\n".join(texts)
    except Exception:
        pass

    return str(resp)



@router.post("/api/news/week")
async def api_news_week(req: WeeklyNewsRequest):
    if not req.apiKey:
        raise HTTPException(status_code=400, detail="apiKey is required")
    try:
        payload = refresh_weekly_news(req.apiKey)

        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Weekly news generation failed: {e}")


@router.post("/api/news/playoffs")
async def api_playoff_news(req: EmptyRequest):
    try:
        return refresh_playoff_news()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playoff news generation failed: {e}")


@router.post("/api/season-report")
async def api_season_report(req: SeasonReportRequest):
    """정규 시즌 종료 후, LLM을 이용해 시즌 결산 리포트를 생성한다."""
    if not req.apiKey:
        raise HTTPException(status_code=400, detail="apiKey is required")

    try:
        report_text = generate_season_report(req.apiKey, req.user_team_id)
        return {"report_markdown": report_text}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Season report generation failed: {e}")


@router.post("/api/validate-key")
async def api_validate_key(req: ApiKeyRequest):
    """주어진 Gemini API 키를 간단히 검증한다."""
    if not req.apiKey:
        raise HTTPException(status_code=400, detail="apiKey is required")

    try:
        genai.configure(api_key=req.apiKey)
        # 최소 호출로 키 유효성 확인 (토큰 카운트 호출)
        model = genai.GenerativeModel("gemini-3-pro-preview")
        model.count_tokens("ping")
        return {"valid": True}
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid API key: {e}")


# -------------------------------------------------------------------------
# 메인 LLM (Home 대화) API
# -------------------------------------------------------------------------
@router.post("/api/chat-main")
async def chat_main(req: ChatMainRequest):
    """메인 프롬프트 + 컨텍스트 + 유저 입력을 가지고 Gemini를 호출."""
    if not req.apiKey:
        raise HTTPException(status_code=400, detail="apiKey is required")

    try:
        genai.configure(api_key=req.apiKey)
        model = genai.GenerativeModel(
            model_name="gemini-3-pro-preview",
            system_instruction=req.mainPrompt or "",
        )

        context_text = req.context
        if isinstance(req.context, (dict, list)):
            context_text = json.dumps(req.context, ensure_ascii=False)

        prompt = f"{context_text}\n\n[USER]\n{req.userInput}"
        resp = model.generate_content(prompt)
        text = extract_text_from_gemini_response(resp)
        return {"reply": text, "answer": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini main chat error: {e}")


@router.post("/api/main-llm")
async def chat_main_legacy(req: ChatMainRequest):
    return await chat_main(req)
