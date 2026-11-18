# Mini RAG Chat - Project ki End-to-End Guide

> **Simple summary:** Yeh ek web application hai jahan user PDF upload karta hai aur us PDF ke content ke baare mein sawaal poochta hai. App PDF ko padhkar uske chhote text parts ka semantic index banata hai. Sawaal aane par woh matching parts dhoondhta hai aur unhe NVIDIA ke language model ko dekar jawab banwata hai.

Is guide mein project ka architecture, data flow, embedding aur search ka kaam, API request/response, storage, configuration, limitations aur local run karne ka tareeqa explain kiya gaya hai. Yeh overview repository ke current implementation ko describe karta hai.

## 1. Project ka purpose

Normal chatbot apni general training ya model ke paas pehle se maujood knowledge se jawab deta hai. Is project ka chatbot pehle user ke uploaded PDFs mein relevant text dhoondhta hai, phir us text ko jawab ke liye language model ko deta hai.

Is pattern ko **RAG** kehte hain:

- **R - Retrieval:** sawaal se milte-julte PDF text chunks dhoondhna.
- **A - Augmented:** dhoonde gaye text ko sawaal ke saath model ko dena.
- **G - Generation:** model se us context ke aadhar par readable jawab banwana.

Isliye model ko poora PDF har sawaal ke saath nahi bheja jata. PDF pehle index hota hai; har naye sawaal par sirf kuch relevant chunks retrieve hote hain.

## 2. Badi tasveer: kaun kisse baat karta hai?

```text
Browser (React + TypeScript)
  |
  | HTTP: JSON ya multipart/form-data
  v
FastAPI backend (localhost:8000)
  |                         \
  | PDF aur metadata         \ Embedding / semantic search
  v                           v
Local uploads/ + SQLite      Qdrant (localhost:6333)
                                ^
                                | vectors aur chunk text
                                |
                        sentence-transformers

Chat ke waqt:
FastAPI -> NVIDIA API (OpenAI-compatible chat completions) -> FastAPI -> Browser
```

Is application ke alag parts ki zimmedari:

1. **Frontend** upload form, document list aur chat UI dikhata hai.
2. **FastAPI** HTTP requests leta hai, validation aur response models sambhalta hai.
3. **Local filesystem** original PDF files rakhta hai.
4. **SQLite** document ki basic details aur indexing status rakhta hai.
5. **Sentence Transformers** PDF chunks aur sawaal ko vectors/embeddings mein badalta hai.
6. **Qdrant** vectors aur unke saath chunk metadata rakhta hai aur nearest matches deta hai.
7. **NVIDIA hosted model** retrieved context se natural-language answer banata hai.

Frontend, backend, Qdrant aur NVIDIA API alag processes/services hain. Frontend se backend tak browser HTTP call karta hai. Backend se Qdrant tak Qdrant client call karta hai. NVIDIA API call backend ke OpenAI SDK client se hoti hai.

## 3. Repository mein kya kahan hai?

```text
PROJECT_OVERVIEW.md             Yeh detailed project guide
README.md                       Setup aur short reference
docker-compose.yml              Qdrant service ki local configuration

backend/
  .env.example                  Environment variables ka template
  requirements.txt              Python dependencies
  app/
    main.py                     FastAPI app, startup, CORS, routes aur health check
    database.py                 SQLite connection, database path aur table setup
    schemas.py                  API request/response ke Pydantic models
    routers/
      chat.py                   POST /api/chat endpoint
      documents.py              Upload/list/delete document endpoints
    services/
      chat_service.py           Retrieval aur LLM ko jodne wala chat flow
      document_service.py       Upload, listing, deletion aur restart recovery
      rag_service.py            PDF extraction, chunking, embeddings aur Qdrant
      llm_service.py            NVIDIA API configuration, request aur error mapping
  data/
    documents.db                SQLite document metadata
    uploads/                    Uploaded PDF files
    qdrant/                      Docker ke local Qdrant data ka volume

frontend/
  index.html                    Browser document shell
  src/
    main.tsx                    React app ka entry point
    App.tsx                      Chat, upload aur document-list UI + HTTP calls
    styles.css                   UI styling
  package.json                  Frontend dependencies aur scripts
  vite.config.ts                Vite + React setup
```

## 4. Application start hone par kya hota hai?

### Backend startup

`backend/app/main.py` FastAPI application banata hai. Startup ke `lifespan` flow mein:

1. `initialize_database()` SQLite mein `documents` table banata hai agar pehle se na ho.
2. `reindex_pending_documents()` un records ko dhoondhta hai jinka `status` `indexed` nahi hai.
3. Har pending record ka local PDF milne par uske purane Qdrant vectors saaf kiye jate hain, PDF dobara index hota hai aur successful hone par status `indexed` hota hai.
4. Missing PDF ya indexing failure log hoti hai; backend startup flow aage chalta hai.

Reindex karna adhure upload/indexing ko server restart ke baad recover karne mein madad karta hai. Iska matlab yeh nahi ki har indexed PDF har startup par dobara process hota hai.

FastAPI CORS mein default se `http://localhost:5173` aur `http://127.0.0.1:5173` allowed hain. `FRONTEND_ORIGINS` se list badli ja sakti hai. `/health` seedha `{"status":"ok"}` deta hai; yeh endpoint Qdrant ya NVIDIA API ki readiness ka alag health test nahi karta.

### Frontend startup

`frontend/src/main.tsx` React app ko `index.html` ke `root` element mein render karta hai. `App.tsx` ka default API base URL `http://localhost:8000` hai. Frontend environment mein `VITE_API_URL` set karke ise badla ja sakta hai.

UI mount hote hi documents lane ke liye `GET /api/documents` chalta hai. Chat messages frontend React state mein hote hain; backend unhe conversation history ke roop mein store ya agle sawaal ke saath bhejta nahi hai.

## 5. PDF upload: request se Qdrant tak

### Browser se upload request

UI mein “Add a PDF” se ek file select hoti hai. `App.tsx`:

1. File ko `FormData` mein `file` naam ke field ke saath rakhta hai.
2. `POST /api/documents` ko `multipart/form-data` request bhejta hai.
3. Safalta par returned document ko UI list ke aage add karta hai.
4. Error par backend ka `detail` message dikhata hai.

Browser request ka conceptual form:

```http
POST /api/documents
Content-Type: multipart/form-data

file = <PDF file>
```

Backend response `201 Created` hoti hai:

```json
{
  "id": "server-generated-uuid",
  "name": "guide.pdf",
  "status": "indexed",
  "uploaded_at": "UTC timestamp"
}
```

### Backend validation aur local save

`routers/documents.py` request ko `document_service.save_document()` tak bhejta hai. Service:

1. Filename ka extension `.pdf` hona check karta hai.
2. File bytes padhta hai aur shuruat mein `%PDF-` signature check karta hai.
3. Naya UUID banata hai. Disk par original filename ke bajaye `<uuid>.pdf` naam se save karta hai; original naam metadata/source citation ke liye rakhta hai.
4. SQLite `documents` table mein ID, original naam, stored filename, status `indexing`, aur UTC upload time save karta hai.
5. PDF ko index karta hai.
6. Indexing safal ho to SQLite status `indexed` karta hai. Tabhi upload response return hota hai.

Yaani current implementation mein upload endpoint file ko receive karne ke baad indexing bhi synchronously complete karta hai; user ko accepted-but-not-yet-indexed result nahi milta.

### PDF text extraction

`rag_service.py` ka `pypdf.PdfReader` PDF pages ko padhta hai. Har page se text alag extract hota hai aur page number 1 se count hota hai.

- Agar PDF se koi bhi text nahi nikalta, request `422` error hoti hai. Yeh aam taur par scanned/image-only PDF ke saath hota hai.
- Project mein OCR nahi hai; image mein likhe text ko yeh flow nahi padh sakta.
- PDF signature check file format ki basic jaanch hai. Actual parsing `pypdf` karta hai aur unreadable PDF par error aata hai.

### Text chunking: bade page ko chhote parts banana

Har page ka extracted text pehle whitespace normalize karta hai: extra spaces, tabs aur line breaks ko ek space mein badalta hai. Phir page ko character-count ke hisaab se chunks mein baanta hai.

Default values:

- `CHUNK_SIZE=1000`: har chunk lagbhag 1000 characters tak.
- `CHUNK_OVERLAP=150`: agle chunk mein pichhle chunk ke aakhri 150 characters dobara shamil.

Overlap ka faayda: agar ek relevant sentence chunk boundary ke paas ho, to uske aas-paas ka context poori tarah alag chunks mein tootne ka risk kam hota hai. Chunking page ke andar hoti hai; chunks apne page number ko saath rakhte hain. Yeh **characters** par based hai, model tokens par nahi.

### Embeddings kaise banti hain?

Embedding text ka numerical vector representation hota hai. Seedhe shabdon mein, model text ka meaning numbers mein encode karta hai. Similar meaning wale text ke vectors aam taur par vector space mein paas hote hain, chahe unmein exact same words na hon.

Is project mein:

- Model: `sentence-transformers/all-MiniLM-L6-v2`.
- PDF ke chunks ke vectors indexing waqt bante hain.
- Sawaal ka vector chat ke waqt isi model se banta hai.
- `normalize_embeddings=True` vectors ko normalize karta hai.
- Model process ke andar `lru_cache` se ek baar load hokar reuse hota hai.
- Pehli baar model ki files download/cache ho sakti hain; local machine par CPU/GPU availability ke hisaab se inference chalta hai.
- Code configured model name ko isi supported model se match karna enforce karta hai; doosra `EMBEDDING_MODEL` dena abhi supported nahi hai.

Yeh embedding model **search ke liye** hai. Answer likhne wala alag NVIDIA model hai. Dono ko ek hi model samajhna nahi chahiye.

### Qdrant mein kya store hota hai?

Har chunk ke liye Qdrant point mein:

- vector (embedding),
- `document_id`,
- `page_number`,
- page ke andar `chunk_index`,
- original `source` filename,
- chunk ka actual `text`

payload ke roop mein rakhe jate hain. Point ID deterministic UUID hota hai, jo document ID, page aur chunk index se banta hai. Isse same document ko dobara index karte waqt uske points ko pehchanna aur overwrite karna aasaan hota hai.

Default Qdrant collection `mini_chat_documents` hai. Collection zarurat par create hoti hai; vector size embedding model se liya jata hai aur distance `COSINE` hai. Points 64 ke batches mein upsert hote hain. Embeddings model encode call mein 32 ke batch size se calculate hote hain.

Qdrant host URL default `http://localhost:6333` hai. `docker-compose.yml` Qdrant container ka port expose karta hai aur `backend/data/qdrant` ko persistent storage volume banata hai.

## 6. Chat: sawaal se jawab tak ka poora request/response flow

### Step 1: Browser request banata hai

User message bhejne par `App.tsx` whitespace trim karta hai, user message chat UI mein dikhata hai, aur yeh request bhejta hai:

```http
POST /api/chat
Content-Type: application/json
```

```json
{
  "message": "Document mein leave policy kya hai?"
}
```

UI empty message submit nahi karta. Backend ka `ChatRequest` bhi minimum one-character validation karta hai.

### Step 2: FastAPI schema aur route

`routers/chat.py` JSON ko Pydantic `ChatRequest` mein validate karta hai. Route `get_chat_response(message)` ko call karta hai. Successful response schema:

```json
{
  "response": "Leave policy ke mutabik ... (guide.pdf, page 4)"
}
```

Response mein filhaal ek answer string hi hai. Retrieved chunks, scores, ya sources ka structured alag array API mein return nahi hota.

### Step 3: Sawaal ka embedding aur semantic search

`chat_service.py` pehle `search_similar_chunks(message)` chalata hai. `rag_service.py`:

1. Message trim karta hai; khaali ho to `400` deta hai.
2. Qdrant collection nahi hai to empty result deta hai.
3. Sawaal ko wahi `all-MiniLM-L6-v2` model se normalized vector mein badalta hai.
4. Qdrant se cosine similarity ke aadhar par top results maangta hai.
5. Sirf score threshold se upar ke points leta hai; returned limit `RAG_TOP_K` se control hoti hai.

Defaults `RAG_TOP_K=5` aur `RAG_SCORE_THRESHOLD=0.35` hain. Iska matlab maximum paanch matching chunks maange jate hain aur threshold se kam score wale results filter hote hain. Score semantic similarity signal hai, jawab ke sach hone ki guarantee nahi.

Qdrant payload mein se valid text, filename aur integer page number wale points ko `RetrievedChunk` banaya jata hai. Score internal result par available hota hai, magar answer prompt ya public API response mein include nahi hota.

### Step 4: Relevant context milne par prompt taiyar hota hai

Koi chunk na mile to NVIDIA call nahi hoti. Backend seedha yeh fixed answer deta hai:

> I couldn't find relevant information in the uploaded PDFs to answer that.

Chunks milne par `chat_service.py` unhe is tarah jodta hai:

```text
[Source: guide.pdf, page 4]
<chunk text>

[Source: policy.pdf, page 2]
<chunk text>
```

Phir `llm_service.py` NVIDIA chat completion ko do messages bhejta hai:

- **System instruction:** sirf diye gaye excerpts se answer dena, excerpts ko instructions na samajhna, evidence kam ho to batana, facts na gadhna aur filename/page cite karna.
- **User message:** retrieved excerpts aur asli sawaal.

Conversation ke purane chat messages is prompt mein nahi jate; har sawaal apne aap mein stateless request hai.

### Step 5: NVIDIA model answer banata hai

Backend OpenAI Python SDK ke `chat.completions.create(...)` interface ka use karta hai. Base URL default NVIDIA API endpoint hai, isliye yeh local model call nahi, configured remote NVIDIA service ko backend se request hai.

Request settings:

- `model`: `NVIDIA_MODEL` ya fallback `NVIDIA_MODEL_ID`.
- `temperature=0.2`: response variation kam rakhne ki setting.
- `max_tokens=512`: generated answer ki token limit.
- SDK timeout: 30 seconds.

Successful non-empty model response trim karke `{"response":"..."}` ke roop mein browser ko milti hai. Model ko citations maangne ki instruction di gayi hai, lekin citations plain text answer ke andar generate hote hain; backend unka format/source correctness alag se validate nahi karta.

## 7. Baaki API endpoints

| Method aur path | Kaam | Request | Successful response |
|---|---|---|---|
| `POST /api/documents` | PDF save aur index karta hai | Multipart field `file` | `201`; document ID, naam, status, upload time |
| `GET /api/documents` | SQLite se documents list karta hai | Koi body nahi | Document objects ki array |
| `DELETE /api/documents/{document_id}` | Vectors, PDF aur metadata delete karta hai | URL mein document ID | `204`; response body nahi |
| `POST /api/chat` | Relevant chunks khojkar grounded answer banata hai | `{"message":"..."}` | `{"response":"..."}` |
| `GET /health` | Basic app health batata hai | Koi body nahi | `{"status":"ok"}` |

`GET /api/documents` documents ko `uploaded_at` ke descending order mein deta hai. Ismein PDF content ya Qdrant chunks nahi aate.

### Delete ka order

Document delete karne par service pehle SQLite se document ka stored filename dhoondhti hai. ID na ho to `404`. Uske baad:

1. Qdrant se us `document_id` ke vectors delete.
2. Local PDF file delete.
3. SQLite metadata record delete.

Yeh teen alag storage systems hain, single cross-system transaction nahi. Agar beech ke step mein error aaye to operation error ke saath ruk sakta hai; logs mein detail likhi jati hai.

## 8. Data kahan rehta hai?

| Data | Jagah | Kya rakha jata hai? |
|---|---|---|
| Original PDF | `backend/data/uploads/<uuid>.pdf` | Uploaded file bytes |
| Document metadata | `backend/data/documents.db` | ID, original filename, stored filename, status, upload timestamp |
| Chunk text aur embeddings | Qdrant collection | Vector + document/page/chunk/source metadata aur chunk text |
| Current chat messages | Browser memory/React state | Current page session ke visible messages; reload par reset |
| NVIDIA credentials/config | Backend environment (`backend/.env`) | API key aur model/config values; secret ko source/docs mein copy nahi karna |

SQLite aur Qdrant ek hi cheez store nahi karte: SQLite document list ke liye hai; Qdrant semantic retrieval ke liye hai; PDF files original document hain.

## 9. Important configuration

Backend values `backend/.env.example` mein template ke roop mein hain. Actual credentials `backend/.env` mein rakhein; API key ko commit ya documentation mein paste na karein.

| Variable | Default | Kaam |
|---|---|---|
| `NVIDIA_API_KEY` | unset | NVIDIA API authentication; zaroori |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA-compatible API base URL |
| `NVIDIA_MODEL` | example file mein configured | NVIDIA catalog ka exact model ID; zaroori |
| `NVIDIA_MODEL_ID` | unset | `NVIDIA_MODEL` ke bajaye fallback model variable |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_API_KEY` | empty | Qdrant ko authentication chahiye to key |
| `QDRANT_COLLECTION` | `mini_chat_documents` | Vector collection ka naam |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model; code filhaal isi naam ko allow karta hai |
| `CHUNK_SIZE` | `1000` | Chunk length, characters mein |
| `CHUNK_OVERLAP` | `150` | Consecutive chunks ka repeated overlap, characters mein |
| `RAG_TOP_K` | `5` | Search se maange jane wale maximum chunks |
| `RAG_SCORE_THRESHOLD` | `0.35` | Minimum cosine score |
| `FRONTEND_ORIGINS` | `localhost:5173` aur `127.0.0.1:5173` | CORS mein allowed browser origins |
| `VITE_API_URL` | `http://localhost:8000` | Frontend ka backend base URL |

Chunk size/top-k positive integer hone chahiye. Overlap zero ya usse bada nahi ho sakta, aur chunk size se chhota hona chahiye. Similarity threshold `-1` se `1` ke beech validate hota hai. Galat config par indexing/search error log hoti hai aur request fail hoti hai.

## 10. Errors aur failure behavior

- **400:** filename `.pdf` nahi, PDF signature invalid, ya message khaali.
- **404:** delete kiya gaya document ID nahi mila.
- **422:** PDF read nahi hua ya extractable text nahi mila (jaise OCR-required scanned PDF).
- **500:** unexpected chat error, NVIDIA config missing, ya document upload/indexing ke dauran unexpected failure.
- **502:** NVIDIA API tak pahunch nahi hui, NVIDIA API error, ya empty model response.
- **503:** Qdrant/embedding semantic search ya document indexing unavailable.
- **504:** NVIDIA request timeout.
- **429:** NVIDIA API rate limit.

Upload indexing fail hone par service jitna safely possible ho Qdrant points, SQLite row aur saved file ko rollback karne ki koshish karti hai. Unexpected backend errors log hote hain aur frontend ko readable error dikhaya jata hai.

## 11. Technologies aur unka role

### Frontend

- **React:** component-based UI aur state management.
- **TypeScript:** frontend types, jaise document/chat message shapes.
- **Vite:** development server aur production bundle.
- **Fetch API:** backend ke HTTP requests.

### Backend

- **FastAPI:** routes, HTTP handling aur interactive API schema/docs.
- **Pydantic:** request/response validation (`schemas.py`).
- **SQLite:** chhota local metadata database; server code standard `sqlite3` use karta hai.
- **pypdf:** PDF se page text extraction.
- **Sentence Transformers:** local semantic embeddings.
- **Qdrant client:** vector collection, upsert, nearest search aur vector deletion.
- **OpenAI Python SDK:** NVIDIA ke OpenAI-compatible chat completions endpoint ko call karna.
- **python-dotenv:** backend `.env` se configuration load karna.
- **Docker Compose:** Qdrant ko local container mein chalana.

## 12. Local machine par run karna

Requirements: README ke mutabik Node.js `20.19+` (ya `22.12+`) aur Python `3.10+`.

### Qdrant

Project root se:

```bash
docker compose up -d qdrant
```

### Backend

```bash
cp backend/.env.example backend/.env
# backend/.env mein NVIDIA_API_KEY aur NVIDIA_MODEL set karein
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API `http://localhost:8000` par aur interactive docs `http://localhost:8000/docs` par milti hain. Pehli baar embedding model ki files download ho sakti hain.

### Frontend

Doosre terminal mein:

```bash
cd frontend
npm install
npm run dev
```

Vite dwara dikhaya gaya local URL (aam taur par `http://localhost:5173`) browser mein kholein.

## 13. Is version ki boundaries

- Sirf PDF upload flow bana hai; DOCX, images aur doosre formats supported nahi.
- Scanned/image-only PDFs par OCR nahi chalta.
- Koi user login/authentication ya per-user document isolation nahi hai.
- Chat history backend/database mein persist nahi hoti; page reload par UI conversation reset hoti hai.
- Backend sirf retrieved excerpts ko prompt mein deta hai, poori PDF nahi. Isliye weak retrieval, low threshold, PDF extraction quality, ya chunk boundary answer ko prabhavit kar sakte hain.
- LLM ko grounded answer aur citations ke liye instruct kiya jata hai, lekin generated prose/citations ki automatic factual verification nahi hoti.
- Frontend document status dikhata hai, lekin normal upload tabhi complete hota hai jab indexing ho chuki ho.
- `/health` basic FastAPI health hai, dependency health check nahi.
- Data local machine ke filesystem, SQLite aur Qdrant volume par nirbhar hai; backup/restore ka alag workflow is project mein nahi diya gaya.

## 14. Ek line mein end-to-end recap

**PDF upload -> local file + SQLite record -> page-wise text extraction -> overlapping chunks -> local embedding vectors -> Qdrant index -> user question embedding -> Qdrant se top matching chunks -> excerpts + question NVIDIA model ko -> answer browser ko.**
