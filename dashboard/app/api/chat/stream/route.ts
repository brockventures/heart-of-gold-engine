import { NextRequest } from "next/server";
import { isAuthenticated } from "@/lib/api";
import { readFile } from "fs/promises";
import { join } from "path";

const WORKSPACE_ROOT = process.env.WORKSPACE_ROOT || "/workspace";

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return new Response("Unauthorized", { status: 401 });
  }

  const searchParams = request.nextUrl.searchParams;
  const messageId = searchParams.get("message_id");

  if (!messageId) {
    return new Response("Missing message_id", { status: 400 });
  }

  const encoder = new TextEncoder();
  let done = false;
  let lastSize = 0;

  const stream = new ReadableStream({
    async start(controller) {
      // Poll for response in the database
      const pollInterval = setInterval(async () => {
        try {
          // Check message queue database for response
          const dbPath = join(WORKSPACE_ROOT, "data/memory/agent-server.db");
          const sqlite3 = await import("sqlite3").then((m) => m.default);
          const { open } = await import("sqlite");

          const db = await open({
            filename: dbPath,
            driver: sqlite3.Database,
          });

          const row = await db.get(
            "SELECT response, processed FROM message_queue WHERE message_id = ?",
            messageId
          );

          if (row && row.response) {
            const response = row.response;
            const chunk = response.substring(lastSize);
            if (chunk) {
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ chunk })}\n\n`));
              lastSize = response.length;
            }

            if (row.processed >= 2) {
              // STATUS_COMPLETE
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ done: true })}\n\n`));
              clearInterval(pollInterval);
              controller.close();
              done = true;
            }
          }

          await db.close();
        } catch (error) {
          console.error("Stream error:", error);
          clearInterval(pollInterval);
          controller.error(error);
        }
      }, 200); // Poll every 200ms — fast enough to feel like real streaming

      // Timeout after 5 minutes
      setTimeout(() => {
        if (!done) {
          clearInterval(pollInterval);
          controller.close();
        }
      }, 300000);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
