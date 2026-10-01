import { useChat } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import { DefaultChatTransport, type UIMessage } from "ai";
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  MessageSquarePlus,
  MoreHorizontal,
  Plus,
  Trash2,
} from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { type InvestigationThread, readThreads, writeThreads } from "@/lib/investigations";
import { cn } from "@/lib/utils";

/** The backend URL prefix (proxied through the SSR server in dev) */
const FHI_BOT_URL = "/backend";

/** Fetch the DB-stored message history for a session. */
async function fetchDbMessages(sessionId: string): Promise<UIMessage[]> {
  try {
    const res = await fetch(`${FHI_BOT_URL}/sessions/${sessionId}/messages`, {
      credentials: "include",
    });
    if (!res.ok) return [];
    const data = await res.json();
    return (data.messages ?? []) as UIMessage[];
  } catch {
    return [];
  }
}

function textFromMessage(message: UIMessage) {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

type TableSeries = { key: string; label: string };
type ParsedMarkdownTable = {
  label: string;
  categoryKey: string;
  rows: Array<Record<string, string | number>>;
  series: TableSeries[];
  unit: "currency" | "percent" | "number";
  timeSeries: boolean;
};

function splitTableCells(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) =>
      cell
        .trim()
        .replace(/\\\|/g, "|")
        .replace(/<[^>]*>/g, ""),
    );
}

function parseTableNumber(value: string): number | null {
  const cleaned = value
    .trim()
    .replace(/^\((.*)\)$/, "-$1")
    .replace(/[^\d.+-]/g, "");
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseMarkdownTables(text: string): ParsedMarkdownTable[] {
  const lines = text.split("\n");
  const tables: ParsedMarkdownTable[] = [];

  for (let index = 0; index < lines.length - 2 && tables.length < 2; index += 1) {
    if (!lines[index]?.includes("|")) continue;
    const headers = splitTableCells(lines[index]!);
    const divider = splitTableCells(lines[index + 1]!);
    if (
      headers.length < 2 ||
      divider.length !== headers.length ||
      !divider.every((cell) => /^:?-{3,}:?$/.test(cell))
    ) {
      continue;
    }

    const values: string[][] = [];
    let rowIndex = index + 2;
    while (rowIndex < lines.length && lines[rowIndex]?.includes("|")) {
      const row = splitTableCells(lines[rowIndex]!);
      if (row.length !== headers.length) break;
      values.push(row);
      rowIndex += 1;
    }
    index = rowIndex - 1;
    if (values.length < 2) continue;

    const numericColumns = headers.slice(1).flatMap((header, offset) => {
      const columnIndex = offset + 1;
      const parsedValues = values.map((row) => parseTableNumber(row[columnIndex] ?? ""));
      if (parsedValues.filter((value) => value !== null).length < 2) return [];
      if (parsedValues.filter((value) => value !== null).length !== values.length) return [];
      return [{ header, columnIndex, parsedValues: parsedValues as number[] }];
    });
    if (numericColumns.length === 0) continue;

    const unitFor = (header: string): ParsedMarkdownTable["unit"] => {
      if (/%|percent/i.test(header)) return "percent";
      if (/[$€£]|\b(currency|revenue|sales|amount|target|actual|variance)\b/i.test(header)) {
        return "currency";
      }
      return "number";
    };
    const unitCounts = new Map<ParsedMarkdownTable["unit"], number>();
    for (const column of numericColumns) {
      const unit = unitFor(column.header);
      unitCounts.set(unit, (unitCounts.get(unit) ?? 0) + 1);
    }
    const unit = (["currency", "number", "percent"] as const).find(
      (candidate) => unitCounts.get(candidate) === Math.max(...unitCounts.values()),
    )!;
    const selectedColumns = numericColumns
      .filter((column) => unitFor(column.header) === unit)
      .slice(0, 4);
    const categoryKey = "category";
    const series = selectedColumns.map((column, selectedIndex) => ({
      key: `series_${selectedIndex}`,
      label: column.header,
    }));
    const chartRows = values.map((row, rowIndex) => {
      const chartRow: Record<string, string | number> = {
        [categoryKey]: row[0] ?? "",
      };
      selectedColumns.forEach((column, selectedIndex) => {
        chartRow[`series_${selectedIndex}`] = column.parsedValues[rowIndex]!;
      });
      return chartRow;
    });
    const labels = values.map((row) => row[0] ?? "");

    tables.push({
      label: selectedColumns.map((column) => column.header).join(" / "),
      categoryKey,
      rows: chartRows,
      series,
      unit,
      timeSeries: labels.every((label) =>
        /(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b|\b\d{4}[-/]\d{1,2}\b)/i.test(
          label,
        ),
      ),
    });
  }

  return tables;
}

function MarkdownTableCharts({ text }: { text: string }) {
  const tables = parseMarkdownTables(text);
  if (tables.length === 0) return null;

  return (
    <div className="mt-4 grid gap-5">
      {tables.map((table, tableIndex) => (
        <section
          key={`${table.label}-${tableIndex}`}
          aria-label={`Chart: ${table.label}`}
          className="border-t border-border/70 pt-3"
        >
          <p className="mb-2 text-[10px] font-bold uppercase text-muted-foreground">
            {table.timeSeries ? "Trend" : "Comparison"} · {table.label}
          </p>
          <div className="h-56 min-w-0 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={table.rows} margin={{ top: 8, right: 12, bottom: 2, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey={table.categoryKey}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  minTickGap={18}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                  tickFormatter={(value: number) =>
                    table.unit === "currency"
                      ? new Intl.NumberFormat("en-US", {
                          notation: "compact",
                          maximumFractionDigits: 1,
                        }).format(value)
                      : table.unit === "percent"
                        ? `${value}%`
                        : new Intl.NumberFormat("en-US", { notation: "compact" }).format(value)
                  }
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--popover)",
                    border: "1px solid var(--border)",
                    borderRadius: "6px",
                    color: "var(--popover-foreground)",
                    fontSize: "12px",
                  }}
                  formatter={(value) =>
                    typeof value === "number" && table.unit === "currency"
                      ? new Intl.NumberFormat("en-US", {
                          style: "currency",
                          currency: "USD",
                          maximumFractionDigits: 2,
                        }).format(value)
                      : typeof value === "number" && table.unit === "percent"
                        ? `${value}%`
                        : value
                  }
                />
                {table.series.length > 1 && <Legend />}
                {table.series.map((series, seriesIndex) => {
                  const color = [
                    "var(--chart-2)",
                    "var(--accent)",
                    "var(--azure)",
                    "var(--chart-5)",
                  ][seriesIndex % 4];
                  return table.timeSeries ? (
                    <Line
                      key={series.key}
                      type="monotone"
                      dataKey={series.key}
                      name={series.label}
                      stroke={color}
                      strokeWidth={2.5}
                      dot={{ r: 3, fill: color, strokeWidth: 0 }}
                      activeDot={{ r: 5 }}
                    />
                  ) : (
                    <Bar
                      key={series.key}
                      dataKey={series.key}
                      name={series.label}
                      fill={color}
                      radius={[3, 3, 0, 0]}
                      maxBarSize={42}
                    />
                  );
                })}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>
      ))}
    </div>
  );
}

// ─── Typing dots loader ────────────────────────────────────────────────────────
function TypingIndicator() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-7 place-items-center rounded-[8px] bg-primary font-display text-[9px] font-black text-primary-foreground shrink-0">
        AI
      </div>
      <div
        className="flex items-center gap-1 rounded-xl bg-card/60 border border-border px-3 py-2.5"
        aria-label="AI is thinking"
      >
        <span
          className="size-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]"
          style={{ animationDuration: "0.9s" }}
        />
        <span
          className="size-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:180ms]"
          style={{ animationDuration: "0.9s" }}
        />
        <span
          className="size-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:360ms]"
          style={{ animationDuration: "0.9s" }}
        />
      </div>
    </div>
  );
}

// ─── Empty-state placeholder when no threads exist ───────────────────────────
function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 py-16 text-center">
      <div className="grid size-16 place-items-center rounded-2xl bg-primary/10 text-primary">
        <MessageSquarePlus className="size-7" />
      </div>
      <div>
        <p className="font-display text-lg font-black">No investigations yet</p>
        <p className="mt-2 max-w-xs text-sm text-muted-foreground leading-5">
          Start a new investigation to ask questions about your data and uncover root causes.
        </p>
      </div>
      <Button onClick={onNew} className="bg-primary text-primary-foreground">
        <Plus className="size-4" />
        New investigation
      </Button>
    </div>
  );
}

function ThreadList({
  threads,
  activeId,
  onNew,
  onDelete,
}: {
  threads: InvestigationThread[];
  activeId: string;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const navigate = useNavigate();
  return (
    <aside className="flex flex-col border-b border-border bg-card/40 p-3 lg:border-b-0 lg:border-r lg:overflow-y-auto">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-bold uppercase text-muted-foreground">Investigations</p>
        <Button size="icon-sm" variant="ghost" onClick={onNew} title="New investigation">
          <Plus />
        </Button>
      </div>
      {threads.length === 0 ? (
        <EmptyState onNew={onNew} />
      ) : (
        <div className="mt-2 space-y-1.5">
          {threads.map((thread) => (
            <div
              key={thread.id}
              className={cn(
                "group rounded-[8px] border p-2.5 transition-colors",
                activeId === thread.id
                  ? "border-primary/35 bg-primary/10"
                  : "border-transparent hover:bg-secondary/60",
              )}
            >
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() =>
                    navigate({
                      to: "/investigations/$threadId",
                      params: { threadId: thread.id },
                    })
                  }
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "size-2 rounded-full",
                        thread.status === "Active"
                          ? "bg-mint"
                          : thread.status === "Review"
                            ? "bg-sun"
                            : "bg-muted-foreground",
                      )}
                    />
                    <span className="truncate text-xs font-bold">{thread.title}</span>
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                    {thread.summary}
                  </p>
                  <p className="mt-2 text-[10px] font-semibold text-muted-foreground">
                    {thread.updatedAt}
                  </p>
                </button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="size-7 opacity-0 group-hover:opacity-100"
                  onClick={() => onDelete(thread.id)}
                  title={`Delete ${thread.title}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

function ChatPanel({
  thread,
  onMessages,
}: {
  thread: InvestigationThread;
  onMessages: (messages: UIMessage[]) => void;
}) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [dbMessages, setDbMessages] = useState<UIMessage[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  // Fetch DB history whenever the thread changes
  useEffect(() => {
    setHistoryLoaded(false);
    setDbMessages([]);
    let cancelled = false;
    fetchDbMessages(thread.id).then((msgs) => {
      if (cancelled) return;
      setDbMessages(msgs);
      setHistoryLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [thread.id]);

  // Seed messages: prefer DB history, fall back to thread.messages from localStorage
  const seedMessages = useMemo<UIMessage[]>(() => {
    if (!historyLoaded) return [];
    return dbMessages.length > 0 ? dbMessages : thread.messages;
  }, [historyLoaded, dbMessages, thread.messages]);

  const transport = useMemo(
    () => new DefaultChatTransport({ api: "/api/chat", body: { threadId: thread.id } }),
    [thread.id],
  );

  const { messages, setMessages, sendMessage, status, stop, error } = useChat({
    id: thread.id,
    messages: seedMessages,
    transport,
  });

  // When seedMessages are ready, initialise useChat if empty
  useEffect(() => {
    if (historyLoaded && seedMessages.length > 0 && messages.length === 0) {
      setMessages(seedMessages);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyLoaded, seedMessages.length]);

  useEffect(() => {
    onMessages(messages);
  }, [messages, onMessages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [thread.id, status]);

  const busy = status === "submitted" || status === "streaming";
  const submit = useCallback(
    async (value: string) => {
      const trimmed = value.trim();
      if (!trimmed || busy) return;
      setInput("");
      await sendMessage({ text: trimmed });
    },
    [busy, sendMessage],
  );

  // Show typing indicator when submitted or streaming with no text yet
  const showTyping =
    status === "submitted" ||
    (status === "streaming" &&
      messages.length > 0 &&
      !textFromMessage(messages[messages.length - 1]!));

  return (
    <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-card/25">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 className="font-display text-base font-extrabold">{thread.title}</h2>
          <p className="mt-0.5 flex items-center gap-1 text-[10px] font-semibold text-muted-foreground">
            <Database className="size-3" />
            {historyLoaded ? `${messages.length} messages · Database` : "Loading history…"}
          </p>
        </div>
        <Button size="icon-sm" variant="ghost" title="More options">
          <MoreHorizontal />
        </Button>
      </div>
      <Conversation className="min-h-0 flex-1">
        <ConversationContent className="gap-5 p-4 md:p-6">
          {messages.map((message) => (
            <Message
              key={message.id}
              from={message.role}
              className={message.role === "user" ? "max-w-[82%]" : "max-w-full"}
            >
              <MessageContent
                className={
                  message.role === "user"
                    ? "bg-primary px-4 py-3 text-primary-foreground"
                    : "w-full max-w-none"
                }
              >
                {message.parts.map((part, index) =>
                  part.type === "text" ? (
                    <div key={`${message.id}-${index}`} className="min-w-0">
                      <MessageResponse>{part.text}</MessageResponse>
                      {message.role === "assistant" && <MarkdownTableCharts text={part.text} />}
                    </div>
                  ) : null,
                )}
              </MessageContent>
            </Message>
          ))}
          {showTyping && <TypingIndicator />}
          <ConversationScrollButton />
        </ConversationContent>
      </Conversation>
      <div className="border-t border-border bg-card/60 p-3 md:p-4">
        {error && (
          <p
            role="alert"
            className="mb-2 rounded-[6px] bg-accent/10 px-3 py-2 text-xs font-semibold text-accent"
          >
            {error.message || "The investigation could not continue. Please try again."}
          </p>
        )}
        <div className="mb-2 flex flex-wrap gap-1.5">
          {[
            "Summarize the strongest evidence",
            "What is the root cause?",
            "Draft a next-step action plan",
          ].map((prompt) => (
            <Button
              key={prompt}
              size="sm"
              variant="outline"
              className="h-7 rounded-[6px] text-[10px]"
              onClick={() => void submit(prompt)}
            >
              {prompt}
            </Button>
          ))}
        </div>
        <PromptInput
          onSubmit={({ text }) => void submit(text)}
          className="rounded-[8px] bg-background/70"
        >
          <PromptInputTextarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about this investigation…"
            className="min-h-20"
          />
          <PromptInputFooter className="justify-between">
            <span className="text-[10px] font-semibold text-muted-foreground">
              Evidence-aware · Shift+Enter for a new line
            </span>
            <PromptInputSubmit
              status={status}
              onStop={() => void stop()}
              disabled={!input.trim() && !busy}
              className="bg-primary text-primary-foreground"
            />
          </PromptInputFooter>
        </PromptInput>
      </div>
    </section>
  );
}

export function InvestigationWorkspace({ threadId }: { threadId: string }) {
  const navigate = useNavigate();
  // Start with empty threads — no seed data
  const [threads, setThreads] = useState<InvestigationThread[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Only load threads that were actually saved by the user (skip the old seed defaults)
    const saved = readThreads();
    // If the saved list is the old seed data (all 3 IDs present), start fresh
    const SEED_IDS = new Set([
      "lumber-decking-performance",
      "ukiah-building-materials",
      "contractor-cohort-shift",
    ]);
    const isSeedOnly = saved.length > 0 && saved.every((t) => SEED_IDS.has(t.id));
    setThreads(isSeedOnly ? [] : saved);
    setReady(true);
  }, []);

  const active = threads.find((thread) => thread.id === threadId) ?? threads[0];

  useEffect(() => {
    if (!ready) return;
    if (threads.length === 0) return; // nothing to redirect to
    if (active && active.id !== threadId)
      void navigate({
        to: "/investigations/$threadId",
        params: { threadId: active.id },
        replace: true,
      });
  }, [active, navigate, ready, threadId, threads.length]);

  const updateMessages = useCallback(
    (messages: UIMessage[]) => {
      setThreads((current) => {
        const existing = current.find((item) => item.id === threadId);
        if (!existing || JSON.stringify(existing.messages) === JSON.stringify(messages))
          return current;
        const next = current.map((item) =>
          item.id === threadId ? { ...item, messages, updatedAt: "Just now" } : item,
        );
        writeThreads(next);
        return next;
      });
    },
    [threadId],
  );

  const createThread = () => {
    const id = `investigation-${Date.now()}`;
    const next: InvestigationThread = {
      id,
      title: "New investigation",
      summary: "A custom operational question.",
      status: "Active",
      updatedAt: "Just now",
      hypotheses: [],
      evidence: [],
      messages: [],
    };
    setThreads((current) => {
      const updated = [next, ...current];
      writeThreads(updated);
      return updated;
    });
    void navigate({ to: "/investigations/$threadId", params: { threadId: id } });
  };

  const deleteThread = (id: string) => {
    const next = threads.filter((thread) => thread.id !== id);
    setThreads(next);
    writeThreads(next);
    if (id === threadId) {
      if (next[0]) {
        void navigate({ to: "/investigations/$threadId", params: { threadId: next[0].id } });
      } else {
        // All threads deleted — stay on this route but render the empty sidebar
        void navigate({ to: "/investigations/$threadId", params: { threadId: "new" } });
      }
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="shrink-0 flex flex-wrap items-end justify-between gap-4 px-5 pb-5 pt-7 md:px-8">
        <div>
          <Button asChild size="sm" variant="ghost" className="mb-3 -ml-3">
            <a href="/">
              <ArrowLeft />
              Overview
            </a>
          </Button>
          <p className="text-[11px] font-bold uppercase text-primary">AI investigation workspace</p>
          <h1 className="mt-2 font-display text-3xl font-black md:text-[40px]">
            Follow the evidence.
          </h1>
        </div>
        <div className="glass flex items-center gap-2 rounded-[8px] px-3 py-2 text-xs font-bold">
          <CheckCircle2 className="size-4 text-mint" /> History from database
        </div>
      </header>
      <div className="flex-1 min-h-0 px-5 pb-7 md:px-8">
        <div className="glass overflow-hidden h-full">
          <div className="grid h-full lg:grid-cols-[250px_minmax(0,1fr)]">
            <ThreadList
              threads={threads}
              activeId={active?.id ?? ""}
              onNew={createThread}
              onDelete={deleteThread}
            />
            {active ? (
              <ChatPanel key={active.id} thread={active} onMessages={updateMessages} />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-5 text-center px-8">
                <div className="grid size-16 place-items-center rounded-2xl bg-primary/10 text-primary">
                  <MessageSquarePlus className="size-7" />
                </div>
                <div>
                  <p className="font-display text-lg font-black">Ready to investigate</p>
                  <p className="mt-2 max-w-sm text-sm text-muted-foreground leading-5">
                    Create a new investigation from the sidebar to start asking questions about your
                    data.
                  </p>
                </div>
                <Button onClick={createThread} className="bg-primary text-primary-foreground">
                  <Plus className="size-4" />
                  New investigation
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
