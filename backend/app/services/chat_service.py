from app.schemas import ChatResponse
from app.services.llm_service import generate_llm_response
from app.services.rag_service import search_similar_chunks

NO_CONTEXT_RESPONSE = "I couldn't find relevant information in the uploaded PDFs to answer that."


def get_chat_response(message: str) -> ChatResponse:
    chunks = search_similar_chunks(message)
    if not chunks:
        return ChatResponse(response=NO_CONTEXT_RESPONSE)

    context = "\n\n".join(
        f"[Source: {chunk.source}, page {chunk.page_number}]\n{chunk.text}"
        for chunk in chunks
    )
    response_text = generate_llm_response(message, context)
    return ChatResponse(response=response_text)
