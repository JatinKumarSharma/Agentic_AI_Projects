import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowUp,
  CircleAlert,
  ExternalLink,
  LoaderCircle,
  Sparkles,
} from "lucide-react";

const API_URL = (
  import.meta.env.VITE_API_URL ||
  "https://financial-analyst-api-fw98.onrender.com"
).replace(/\/$/, "");

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type AgentInfo = {
  service_version?: string;
  llm_provider?: string;
  model?: string;
};

const SUGGESTIONS = [
  "What is Apple's current quote and who are its peers?",
  "Compare AAPL and MSFT using live data",
  "What is Tesla's analyst consensus price target?",
  "Find the profile for ticker ZZZZ",
];

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    parts.push(
      match[2] ? (
        <strong key={match.index}>{match[2]}</strong>
      ) : match[3] ? (
        <em key={match.index}>{match[3]}</em>
      ) : (
        <code key={match.index}>{match[4]}</code>
      ),
    );
    last = match.index + match[0].length;
  }

  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function parseRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

function MessageBody({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let bullets: string[] = [];
  let paragraph: string[] = [];
  let key = 0;

  const flushBullets = () => {
    if (bullets.length === 0) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={`ul-${key++}`}>
        {items.map((item, index) => (
          <li key={index}>{inline(item)}</li>
        ))}
      </ul>,
    );
  };

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const content = paragraph.join(" ");
    paragraph = [];
    blocks.push(<p key={`p-${key++}`}>{inline(content)}</p>);
  };

  const flush = () => {
    flushParagraph();
    flushBullets();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flush();
      continue;
    }

    const heading = trimmed.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      flush();
      blocks.push(<h3 key={`h-${key++}`}>{inline(heading[1])}</h3>);
      continue;
    }

    if (/^---+$/.test(trimmed)) {
      flush();
      blocks.push(<hr key={`hr-${key++}`} />);
      continue;
    }

    if (
      trimmed.startsWith("|") &&
      lines[i + 1] &&
      /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim()) &&
      lines[i + 1].includes("-")
    ) {
      flush();
      const header = parseRow(trimmed);
      const rows: string[][] = [];
      i += 1;
      while (lines[i + 1] && lines[i + 1].trim().startsWith("|")) {
        rows.push(parseRow(lines[i + 1]));
        i += 1;
      }
      blocks.push(
        <div className="table-wrap" key={`table-${key++}`}>
          <table>
            <thead>
              <tr>
                {header.map((cell, index) => (
                  <th key={index}>{inline(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}>{inline(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
      flushParagraph();
      bullets.push(trimmed.slice(2));
      continue;
    }

    flushBullets();
    paragraph.push(trimmed);
  }
  flush();

  return <div className="message-body text-[14px] leading-[1.75]">{blocks}</div>;
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<AgentInfo | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/version`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.json() as Promise<AgentInfo>;
      })
      .then((data) => {
        setInfo(data);
        setOnline(true);
      })
      .catch(() => setOnline(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  async function ask(question: string) {
    const cleaned = question.trim();
    if (!cleaned || loading) return;

    setError(null);
    setInput("");
    setMessages((current) => [
      ...current,
      { id: nextId.current++, role: "user", text: cleaned },
    ]);
    setLoading(true);

    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 120000);

    try {
      const response = await fetch(`${API_URL}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: cleaned }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const data = (await response.json()) as { answer?: string };
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          text: data.answer || "No answer was returned.",
        },
      ]);
    } catch (cause) {
      setError(
        cause instanceof Error && cause.name === "AbortError"
          ? "The assistant took too long to respond. Please try again."
          : "The assistant could not be reached. Please try again.",
      );
    } finally {
      window.clearTimeout(timer);
      setLoading(false);
    }
  }

  const providerLabel = info?.llm_provider
    ? `${info.llm_provider} · ${info.model ?? "default model"}`
    : "Multi-model compatible";

  return (
    <div className="dot-grid min-h-screen bg-background text-foreground">
      <div className="ambient-glow pointer-events-none absolute inset-x-0 top-0 h-[440px]" />

      <div className="relative mx-auto flex min-h-screen max-w-3xl flex-col px-5 pb-8 pt-8 sm:px-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 text-accent-light">
              <Sparkles size={19} strokeWidth={1.7} />
            </div>
            <div>
              <h1 className="text-xl tracking-tight sm:text-2xl">
                Financial Research Assistant
              </h1>
              <p className="mt-0.5 text-[13px] text-muted">
                Agentic research grounded in live financial data.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <span
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] uppercase tracking-wide ${
                online
                  ? "border-accent/30 bg-accent/10 text-accent-light"
                  : "border-border bg-surface text-subtle"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  online ? "bg-accent-light" : "bg-subtle"
                }`}
              />
              {online === null
                ? "Connecting"
                : online
                  ? "Assistant online"
                  : "Unavailable"}
            </span>
            <span className="rounded-full border border-info/25 bg-info/5 px-3 py-1.5 text-[11px] text-info">
              {providerLabel}
            </span>
          </div>
        </header>

        <main className="flex flex-1 flex-col">
          <div className="flex-1 py-8">
            {messages.length === 0 ? (
              <div className="mx-auto max-w-xl pt-10 text-center sm:pt-16">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-surface text-accent-light">
                  <Sparkles size={22} strokeWidth={1.5} />
                </div>
                <h2 className="mt-5 text-lg sm:text-xl">
                  Ask a research question
                </h2>
                <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-muted">
                  The assistant selects its own tools, pulls live data, and
                  checks its output before answering.
                </p>

                <div className="mt-8 grid gap-2 text-left sm:grid-cols-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => ask(suggestion)}
                      disabled={loading}
                      className="rounded-xl border border-border bg-surface/70 px-4 py-3 text-left text-[13px] text-muted transition hover:border-accent/40 hover:text-foreground disabled:opacity-50"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map((message) => (
                  <div
                    key={message.id}
                    className={
                      message.role === "user"
                        ? "ml-auto max-w-[88%] rounded-2xl rounded-br-sm border border-border bg-surface-elevated px-4 py-3"
                        : "mr-auto max-w-[88%] rounded-2xl rounded-bl-sm border border-border bg-surface px-4 py-3"
                    }
                  >
                    <div className="mb-1.5 text-[10px] uppercase tracking-[0.16em] text-subtle">
                      {message.role === "user" ? "You" : "Assistant"}
                    </div>
                    <MessageBody text={message.text} />
                  </div>
                ))}

                {loading && (
                  <div className="mr-auto max-w-[88%] rounded-2xl rounded-bl-sm border border-border bg-surface px-4 py-3">
                    <div className="mb-1.5 text-[10px] uppercase tracking-[0.16em] text-subtle">
                      Assistant
                    </div>
                    <div className="flex items-center gap-2 text-[13px] text-muted">
                      <LoaderCircle
                        size={15}
                        className="animate-spin text-accent-light"
                      />
                      Researching
                    </div>
                  </div>
                )}
              </div>
            )}
            <div ref={bottom} />
          </div>

          {error && (
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-red-500/25 bg-red-500/5 px-3 py-2 text-[12px] text-red-300">
              <CircleAlert size={14} />
              {error}
            </div>
          )}

          <div className="sticky bottom-0 border-t border-border/70 bg-background/95 py-4 backdrop-blur">
            <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface px-3 py-2.5 focus-within:border-accent/50">
              <textarea
                rows={1}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask(input);
                  }
                }}
                placeholder="Ask about a company, ticker or market comparison"
                aria-label="Research question"
                className="max-h-32 flex-1 resize-none bg-transparent text-[14px] leading-relaxed text-foreground outline-none placeholder:text-subtle"
              />
              <button
                type="button"
                onClick={() => void ask(input)}
                disabled={!input.trim() || loading}
                aria-label="Send question"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-background transition hover:bg-accent-light disabled:cursor-not-allowed disabled:bg-surface-elevated disabled:text-subtle"
              >
                <ArrowUp size={17} strokeWidth={2} />
              </button>
            </div>
            <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-subtle">
              <span>Grounded in live tool data · Deterministic checks active</span>
              <a
                href="https://jatin-sharma-portfolio-ten.vercel.app/evaluation-framework"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 transition hover:text-accent-light"
              >
                How this was evaluated
                <ExternalLink size={11} />
              </a>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
