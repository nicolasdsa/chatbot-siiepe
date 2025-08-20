import React, { useEffect, useMemo, useRef, useState } from "react";
import { sanitizeHtml } from "../lib/sanitize";

const API_BASE = "/api";
const LS_KEY_HISTORY = "siepe-chat-history";

// Token vem do build (Docker/Vite)
const AUTH_HEADER =
  import.meta.env.VITE_RAG_TOKEN
    ? { Authorization: `Bearer ${import.meta.env.VITE_RAG_TOKEN}` }
    : {};

function useLocalStorage(key, initialValue) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : initialValue;
    } catch {
      return initialValue;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }, [key, value]);
  return [value, setValue];
}

function stripHtml(html) {
  const tmp = document.createElement("div");
  tmp.innerHTML = html || "";
  return (tmp.textContent || tmp.innerText || "").trim();
}

/* ===== Typewriter do assistente ===== */
function splitHtmlSegments(html) {
  const out = [];
  let i = 0;
  while (i < html.length) {
    if (html[i] === "<") {
      const j = html.indexOf(">", i);
      if (j === -1) { out.push({ type: "text", value: html.slice(i) }); break; }
      out.push({ type: "tag", value: html.slice(i, j + 1) });
      i = j + 1;
    } else {
      const j = html.indexOf("<", i);
      out.push({ type: "text", value: html.slice(i, j === -1 ? html.length : j) });
      i = (j === -1) ? html.length : j;
    }
  }
  return out;
}

function AssistantMessage({ html }) {
  const clean = useMemo(() => sanitizeHtml(html ?? ""), [html]);
  const segments = useMemo(() => splitHtmlSegments(clean), [clean]);

  const [typed, setTyped] = useState("");
  const [isTyping, setIsTyping] = useState(true);
  const containerRef = useRef(null);

  useEffect(() => {
    let segIdx = 0, charIdx = 0, acc = "";
    let cancelled = false;

    const totalTextChars = segments.filter(s => s.type === "text")
      .reduce((n, s) => n + s.value.length, 0);

    const baseDelay = totalTextChars > 2000 ? 8 : totalTextChars > 800 ? 12 : 16;

    function tick() {
      if (cancelled) return;
      if (segIdx >= segments.length) { setIsTyping(false); return; }

      const seg = segments[segIdx];

      if (seg.type === "tag") {
        acc += seg.value; segIdx++; charIdx = 0; setTyped(acc);
        requestAnimationFrame(tick);
        return;
      }

      const step = totalTextChars > 1500 ? 3 : totalTextChars > 600 ? 2 : 1;
      const next = seg.value.slice(charIdx, charIdx + step);
      acc += next; charIdx += next.length; setTyped(acc);
      if (charIdx >= seg.value.length) { segIdx++; charIdx = 0; }

      setTimeout(tick, baseDelay);
    }

    const start = setTimeout(tick, 350);
    return () => { cancelled = true; clearTimeout(start); };
  }, [segments]);

  useEffect(() => {
    containerRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [typed, isTyping]);

  return (
    <div
      ref={containerRef}
      className="chat-bubble chat-bubble-bot chat-content"
      dangerouslySetInnerHTML={{
        __html: typed + (isTyping ? '<span class="caret"></span>' : "")
      }}
    />
  );
}

/* ===================== */
/*         Chat          */
/* ===================== */

export default function Chat() {
  const [messages, setMessages] = useLocalStorage(LS_KEY_HISTORY, [
    { role: "assistant", content: "<p>Olá! Sou o assistente da <strong>SIIEPE/UFPel</strong>. Envie sua pergunta.</p>" }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const q = input.trim();
    if (!q) return;
    setInput("");

    const newMessages = [...messages, { role: "user", content: q }];
    setMessages(newMessages);
    setLoading(true);

    try {
      const historyForApi = newMessages.slice(-6).map(m => ({
        role: m.role,
        content: stripHtml(m.content)
      }));

      const resp = await fetch(`${API_BASE}/query`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...AUTH_HEADER
        },
        body: JSON.stringify({ q, top_k: 3, history: historyForApi, contextualize: true })
      });

      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
      }
      const data = await resp.json();
      const answer = data?.answer ?? "Sem resposta.";
      setMessages(msgs => [...msgs, { role: "assistant", content: answer }]);
    } catch (err) {
      setMessages(msgs => [
        ...msgs,
        { role: "assistant", content: `<p><strong>Ops!</strong> Não consegui responder agora.</p><p class="text-inkSoft"><code>${String(err)}</code></p>` }
      ]);
    } finally {
      setLoading(false);
    }
  }

  function clearChat() {
    try { localStorage.removeItem(LS_KEY_HISTORY); } catch {}
    setMessages([]);
    setInput("");
    setLoading(false);
  }

  function exportChat() {
    const blob = new Blob([JSON.stringify(messages, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "conversa-siepe.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      {/* Aviso de tempo de resposta */}
      <div className="mb-4 rounded-xl bg-yellow-50 border border-secondary/30 text-ink text-sm p-3 flex items-center gap-3">
        <svg width="18" height="18" viewBox="0 0 24 24" className="text-secondary">
          <path fill="currentColor" d="M12 2L1 21h22L12 2Zm1 15h-2v-2h2v2Zm0-4h-2V9h2v4Z" />
        </svg>
        <span>
          As respostas podem levar <strong>~2–3 minutos</strong>, pois o sistema pesquisa e
          consolida informações dos anais.
        </span>
      </div>

      {/* Ações (sem campo de token) */}
      <div className="mb-4 flex flex-wrap items-center gap-2 justify-end">
        <button onClick={exportChat} className="px-3 py-2 rounded-xl bg-accentGreen text-white hover:opacity-90">
          Exportar
        </button>
        <button onClick={clearChat} className="px-3 py-2 rounded-xl bg-accentOrange text-white hover:opacity-90">
          Limpar
        </button>
      </div>

      {/* Janela do chat */}
      <div className="bg-white rounded-2xl shadow-soft border border-gray-100">
        <div ref={listRef} className="messages max-h-[65vh] overflow-y-auto p-4 space-y-3">
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="chat-bubble chat-bubble-user max-w-[80%] whitespace-pre-wrap">{m.content}</div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <AssistantMessage html={m.content} />
              </div>
            )
          )}

          {loading && (
            <div className="flex items-center gap-3 px-2">
              <svg className="animate-spin" width="18" height="18" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" stroke="#003A70" strokeWidth="4" fill="none" opacity="0.2" />
                <path d="M22 12a10 10 0 0 0-10-10" stroke="#003A70" strokeWidth="4" fill="none" />
              </svg>
              <span className="text-inkSoft">Procurando…</span>
            </div>
          )}
        </div>

        {/* Form de envio */}
        <div className="border-t border-gray-100 p-3">
          <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Escreva sua pergunta…"
              className="flex-1 min-h-[44px] max-h-[180px] resize-y px-3 py-2 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-primary/40"
              required
            />
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 rounded-xl bg-primary text-white hover:opacity-90 disabled:opacity-50"
              title="Enviar"
            >
              Enviar
            </button>
          </form>
          <div className="text-xs text-inkSoft mt-2">
            Dica: inclua o <strong>ano/área/evento</strong> na pergunta para respostas mais focadas.
          </div>
        </div>
      </div>

      {/* Legenda de cores / temas */}
      <div className="flex gap-2 text-xs text-inkSoft mt-4">
        <span className="px-2 py-1 rounded-full bg-accentGreen/15">Ensino</span>
        <span className="px-2 py-1 rounded-full bg-accentPurple/15">Pesquisa</span>
        <span className="px-2 py-1 rounded-full bg-accentOrange/15">Extensão</span>
        <span className="px-2 py-1 rounded-full bg-secondary/20">Inovação</span>
      </div>
    </div>
  );
}
