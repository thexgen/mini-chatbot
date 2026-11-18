from dataclasses import dataclass
from functools import lru_cache
import logging
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from fastapi import HTTPException
from pypdf import PdfReader
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logger = logging.getLogger(__name__)
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_COLLECTION_NAME = "mini_chat_documents"
QDRANT_BATCH_SIZE = 64


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source: str
    page_number: int
    score: float


def _positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def _chunk_text(text: str) -> list[str]:
    chunk_size = _positive_int_setting("CHUNK_SIZE", 1000)
    overlap = int(os.getenv("CHUNK_OVERLAP", "150"))
    if overlap < 0 or overlap >= chunk_size:
        raise RuntimeError("CHUNK_OVERLAP must be non-negative and smaller than CHUNK_SIZE.")

    normalized_text = " ".join(text.split())
    chunks: list[str] = []
    start = 0
    while start < len(normalized_text):
        end = min(start + chunk_size, len(normalized_text))
        chunk = normalized_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(normalized_text):
            break
        start = end - overlap
    return chunks


def _extract_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    try:
        reader = PdfReader(str(pdf_path))
        pages = [
            (page_number, page.extract_text() or "")
            for page_number, page in enumerate(reader.pages, start=1)
        ]
    except Exception as exc:
        logger.exception("Could not extract text from PDF %s.", pdf_path.name)
        raise HTTPException(status_code=422, detail="Could not read the uploaded PDF.") from exc

    if not any(text.strip() for _, text in pages):
        raise HTTPException(
            status_code=422,
            detail="No extractable text was found in the PDF. Scanned PDFs require OCR.",
        )
    return pages


@lru_cache(maxsize=1)
def _get_embedding_model() -> SentenceTransformer:
    model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
    if model_name != DEFAULT_EMBEDDING_MODEL:
        raise RuntimeError(f"EMBEDDING_MODEL must be {DEFAULT_EMBEDDING_MODEL}.")
    return SentenceTransformer(model_name)


@lru_cache(maxsize=1)
def _get_qdrant_client() -> QdrantClient:
    url = os.getenv("QDRANT_URL", "http://localhost:6333").strip()
    if not url:
        raise RuntimeError("QDRANT_URL must not be empty.")
    api_key = os.getenv("QDRANT_API_KEY", "").strip() or None
    return QdrantClient(url=url, api_key=api_key, timeout=10.0)


def _collection_name() -> str:
    return os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME).strip() or DEFAULT_COLLECTION_NAME


def _ensure_collection(client: QdrantClient, vector_size: int) -> None:
    collection_name = _collection_name()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )


def index_pdf(pdf_path: Path, document_id: str, source_name: str) -> int:
    try:
        pages = _extract_pdf_pages(pdf_path)
        chunks = [
            {
                "page_number": page_number,
                "chunk_index": chunk_index,
                "text": chunk,
            }
            for page_number, page_text in pages
            for chunk_index, chunk in enumerate(_chunk_text(page_text))
        ]
        if not chunks:
            raise HTTPException(
                status_code=422,
                detail="No extractable text was found in the PDF. Scanned PDFs require OCR.",
            )

        model = _get_embedding_model()
        vectors = model.encode(
            [chunk["text"] for chunk in chunks],
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vector_size = model.get_embedding_dimension()
        if vector_size is None:
            raise RuntimeError("The embedding model did not report its vector dimension.")

        client = _get_qdrant_client()
        _ensure_collection(client, vector_size)
        collection_name = _collection_name()
        points = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"{document_id}:{chunk['page_number']}:{chunk['chunk_index']}")),
                vector=vector.tolist(),
                payload={
                    "document_id": document_id,
                    "page_number": chunk["page_number"],
                    "chunk_index": chunk["chunk_index"],
                    "source": source_name,
                    "text": chunk["text"],
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for offset in range(0, len(points), QDRANT_BATCH_SIZE):
            client.upsert(
                collection_name=collection_name,
                points=points[offset : offset + QDRANT_BATCH_SIZE],
                wait=True,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Indexing failed for document %s.", document_id)
        raise HTTPException(
            status_code=503,
            detail="Document indexing failed. Check the embedding model and Qdrant configuration.",
        ) from exc

    logger.info("Indexed %d chunks for document %s.", len(chunks), document_id)
    return len(chunks)


def delete_document_vectors(document_id: str) -> None:
    try:
        client = _get_qdrant_client()
        collection_name = _collection_name()
        if not client.collection_exists(collection_name):
            return
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        )
                    ]
                )
            ),
            wait=True,
        )
    except Exception as exc:
        logger.exception("Could not delete vectors for document %s.", document_id)
        raise HTTPException(
            status_code=503,
            detail="Could not remove the document's vectors from Qdrant.",
        ) from exc


def search_similar_chunks(query: str) -> list[RetrievedChunk]:
    query_text = query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    try:
        client = _get_qdrant_client()
        collection_name = _collection_name()
        if not client.collection_exists(collection_name):
            return []

        model = _get_embedding_model()
        vector = model.encode(
            query_text,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        top_k = _positive_int_setting("RAG_TOP_K", 5)
        score_threshold = float(os.getenv("RAG_SCORE_THRESHOLD", "0.35"))
        if not -1.0 <= score_threshold <= 1.0:
            raise RuntimeError("RAG_SCORE_THRESHOLD must be between -1 and 1.")
        results = client.query_points(
            collection_name=collection_name,
            query=vector.tolist(),
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Semantic search failed.")
        raise HTTPException(
            status_code=503,
            detail="Semantic search is unavailable. Check the embedding model and Qdrant configuration.",
        ) from exc

    chunks: list[RetrievedChunk] = []
    for point in results.points:
        payload = point.payload or {}
        text = payload.get("text")
        source = payload.get("source")
        page_number = payload.get("page_number")
        if isinstance(text, str) and isinstance(source, str) and isinstance(page_number, int):
            chunks.append(
                RetrievedChunk(
                    text=text,
                    source=source,
                    page_number=page_number,
                    score=float(point.score),
                )
            )
    logger.info("Retrieved %d relevant chunks.", len(chunks))
    return chunks
