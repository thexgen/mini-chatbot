import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type DocumentItem = {
  id: string;
  name: string;
  status: string;
  uploaded_at: string;
};

type Message = {
  role: "assistant" | "user";
  content: string;
};

async function readError(response: Response): Promise<string> {
  try {
    const body: { detail?: string } = await response.json();
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function App() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Hi! Upload a PDF and ask a question. Answers are based on relevant PDF excerpts.",
    },
  ]);
  const [input, setInput] = useState("");
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  async function loadDocuments() {
    setLoadingDocuments(true);
    try {
      const response = await fetch(`${API_URL}/api/documents`);
      if (!response.ok) throw new Error(await readError(response));
      setDocuments((await response.json()) as DocumentItem[]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load documents.");
    } finally {
      setLoadingDocuments(false);
    }
  }

  useEffect(() => {
    void loadDocuments();
  }, []);

  async function uploadDocument(file: File) {
    setUploading(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`${API_URL}/api/documents`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) throw new Error(await readError(response));
      const document = (await response.json()) as DocumentItem;
      setDocuments((current) => [document, ...current]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not upload the PDF.");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function deleteDocument(document: DocumentItem) {
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/documents/${document.id}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await readError(response));
      setDocuments((current) => current.filter((item) => item.id !== document.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete the document.");
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = input.trim();
    if (!content || sending) return;

    setMessages((current) => [...current, { role: "user", content }]);
    setInput("");
    setSending(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: content }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as { response: string };
      setMessages((current) => [...current, { role: "assistant", content: result.response }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not send your message.");
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Mini RAG Chat home">
          <span className="brand-mark">r</span>
          <span>paper<span className="brand-light">trail</span></span>
        </a>
        <span className="topbar-note">Your documents, in conversation</span>
      </header>

      <div className="workspace">
        <section className="chat-panel" aria-label="Chat">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">YOUR ASSISTANT</p>
              <h1>Chat</h1>
            </div>
            <span className="connection-indicator"><i /> Ready</span>
          </div>

          <div className="message-list" aria-live="polite">
            {messages.map((message, index) => (
              <div className={`message-row ${message.role}`} key={`${message.role}-${index}`}>
                {message.role === "assistant" && <span className="assistant-avatar">r</span>}
                <div className="message-bubble">
                  {message.role === "assistant" ? (
                    <>
                      <div className="answer-heading">Answer</div>
                      <div className="markdown-content">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                      </div>
                    </>
                  ) : message.content}
                </div>
              </div>
            ))}
            {sending && <p className="typing-indicator">Thinking…</p>}
          </div>

          <form className="chat-form" onSubmit={sendMessage}>
            <label className="sr-only" htmlFor="chat-input">Message</label>
            <input
              id="chat-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Ask something about your documents…"
              disabled={sending}
            />
            <button type="submit" className="send-button" disabled={!input.trim() || sending} aria-label="Send message">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 10h13M10 4l6 6-6 6" /></svg>
            </button>
          </form>
          <p className="chat-footnote">Answers use relevant excerpts from your uploaded PDFs.</p>
        </section>

        <aside className="documents-panel" aria-label="Documents">
          <div className="panel-heading documents-heading">
            <div>
              <p className="eyebrow">KNOWLEDGE BASE</p>
              <h2>Documents <span className="count-badge">{documents.length}</span></h2>
            </div>
          </div>

          <label className={`upload-card ${uploading ? "is-uploading" : ""}`}>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              disabled={uploading}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void uploadDocument(file);
              }}
            />
            <span className="upload-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14.5v4A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-4" /></svg>
            </span>
            <span className="upload-title">{uploading ? "Uploading PDF…" : "Add a PDF"}</span>
            <span className="upload-help">Choose a PDF from your device</span>
            <span className="file-limit">PDF files only</span>
          </label>

          <div className="document-list-heading">
            <h3>Uploaded files</h3>
            <button className="refresh-button" type="button" onClick={() => void loadDocuments()} aria-label="Refresh documents" title="Refresh">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 8a6 6 0 0 0-10.2-3L4 7m0-4v4h4m-4 5a6 6 0 0 0 10.2 3L16 13m0 4v-4h-4" /></svg>
            </button>
          </div>

          {loadingDocuments ? (
            <p className="empty-state">Loading documents…</p>
          ) : documents.length === 0 ? (
            <div className="empty-state">
              <span className="empty-file-icon">▤</span>
              <p>No documents yet</p>
              <span>Your PDFs will appear here.</span>
            </div>
          ) : (
            <ul className="document-list">
              {documents.map((document) => (
                <li className="document-item" key={document.id}>
                  <span className="pdf-icon">PDF</span>
                  <span className="document-info">
                    <span className="document-name" title={document.name}>{document.name}</span>
                    <span className="document-status"><i />{document.status}</span>
                  </span>
                  <button className="delete-button" type="button" onClick={() => void deleteDocument(document)} aria-label={`Delete ${document.name}`} title="Delete document">
                    <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4.5 6h11m-9.5 0 .6 10h6.8l.6-10M8 6V4h4v2m-3 3v4m2-4v4" /></svg>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {error && <p className="error-message" role="alert">{error}</p>}

          <div className="future-note">
            <span className="sparkle">✳</span>
            <p><strong>PDF search is ready</strong><br />Text is extracted, indexed, and searched to provide context for answers.</p>
          </div>
        </aside>
      </div>
      <footer className="footer">A quiet place for curious questions <span>·</span> v0.1</footer>
    </main>
  );
}

export default App;
