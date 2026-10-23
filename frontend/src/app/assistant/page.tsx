"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bot, FileText, Paperclip, Send, Sparkles, X } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { MotionPanel } from "@/components/MotionPanel";
import { InvestigationCard } from "@/components/InvestigationCard";
import { AssistantText } from "@/components/AssistantText";
import { WorkflowPanel } from "@/components/WorkflowPanel";

type MessageContext = {
  kind?: string;
  import_ids?: string[];
  review_required?: boolean;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  context: MessageContext;
  created_at: string;
};

type Statement = { id: string };

const suggestions = [
  "Give me my balance summary",
  "What still needs review?",
  "Show my credit-card summary",
  "Who have I paid the most?",
];

export default function AssistantPage() {
  const [accent, setAccent] = useState(() =>
    typeof window === "undefined"
      ? "cobalt"
      : (localStorage.getItem("expense-accent") ?? "cobalt"),
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [panelKey, setPanelKey] = useState(0);
  const [turn, setTurn] = useState(0);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  // One line by default; grows with the draft up to the CSS max-height.
  useEffect(() => {
    const field = composer.current;
    if (!field) return;
    field.style.height = "";
    if (field.scrollHeight > field.clientHeight) field.style.height = `${field.scrollHeight}px`;
  }, [message]);

  useEffect(() => {
    fetch("/api/assistant/messages")
      .then(async (response) => {
        if (!response.ok) throw new Error("Your conversation could not be loaded.");
        return response.json();
      })
      .then(setMessages)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Unable to load"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages, busy]);

  // Questions and notes load on their own; while the reader is at the bottom, keep the newest
  // turn in view as it appears. Someone reading further up is never pulled down.
  // A callback ref, because the feed mounts only after the shell has checked the session.
  const feedObserver = useRef<MutationObserver | null>(null);
  const attachFeed = useCallback((element: HTMLDivElement | null) => {
    feedObserver.current?.disconnect();
    feed.current = element;
    if (!element) return;
    let pinned = true;
    element.addEventListener("scroll", () => { pinned = element.scrollHeight - element.scrollTop - element.clientHeight < 80; });
    feedObserver.current = new MutationObserver(() => { if (pinned) element.scrollTop = element.scrollHeight; });
    feedObserver.current.observe(element, { childList: true, subtree: true, characterData: true });
  }, []);

  function chooseFiles(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files ?? []).slice(0, 20));
    event.target.value = "";
  }

  async function send(event?: FormEvent) {
    event?.preventDefault();
    if ((!message.trim() && !files.length) || busy) return;
    setBusy(true);
    setError("");
    try {
      let importIds: string[] = [];
      if (files.length) {
        const body = new FormData();
        files.forEach((file) => body.append("files", file));
        const upload = await fetch("/api/imports", { method: "POST", body });
        if (!upload.ok) {
          const detail = await upload.json().catch(() => ({ detail: "Statement upload failed" }));
          throw new Error(detail.detail ?? "Statement upload failed");
        }
        const statements: Statement[] = await upload.json();
        await fetch("/api/workflow/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ import_ids: statements.map(item => item.id), client_key: `intake-${statements[0]?.id}` }) }).catch(() => undefined);
        setPanelKey(key => key + 1);
        importIds = statements.map((statement) => statement.id);
      }
      const turn = await fetch("/api/assistant/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message.trim(), import_ids: importIds }),
      });
      if (!turn.ok) {
        const detail = await turn.json().catch(() => ({ detail: "The assistant could not respond" }));
        throw new Error(detail.detail ?? "The assistant could not respond");
      }
      const assistantMessage: Message = await turn.json();
      const userMessage: Message = {
        id: `local-${Date.now()}`,
        role: "user",
        content:
          message.trim() ||
          `Uploaded ${files.length} statement file${files.length === 1 ? "" : "s"}.`,
        context: { import_ids: importIds },
        created_at: new Date().toISOString(),
      };
      setMessages((current) => [...current, userMessage, assistantMessage]);
      setTurn((current) => current + 1);
      setMessage("");
      setFiles([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell accent={accent} onAccent={setAccent} active="Assistant">
      <section className="assistant-shell">
        <header className="assistant-head">
          <div className="assistant-mark" aria-hidden="true">
            <Bot />
          </div>
          <div>
            <p className="kicker">Private financial assistant</p>
            <h1>What should we sort out?</h1>
            <p>Your statements and financial history stay inside your private workspace.</p>
          </div>
          <span className="assistant-local"><i /> Local analysis</span>
        </header>

        <div ref={attachFeed} className="assistant-feed" aria-live="polite" aria-busy={loading || busy}>
          {loading && <p className="assistant-status">Opening your private conversation…</p>}
          {!loading && messages.length === 0 && (
            <div className="assistant-welcome">
              <Sparkles aria-hidden="true" />
              <h2>Start with a statement or a question</h2>
              <p>
                I can identify the bank and account type, group repeated payments, connect card
                repayments, and explain what still needs your decision.
              </p>
              <div className="assistant-suggestions" aria-label="Suggested questions">
                {suggestions.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => setMessage(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((item) => (
            <article className={`assistant-message ${item.role}`} key={item.id}>
              <span>{item.role === "assistant" ? <Bot aria-hidden="true" /> : "You"}</span>
              <div>
                {item.role === "assistant" && item.context.kind === "investigation" ? <InvestigationCard context={item.context as never} /> : item.role === "assistant" ? <AssistantText content={item.content} /> : <p>{item.content}</p>}
                {item.role === "assistant" && item.context.review_required && (
                  <Link className="assistant-action" href="/imports">
                    Review statement <ArrowRight aria-hidden="true" />
                  </Link>
                )}
              </div>
            </article>
          ))}
          {/* What the assistant is waiting on comes after the conversation, as its latest turn. */}
          <WorkflowPanel refreshKey={panelKey} turn={turn} onDone={() => { fetch("/api/assistant/messages").then(r => (r.ok ? r.json() : null)).then(items => { if (items) setMessages(items); }).catch(() => undefined); setPanelKey(key => key + 1); }} />
          <MotionPanel refreshKey={panelKey} />
          {busy && (
            <div className="assistant-thinking">
              <Bot aria-hidden="true" />
              <span>{files.length ? "Reading your statement…" : "Checking your workspace…"}</span>
            </div>
          )}
        </div>

        <form className="assistant-composer" onSubmit={send}>
          {files.length > 0 && (
            <div className="assistant-files">
              {files.map((file, index) => (
                <span key={`${file.name}-${index}`}>
                  <FileText aria-hidden="true" /> {file.name}
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => setFiles((current) => current.filter((_, item) => item !== index))}
                  >
                    <X aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="assistant-compose-row">
            <button
              className="assistant-attach"
              type="button"
              aria-label="Attach bank statements"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
            >
              <Paperclip aria-hidden="true" />
            </button>
            <label className="sr-only" htmlFor="assistant-message">
              Message your financial assistant
            </label>
            <textarea
              ref={composer}
              id="assistant-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Ask about your money, or attach a statement…"
              rows={1}
              disabled={busy}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <button
              className="assistant-send"
              type="submit"
              aria-label="Send message"
              disabled={busy || (!message.trim() && !files.length)}
            >
              <Send aria-hidden="true" />
            </button>
            <input
              ref={fileInput}
              type="file"
              aria-label="Statement files"
              accept=".pdf,.csv,.txt"
              multiple
              hidden
              onChange={chooseFiles}
            />
          </div>
          <div className="assistant-compose-meta">
            <span>PDF, CSV or TXT · review required before ledger changes</span>
            <span>Enter to send · Shift+Enter for a new line</span>
          </div>
          {error && <p className="assistant-error" role="alert">{error}</p>}
        </form>
      </section>
    </AppShell>
  );
}
