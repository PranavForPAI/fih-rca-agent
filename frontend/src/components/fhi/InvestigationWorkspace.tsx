import { useChat } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import { DefaultChatTransport, type UIMessage } from "ai";
import {
  ArrowLeft,
  CheckCircle2,
  FileSpreadsheet,
  FlaskConical,
  MoreHorizontal,
  Plus,
  Trash2,
} from "lucide-react";
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
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import {
  type InvestigationThread,
  readThreads,
  seedThreads,
  writeThreads,
} from "@/lib/investigations";
import { cn } from "@/lib/utils";

function textFromMessage(message: UIMessage) {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
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
    <aside className="border-b border-border bg-card/40 p-3 lg:border-b-0 lg:border-r lg:overflow-y-auto">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-bold uppercase text-muted-foreground">Investigations</p>
        <Button size="icon-sm" variant="ghost" onClick={onNew} title="New investigation">
          <Plus />
        </Button>
      </div>
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
                  navigate({ to: "/investigations/$threadId", params: { threadId: thread.id } })
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
                  {thread.updatedAt} · {thread.hypotheses.length} hypotheses
                </p>
              </button>
              {threads.length > 1 && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="size-7 opacity-0 group-hover:opacity-100"
                  onClick={() => onDelete(thread.id)}
                  title={`Delete ${thread.title}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function EvidencePanel({ thread }: { thread: InvestigationThread }) {
  return (
    <aside className="space-y-5 border-t border-border bg-card/35 p-4 xl:border-l xl:border-t-0 xl:overflow-y-auto">
      <section>
        <div className="flex items-center gap-2">
          <FlaskConical className="size-4 text-primary" />
          <h2 className="text-xs font-black uppercase">Hypothesis tracker</h2>
        </div>
        <div className="mt-3 space-y-2">
          {thread.hypotheses.map((item) => (
            <div key={item.id} className="rounded-[8px] border border-border bg-card/60 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-black text-primary">{item.id}</span>
                <span
                  className={cn(
                    "rounded-[5px] px-1.5 py-0.5 text-[9px] font-black uppercase",
                    item.status === "Leading"
                      ? "bg-sun/20"
                      : item.status === "Testing"
                        ? "bg-azure/10 text-azure"
                        : "bg-secondary text-muted-foreground",
                  )}
                >
                  {item.status}
                </span>
              </div>
              <p className="mt-2 text-xs font-bold leading-4">{item.title}</p>
              <div className="mt-2 flex items-center gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${item.confidence}%` }}
                  />
                </div>
                <span className="text-[10px] font-bold text-muted-foreground">
                  {item.confidence}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section>
        <div className="flex items-center gap-2">
          <FileSpreadsheet className="size-4 text-mint" />
          <h2 className="text-xs font-black uppercase">Evidence ledger</h2>
        </div>
        <div className="mt-3 space-y-2">
          {thread.evidence.map((item) => (
            <div key={item.id} className="rounded-[8px] border border-border bg-card/60 p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-bold">{item.source}</p>
                <span className="text-[10px] font-black text-mint">{item.value}</span>
              </div>
              <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{item.detail}</p>
            </div>
          ))}
        </div>
      </section>
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
  const transport = useMemo(
    () => new DefaultChatTransport({ api: "/api/chat", body: { threadId: thread.id } }),
    [thread.id],
  );
  const { messages, setMessages, sendMessage, status, stop, error } = useChat({
    id: thread.id,
    messages: thread.messages,
    transport,
  });
  // Sync stored thread messages into useChat when the thread changes
  // (handles restoring history on session switch)
  const threadMsgCount = thread.messages.length;
  useEffect(() => {
    if (threadMsgCount > 0 && messages.length === 0) {
      setMessages(thread.messages);
    }
    // Only re-run when the thread identity or stored count changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread.id, threadMsgCount]);
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
  return (
    <section className="flex min-w-0 flex-col overflow-hidden bg-card/25">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 className="font-display text-base font-extrabold">{thread.title}</h2>
          <p className="mt-0.5 text-[10px] font-semibold text-muted-foreground">
            {thread.messages.length} saved messages · Browser storage
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
                    <MessageResponse key={`${message.id}-${index}`}>{part.text}</MessageResponse>
                  ) : null,
                )}
              </MessageContent>
            </Message>
          ))}
          {(status === "submitted" ||
            (status === "streaming" &&
              messages.length > 0 &&
              !textFromMessage(messages[messages.length - 1]))) && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <div className="grid size-7 place-items-center rounded-[8px] bg-primary font-display text-[9px] font-black text-primary-foreground">
                AI
              </div>
              <Shimmer>Examining the evidence…</Shimmer>
            </div>
          )}
          <ConversationScrollButton />
        </ConversationContent>
      </Conversation>
      <div className="border-t border-border bg-card/60 p-3 md:p-4">
        {error && (
          <p
            role="alert"
            className="mb-2 rounded-[6px] bg-accent/10 px-3 py-2 text-xs font-semibold text-accent"
          >
            {error.message ||
              "The investigation could not continue. Your message is still saved locally."}
          </p>
        )}
        <div className="mb-2 flex flex-wrap gap-1.5">
          {[
            "Summarize the strongest evidence",
            "Challenge the leading hypothesis",
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
  const [threads, setThreads] = useState<InvestigationThread[]>(seedThreads);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setThreads(readThreads());
    setReady(true);
  }, []);
  const active = threads.find((thread) => thread.id === threadId) ?? threads[0];
  useEffect(() => {
    if (ready && active && active.id !== threadId)
      void navigate({
        to: "/investigations/$threadId",
        params: { threadId: active.id },
        replace: true,
      });
  }, [active, navigate, ready, threadId]);
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
    if (id === threadId && next[0])
      void navigate({ to: "/investigations/$threadId", params: { threadId: next[0].id } });
  };
  if (!active) return null;
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
          <CheckCircle2 className="size-4 text-mint" /> Topic history saved locally
        </div>
      </header>
      <div className="flex-1 min-h-0 px-5 pb-7 md:px-8">
        <div className="glass overflow-hidden h-full">
          <div className="grid h-full lg:grid-cols-[250px_minmax(0,1fr)] xl:grid-cols-[250px_minmax(420px,1fr)_290px]">
            <ThreadList
              threads={threads}
              activeId={active.id}
              onNew={createThread}
              onDelete={deleteThread}
            />
            <ChatPanel key={active.id} thread={active} onMessages={updateMessages} />
            <EvidencePanel thread={active} />
          </div>
        </div>
      </div>
    </div>
  );
}
