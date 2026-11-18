from datetime import datetime, timezone
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import UPLOAD_DIR, SessionLocal
from app.models import Document
from app.schemas import DocumentResponse
from app.services.rag_service import delete_document_vectors, index_pdf

logger = logging.getLogger(__name__)


def _to_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        name=document.name,
        status=document.status,
        uploaded_at=document.uploaded_at.isoformat(),
    )


def list_documents(db: Session) -> list[DocumentResponse]:
    documents = db.scalars(select(Document).order_by(Document.uploaded_at.desc())).all()
    return [_to_response(document) for document in documents]


def reindex_pending_documents() -> None:
    with SessionLocal() as db:
        pending_documents = [
            (document.id, document.name, document.stored_name)
            for document in db.scalars(
                select(Document)
                .where(Document.status != "indexed")
                .order_by(Document.uploaded_at)
            )
        ]

    for document_id, name, stored_name in pending_documents:
        pdf_path = UPLOAD_DIR / stored_name
        if not pdf_path.is_file():
            logger.error("Cannot reindex document %s: local PDF is missing.", document_id)
            continue
        try:
            delete_document_vectors(document_id)
            index_pdf(pdf_path, document_id, name)
            with SessionLocal.begin() as db:
                document = db.get(Document, document_id)
                if document is not None:
                    document.status = "indexed"
        except Exception:
            logger.exception("Could not reindex pending document %s.", document_id)


async def save_document(file: UploadFile, db: Session) -> DocumentResponse:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    document_id = str(uuid4())
    stored_name = f"{document_id}.pdf"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIR / stored_name
    metadata_saved = False

    try:
        contents = await file.read()
        if not contents.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")
        destination.write_bytes(contents)
        document = Document(
            id=document_id,
            name=Path(file.filename).name,
            stored_name=stored_name,
            status="indexing",
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(document)
        db.commit()
        metadata_saved = True
        index_pdf(destination, document_id, document.name)
        document.status = "indexed"
        db.commit()
    except Exception as exc:
        logger.exception("Could not save and index document %s.", document_id)
        db.rollback()
        vectors_cleared = not metadata_saved or (
            isinstance(exc, HTTPException) and exc.status_code == 422
        )
        if metadata_saved and not vectors_cleared:
            try:
                delete_document_vectors(document_id)
                vectors_cleared = True
            except Exception:
                logger.exception("Could not roll back Qdrant vectors for document %s.", document_id)
        if metadata_saved and vectors_cleared:
            metadata_removed = False
            try:
                db.delete(document)
                db.commit()
                metadata_removed = True
            except Exception:
                db.rollback()
                logger.exception("Could not roll back PostgreSQL metadata for document %s.", document_id)
            if metadata_removed:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Could not remove failed upload %s.", document_id)
        elif not metadata_saved:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove failed upload %s.", document_id)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="Document upload and indexing failed.") from exc
    finally:
        await file.close()

    return _to_response(document)


def delete_document(document_id: str, db: Session) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    stored_name = document.stored_name

    delete_document_vectors(document_id)
    try:
        (UPLOAD_DIR / stored_name).unlink(missing_ok=True)
    except OSError as exc:
        logger.exception("Could not delete local PDF for document %s.", document_id)
        raise HTTPException(status_code=500, detail="Could not delete the local PDF file.") from exc

    try:
        db.delete(document)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Could not delete PostgreSQL metadata for document %s.", document_id)
        raise HTTPException(status_code=500, detail="Could not delete document metadata.") from exc

    logger.info("Deleted document %s and its vectors.", document_id)
