from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DocumentResponse
from app.services import document_service

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> DocumentResponse:
    return await document_service.save_document(file, db)


@router.get("", response_model=list[DocumentResponse])
def get_documents(db: Session = Depends(get_db)) -> list[DocumentResponse]:
    return document_service.list_documents(db)


@router.delete("/{document_id}", status_code=204)
def remove_document(document_id: str, db: Session = Depends(get_db)) -> None:
    document_service.delete_document(document_id, db)
