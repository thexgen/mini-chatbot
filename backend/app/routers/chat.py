from fastapi import APIRouter, HTTPException

from app.schemas import ChatRequest, ChatResponse
from app.services.chat_service import get_chat_response

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        return get_chat_response(request.message)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive fallback for unexpected errors
        raise HTTPException(status_code=500, detail="Chat processing failed.") from exc
