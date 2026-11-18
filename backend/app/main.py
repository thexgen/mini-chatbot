import asyncio
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import chat, documents
from app.services.document_service import reindex_pending_documents


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(reindex_pending_documents)
    yield


app = FastAPI(title="Mini RAG Chat API", version="0.1.0", lifespan=lifespan)

allowed_origins = os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
