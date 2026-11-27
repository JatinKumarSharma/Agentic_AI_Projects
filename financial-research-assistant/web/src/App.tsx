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

type ChartSpec = {
  type: "line" | "bar" | "pie" | "table";
  title: string;
  labels: string[];
  values: number[];
  x_label?: string;
  y_label?: string;
  source?: string;
};

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  charts?: ChartSpec[];
};

type AgentInfo = {
  service_version?: string;
  llm_provider?: string;
  model?: string;
};

const SUGGESTIONS = [
  "Present the past month of AAPL share prices as a line chart",
  "What is Apple's current quote and who are its peers?",
  "Compare AAPL and MSFT using live data",
  "What is Tesla's analyst consensus price target?",
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

const CHART_COLORS = [
  "#79a848",
  "#7fa6ad",
  "#5e8c2e",
  "#a7aaa6",
  "#42651e",
  "#9fb86f",
  "#c2d4a0",
];

function formatValue(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function ChartFrame({
  chart,
  children,
}: {
  chart: ChartSpec;
  children: ReactNode;
}) {
  return (
    <div className="mt-3 rounded-xl border border-border bg-background/60 p-3">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
        {chart.title}
      </div>
      {children}
      <div className="mt-2 text-[10px] text-subtle">
        Rendered from tool-returned data
        {chart.source ? ` · ${chart.source}` : ""}
      </div>
    </div>
  );
}

function LineChart({ chart }: { chart: ChartSpec }) {
  const width = 560;
  const height = 210;
  const left = 48;
  const right = 12;
  const top = 14;
  const bottom = 30;
  const values = chart.values;
  const labels = chart.labels;
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = rawMax === rawMin ? Math.max(1, Math.abs(rawMax) * 0.05) : (rawMax - rawMin) * 0.08;
  const min = rawMin - pad;
  const max = rawMax + pad;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (index: number) =>
    left + (values.length === 1 ? plotWidth / 2 : (index / (values.length - 1)) * plotWidth);
  const y = (value: number) => top + ((max - value) / (max - min)) * plotHeight;
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const labelIndexes = Array.from(
    new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1]),
  ).filter((index) => index >= 0);

  return (
    <ChartFrame chart={chart}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${chart.title} line chart`}
      >
        {[0, 1, 2, 3].map((step) => {
          const value = min + ((max - min) * step) / 3;
          const gy = y(value);
          return (
            <g key={step}>
              <line
                x1={left}
                x2={width - right}
                y1={gy}
                y2={gy}
                stroke="#25302a"
                strokeWidth={1}
              />
              <text x={left - 6} y={gy + 3} fill="#6f7672" fontSize="10" textAnchor="end">
                {formatValue(value)}
              </text>
            </g>
          );
        })}
        <polyline points={points} fill="none" stroke="#79a848" strokeWidth={2} />
        {values.length <= 24 &&
          values.map((value, index) => (
            <circle
              key={index}
              cx={x(index)}
              cy={y(value)}
              r={2.5}
              fill="#000508"
              stroke="#79a848"
              strokeWidth={1.5}
            />
          ))}
        {labelIndexes.map((index) => (
          <text
            key={index}
            x={x(index)}
            y={height - 8}
            fill="#6f7672"
            fontSize="10"
            textAnchor={index === 0 ? "start" : index === labels.length - 1 ? "end" : "middle"}
          >
            {labels[index]}
          </text>
        ))}
      </svg>
    </ChartFrame>
  );
}

function BarChart({ chart }: { chart: ChartSpec }) {
  const width = 560;
  const height = 210;
  const left = 48;
  const right = 12;
  const top = 14;
  const bottom = 30;
  const values = chart.values;
  const labels = chart.labels;
  const peak = Math.max(...values.map(Math.abs)) || 1;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const slot = plotWidth / values.length;
  const barWidth = Math.max(3, slot * 0.68);

  return (
    <ChartFrame chart={chart}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${chart.title} bar chart`}
      >
        <line
          x1={left}
          x2={width - right}
          y1={top + plotHeight}
          y2={top + plotHeight}
          stroke="#25302a"
        />
        {values.map((value, index) => {
          const barHeight = (Math.abs(value) / peak) * plotHeight;
          const bx = left + index * slot + (slot - barWidth) / 2;
          const by = top + plotHeight - barHeight;
          return (
            <rect
              key={index}
              x={bx}
              y={by}
              width={barWidth}
              height={barHeight}
              fill={CHART_COLORS[index % CHART_COLORS.length]}
              opacity={0.9}
            />
          );
        })}
        {[0, 1, 2].map((step) => (
          <text
            key={step}
            x={left - 6}
            y={top + (plotHeight * step) / 2 + 3}
            fill="#6f7672"
            fontSize="10"
            textAnchor="end"
          >
            {formatValue(peak - (peak * step) / 2)}
          </text>
        ))}
        {Array.from(new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1]))
          .filter((index) => index >= 0)
          .map((index) => (
            <text
              key={index}
              x={left + index * slot + slot / 2}
              y={height - 8}
              fill="#6f7672"
              fontSize="10"
              textAnchor="middle"
            >
              {labels[index]}
            </text>
          ))}
      </svg>
    </ChartFrame>
  );
}

function PieChart({ chart }: { chart: ChartSpec }) {
  const values = chart.values;
  const labels = chart.labels;
  const total = values.reduce((sum, value) => sum + Math.max(0, value), 0) || 1;
  const cx = 110;
  const cy = 110;
  const radius = 82;
  let angle = -Math.PI / 2;

  const polar = (r: number, radians: number) => ({
    x: cx + r * Math.cos(radians),
    y: cy + r * Math.sin(radians),
  });

  return (
    <ChartFrame chart={chart}>
      <div className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
        <svg
          viewBox="0 0 220 220"
          className="h-44 w-44 shrink-0"
          role="img"
          aria-label={`${chart.title} pie chart`}
        >
          {values.map((value, index) => {
            const sweep = (Math.max(0, value) / total) * Math.PI * 2;
            const start = angle;
            const end = angle + sweep;
            angle = end;
            const startPoint = polar(radius, start);
            const endPoint = polar(radius, end);
            const large = sweep > Math.PI ? 1 : 0;
            const path = `M ${cx} ${cy} L ${startPoint.x} ${startPoint.y} A ${radius} ${radius} 0 ${large} 1 ${endPoint.x} ${endPoint.y} Z`;
            return (
              <path
                key={index}
                d={path}
                fill={CHART_COLORS[index % CHART_COLORS.length]}
                stroke="#000508"
                strokeWidth={1.5}
              />
            );
          })}
        </svg>
        <div className="w-full space-y-1.5">
          {values.map((value, index) => (
            <div key={index} className="flex items-center gap-2 text-[11px] text-muted">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ background: CHART_COLORS[index % CHART_COLORS.length] }}
              />
              <span className="min-w-0 flex-1 truncate">{labels[index]}</span>
              <span className="text-foreground">{((value / total) * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </ChartFrame>
  );
}

function ChartView({ chart }: { chart: ChartSpec }) {
  const values = Array.isArray(chart.values) ? chart.values.filter(Number.isFinite) : [];
  const labels = Array.isArray(chart.labels) ? chart.labels : [];
  if (values.length === 0) return null;
  if (chart.type === "pie") return <PieChart chart={{ ...chart, values, labels }} />;
  if (chart.type === "bar") return <BarChart chart={{ ...chart, values, labels }} />;
  return <LineChart chart={{ ...chart, values, labels }} />;
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
      const data = (await response.json()) as {
        answer?: string;
        charts?: ChartSpec[];
      };
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          text: data.answer || "No answer was returned.",
          charts: Array.isArray(data.charts) ? data.charts : [],
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
                    {message.charts?.map((chart, chartIndex) => (
                      <ChartView key={chartIndex} chart={chart} />
                    ))}
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
