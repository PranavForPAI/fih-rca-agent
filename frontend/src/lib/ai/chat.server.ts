import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
} from "ai";
const FHI_BOT_URL = process.env["FHI_BOT_URL"] || "http://127.0.0.1:8000";

export async function handleChat(request: Request) {
  let payload: { messages?: UIMessage[]; threadId?: string };
  try {
    payload = await request.json();
  } catch {
    return Response.json({ message: "The investigation request was invalid." }, { status: 400 });
  }

  if (!Array.isArray(payload.messages) || typeof payload.threadId !== "string") {
    return Response.json({ message: "Messages and a topic are required." }, { status: 400 });
  }

  // Extract the latest user question
  const lastUserMsg = [...payload.messages].reverse().find((m) => m.role === "user");
  const userText =
    lastUserMsg?.parts
      ?.filter((p): p is { type: "text"; text: string } => p.type === "text")
      .map((p) => p.text)
      .join("\n") || "";

  if (!userText.trim()) {
    return Response.json({ message: "No question provided in the request." }, { status: 400 });
  }

  // Stream UI message SSE chunks using AI SDK v7's standard createUIMessageStream
  const stream = createUIMessageStream({
    originalMessages: payload.messages,
    execute: async ({ writer }) => {
      // Unique ID for the text part within this assistant message
      const textPartId = crypto.randomUUID();

      // Open the text part — required before any text-delta chunks
      writer.write({ type: "text-start", id: textPartId });

      // Periodic SSE heartbeat while the RCA agent executes SQL queries.
      // Sends empty text-delta to keep the connection alive and prevent
      // proxy/browser timeouts on long-running requests.
      const heartbeatInterval = setInterval(() => {
        try {
          writer.write({ type: "text-delta", id: textPartId, delta: "" });
        } catch {
          clearInterval(heartbeatInterval);
        }
      }, 5000);

      try {
        const botResponse = await fetch(`${FHI_BOT_URL}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: payload.threadId,
            message: userText,
            schema_name: "fhi_sales",
          }),
        });

        clearInterval(heartbeatInterval);

        if (!botResponse.ok) {
          const errBody = await botResponse.text();
          writer.write({
            type: "text-delta",
            id: textPartId,
            delta: `⚠️ **Agent Error (${botResponse.status})**: ${errBody}`,
          });
          writer.write({ type: "text-end", id: textPartId });
          writer.write({ type: "finish", finishReason: "error" });
          return;
        }

        const data: {
          session_id: string;
          answer: string;
          queries_used: Array<{ sql: string; row_count: number }>;
          confidence_note?: string | null;
          message_count: number;
        } = await botResponse.json();

        let finalAnswer = data.answer || "No response received from agent.";

        // If SQL queries were executed, append a clean breakdown
        if (data.queries_used && data.queries_used.length > 0) {
          const queryList = data.queries_used
            .map(
              (q, idx) =>
                `**Query ${idx + 1}** (${q.row_count} rows):\n\`\`\`sql\n${q.sql}\n\`\`\``,
            )
            .join("\n\n");
          finalAnswer += `\n\n<details>\n<summary>🔍 <b>Queries Executed (${data.queries_used.length})</b></summary>\n\n${queryList}\n</details>`;
        }

        // Stream the complete response as a single text delta
        writer.write({ type: "text-delta", id: textPartId, delta: finalAnswer });

        // Close the text part and finish the message
        writer.write({ type: "text-end", id: textPartId });
        writer.write({ type: "finish", finishReason: "stop" });
      } catch (error: any) {
        clearInterval(heartbeatInterval);
        writer.write({
          type: "text-delta",
          id: textPartId,
          delta: `⚠️ **Connection Error**: Could not complete request to RCA agent (${error?.message || error}).`,
        });
        writer.write({ type: "text-end", id: textPartId });
        writer.write({ type: "finish", finishReason: "error" });
      }
    },
  });

  return createUIMessageStreamResponse({
    stream,
  });
}
