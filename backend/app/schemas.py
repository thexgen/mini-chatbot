from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: str
    name: str
    status: str
    uploaded_at: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message to send to the LLM")


class ChatResponse(BaseModel):
    response: str
