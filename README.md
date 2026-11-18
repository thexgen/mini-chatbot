# Mini RAG Chat

A React + TypeScript frontend and FastAPI backend for uploading PDFs and asking questions grounded in their contents. PDF metadata is stored in PostgreSQL, files are stored locally, and chunks are embedded with `sentence-transformers/all-MiniLM-L6-v2` and indexed in Qdrant. NVIDIA Nemotron generates answers from the retrieved excerpts.

## Requirements

- Node.js 20.19+ (or 22.12+)
- Python 3.10+

## Run the backend

Start PostgreSQL and Qdrant from the project root (PostgreSQL runs in Docker; no host installation is needed):

```bash
docker compose up -d postgres qdrant
docker compose exec postgres pg_isready -U mini_chatbot -d mini_chatbot
```

Create and configure the backend environment file (database credentials and API keys belong in `backend/.env`):

```bash
cp backend/.env.example backend/.env
# Set DATABASE_URL to postgresql+psycopg2://mini_chatbot:mini_chatbot_dev@localhost:5433/mini_chatbot.
# Set NVIDIA_API_KEY in backend/.env.
```

Then install dependencies, apply the schema migration, and start FastAPI:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API starts at <http://localhost:8000>. Interactive API docs are available at <http://localhost:8000/docs>. Uploaded PDFs and the local Qdrant data are stored under `backend/data/`; relational metadata is stored in PostgreSQL. PostgreSQL uses host port `5433` to avoid conflict with an existing service on `5432`; its data persists in the `postgres_data` Docker volume. The embedding model is downloaded the first time it is needed.

### RAG settings

Set these optional values in `backend/.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_API_KEY` | empty | Qdrant API key, if required |
| `QDRANT_COLLECTION` | `mini_chat_documents` | Collection for PDF chunks |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `CHUNK_SIZE` | `1000` | Chunk length in characters |
| `CHUNK_OVERLAP` | `150` | Overlap in characters |
| `RAG_TOP_K` | `5` | Maximum retrieved chunks per question |
| `RAG_SCORE_THRESHOLD` | `0.35` | Minimum cosine-similarity score |

Uploaded PDFs are extracted page by page, chunked, embedded, and indexed before the upload request succeeds. Existing documents not yet indexed are retried when the backend starts. Scanned/image-only PDFs are rejected because OCR is not included.

## Run the frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL (usually <http://localhost:5173>). The frontend uses `http://localhost:8000` by default. To use a different backend URL, set `VITE_API_URL` in a frontend `.env` file.

## API routes

- `POST /api/documents` — upload a PDF (`multipart/form-data`, field `file`)
- `GET /api/documents` — list uploaded documents
- `DELETE /api/documents/{document_id}` — delete an uploaded document
- `POST /api/chat` — retrieve relevant PDF chunks and generate a grounded answer (`{"message": "..."}`)
- `GET /health` — health check

When search finds no sufficiently similar chunk, chat explicitly reports that the uploaded PDFs do not contain a relevant answer. Returned answers are instructed to cite source filenames and page numbers.




docker compose up -d postgres qdrant
docker compose exec postgres pg_isready -U mini_chatbot -d mini_chatbot

