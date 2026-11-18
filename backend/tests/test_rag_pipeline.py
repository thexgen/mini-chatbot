from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from typing import cast

from fastapi import HTTPException, UploadFile
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session

from app import database
from app.schemas import ChatResponse
from app.services import chat_service, document_service, rag_service


class FakeVector(list):
    def tolist(self) -> list[float]:
        return list(self)


class FakeEmbeddingModel:
    def encode(self, texts: object, **_: object) -> list[FakeVector] | FakeVector:
        if isinstance(texts, str):
            return FakeVector([0.1, 0.2])
        return [FakeVector([0.1, 0.2]) for _ in texts]  # type: ignore[union-attr]

    def get_embedding_dimension(self) -> int:
        return 2


class FakeQdrantClient:
    def __init__(self) -> None:
        self.created_collection = False
        self.upserted_points: list[object] = []
        self.delete_args: dict[str, object] | None = None

    def collection_exists(self, _: str) -> bool:
        return self.created_collection

    def create_collection(self, **_: object) -> None:
        self.created_collection = True

    def upsert(self, *, points: list[object], **_: object) -> None:
        self.upserted_points.extend(points)

    def delete(self, **kwargs: object) -> None:
        self.delete_args = kwargs


class RagPipelineTests(unittest.TestCase):
    def test_chunking_applies_configured_overlap(self) -> None:
        with patch.dict(os.environ, {"CHUNK_SIZE": "10", "CHUNK_OVERLAP": "3"}):
            chunks = rag_service._chunk_text("abcdefghijABCDEFGHIJ")

        self.assertEqual(chunks, ["abcdefghij", "hijABCDEFG", "EFGHIJ"])

    def test_extracts_each_page_and_keeps_page_numbers(self) -> None:
        pages = [SimpleNamespace(extract_text=lambda: "first"), SimpleNamespace(extract_text=lambda: "second")]
        with patch.object(rag_service, "PdfReader", return_value=SimpleNamespace(pages=pages)):
            extracted = rag_service._extract_pdf_pages(Path("sample.pdf"))

        self.assertEqual(extracted, [(1, "first"), (2, "second")])

    def test_indexing_stores_document_and_page_metadata(self) -> None:
        client = FakeQdrantClient()
        pages = [(1, "first page text"), (2, "second page text")]
        with (
            patch.object(rag_service, "_extract_pdf_pages", return_value=pages),
            patch.object(rag_service, "_get_embedding_model", return_value=FakeEmbeddingModel()),
            patch.object(rag_service, "_get_qdrant_client", return_value=client),
            patch.dict(os.environ, {"CHUNK_SIZE": "100", "CHUNK_OVERLAP": "10"}),
        ):
            count = rag_service.index_pdf(Path("sample.pdf"), "doc-1", "sample.pdf")

        self.assertEqual(count, 2)
        first_payload = client.upserted_points[0].payload  # type: ignore[attr-defined]
        self.assertEqual(first_payload["document_id"], "doc-1")
        self.assertEqual(first_payload["page_number"], 1)
        self.assertEqual(first_payload["chunk_index"], 0)
        self.assertEqual(first_payload["source"], "sample.pdf")
        self.assertEqual(first_payload["text"], "first page text")
        self.assertEqual(client.upserted_points[1].payload["page_number"], 2)  # type: ignore[attr-defined]

    def test_search_returns_matching_source_and_page(self) -> None:
        point = SimpleNamespace(
            payload={"text": "relevant text", "source": "guide.pdf", "page_number": 4},
            score=0.82,
        )
        client = FakeQdrantClient()
        client.created_collection = True
        client.query_points = lambda **_: SimpleNamespace(points=[point])  # type: ignore[attr-defined]
        with (
            patch.object(rag_service, "_get_embedding_model", return_value=FakeEmbeddingModel()),
            patch.object(rag_service, "_get_qdrant_client", return_value=client),
            patch.dict(os.environ, {"RAG_TOP_K": "3", "RAG_SCORE_THRESHOLD": "0.35"}),
        ):
            chunks = rag_service.search_similar_chunks("question")

        self.assertEqual(
            chunks,
            [rag_service.RetrievedChunk("relevant text", "guide.pdf", 4, 0.82)],
        )

    def test_search_skips_embedding_when_collection_does_not_exist(self) -> None:
        client = FakeQdrantClient()
        with (
            patch.object(rag_service, "_get_embedding_model") as get_model,
            patch.object(rag_service, "_get_qdrant_client", return_value=client),
        ):
            chunks = rag_service.search_similar_chunks("question")

        self.assertEqual(chunks, [])
        get_model.assert_not_called()

    def test_deletion_filters_by_document_id(self) -> None:
        client = FakeQdrantClient()
        client.created_collection = True
        with patch.object(rag_service, "_get_qdrant_client", return_value=client):
            rag_service.delete_document_vectors("doc-1")

        selector = client.delete_args["points_selector"]  # type: ignore[index]
        condition = selector.filter.must[0]  # type: ignore[attr-defined]
        self.assertEqual(condition.key, "document_id")
        self.assertEqual(condition.match.value, "doc-1")

    def test_pipeline_indexes_searches_and_deletes_in_qdrant(self) -> None:
        client = QdrantClient(":memory:")
        pages = [(1, "first page text"), (2, "second page text")]
        try:
            with (
                patch.object(rag_service, "_extract_pdf_pages", return_value=pages),
                patch.object(rag_service, "_get_embedding_model", return_value=FakeEmbeddingModel()),
                patch.object(rag_service, "_get_qdrant_client", return_value=client),
                patch.dict(
                    os.environ,
                    {
                        "CHUNK_SIZE": "100",
                        "CHUNK_OVERLAP": "10",
                        "RAG_TOP_K": "5",
                        "RAG_SCORE_THRESHOLD": "0.35",
                    },
                ),
            ):
                self.assertEqual(rag_service.index_pdf(Path("sample.pdf"), "doc-1", "sample.pdf"), 2)
                retrieved = rag_service.search_similar_chunks("question")
                self.assertEqual({chunk.page_number for chunk in retrieved}, {1, 2})

                rag_service.delete_document_vectors("doc-1")
                self.assertEqual(rag_service.search_similar_chunks("question"), [])
        finally:
            client.close()

    def test_chat_does_not_call_llm_when_no_chunks_match(self) -> None:
        with (
            patch.object(chat_service, "search_similar_chunks", return_value=[]),
            patch.object(chat_service, "generate_llm_response") as generate,
        ):
            response = chat_service.get_chat_response("question")

        self.assertIsInstance(response, ChatResponse)
        self.assertIn("couldn't find relevant information", response.response)
        generate.assert_not_called()

    def test_chat_sends_retrieved_context_to_llm(self) -> None:
        chunks = [rag_service.RetrievedChunk("answer text", "guide.pdf", 4, 0.82)]
        with (
            patch.object(chat_service, "search_similar_chunks", return_value=chunks),
            patch.object(chat_service, "generate_llm_response", return_value="Grounded answer.") as generate,
        ):
            response = chat_service.get_chat_response("question")

        self.assertEqual(response.response, "Grounded answer.")
        generate.assert_called_once_with(
            "question",
            "[Source: guide.pdf, page 4]\nanswer text",
        )


class DocumentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_dependency_closes_session(self) -> None:
        class FakeSession:
            closed = False

            def close(self) -> None:
                self.closed = True

        session = FakeSession()
        with patch.object(database, "SessionLocal", return_value=session):
            dependency = database.get_db()
            self.assertIs(next(dependency), session)
            dependency.close()

        self.assertTrue(session.closed)

    async def test_upload_preserves_file_and_metadata_if_vector_rollback_fails(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.added: list[object] = []
                self.deleted: list[object] = []

            def add(self, instance: object) -> None:
                self.added.append(instance)

            def commit(self) -> None:
                pass

            def rollback(self) -> None:
                pass

            def delete(self, instance: object) -> None:
                self.deleted.append(instance)

        session = FakeSession()

        with TemporaryDirectory() as temp_dir:
            upload_dir = Path(temp_dir)
            with (
                patch.object(document_service, "UPLOAD_DIR", upload_dir),
                patch.object(
                    document_service,
                    "index_pdf",
                    side_effect=HTTPException(status_code=503, detail="index failed"),
                ),
                patch.object(
                    document_service,
                    "delete_document_vectors",
                    side_effect=HTTPException(status_code=503, detail="rollback failed"),
                ),
            ):
                upload = UploadFile(filename="sample.pdf", file=BytesIO(b"%PDF-test"))
                with self.assertRaises(HTTPException):
                    await document_service.save_document(upload, cast(Session, session))

            self.assertEqual(len(list(upload_dir.glob("*.pdf"))), 1)
            self.assertEqual(len(session.added), 1)
            self.assertEqual(session.deleted, [])


if __name__ == "__main__":
    unittest.main()
