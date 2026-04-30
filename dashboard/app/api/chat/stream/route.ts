import { NextRequest } from "next/server";
import { isAuthenticated } from "@/lib/api";
import { join } from "path";

const WORKSPACE_ROOT = process.env.WORKSPACE_ROOT || "/workspace";

// Mirror of bin/agent-server.py status constants.
const STATUS_QUEUED = 0;
const STATUS_IN_PROGRESS = 1;
const STATUS_COMPLETE = 2;
const STATUS_CRASHED = 3;
const STATUS_SKIPPED = 4;

const POLL_INTERVAL_MS = 200;
const STREAM_TIMEOUT_MS = 5 * 60 * 1000;

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return new Response("Unauthorized", { status: 401 });
  }

  const messageId = request.nextUrl.searchParams.get("message_id");
  if (!messageId) {
    return new Response("Missing message_id", { status: 400 });
  }

  const encoder = new TextEncoder();
  const dbPath = join(WORKSPACE_ROOT, "data/memory/agent-server.db");

  // Hoisted so cancel() can hit the same cleanup path as start() when
  // the client disconnects mid-stream.
  let cleanup: (reason: string) => Promise<void> = async () => {};

  const stream = new ReadableStream({
    async start(controller) {
      // Lazy-load to keep cold-start light.
      const sqlite3 = await import("sqlite3").then((m) => m.default);
      const { open } = await import("sqlite");

      // Single connection reused across polls — avoids file-handle churn
      // and per-tick "open/close" cost. Closed in cleanup().
      let db;
      try {
        db = await open({ filename: dbPath, driver: sqlite3.Database });
      } catch (err) {
        controller.error(err);
        return;
      }

      let lastSize = 0;
      let polling = false;     // overlap guard — skip ticks if previous still in flight
      let closed = false;
      let pollHandle: ReturnType<typeof setInterval> | null = null;
      let timeoutHandle: ReturnType<typeof setTimeout> | null = null;

      const send = (payload: unknown) => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`));
        } catch {
          // Controller may be closed — caller has already disconnected.
          // Cleanup will be triggered via the cancel handler.
        }
      };

      cleanup = async (_reason: string) => {
        if (closed) return;
        closed = true;
        if (pollHandle) clearInterval(pollHandle);
        if (timeoutHandle) clearTimeout(timeoutHandle);
        try {
          await db.close();
        } catch {
          // best-effort
        }
        try {
          controller.close();
        } catch {
          // controller may already be closed
        }
      };

      const poll = async () => {
        if (polling || closed) return;
        polling = true;
        try {
          const row = await db.get(
            "SELECT response, processed FROM message_queue WHERE message_id = ?",
            messageId
          );

          if (!row) return;

          const response: string = row.response || "";
          if (response.length > lastSize) {
            send({ chunk: response.substring(lastSize) });
            lastSize = response.length;
          }

          const processed = row.processed as number;
          if (processed === STATUS_QUEUED || processed === STATUS_IN_PROGRESS) {
            return; // keep polling
          }

          // Terminal status — emit a typed event so the client can
          // distinguish a clean finish from a crash.
          if (processed === STATUS_COMPLETE) {
            send({ done: true, status: "complete" });
            await cleanup("complete");
          } else if (processed === STATUS_CRASHED) {
            send({ done: true, status: "crashed", error: "Agent crashed" });
            await cleanup("crashed");
          } else if (processed === STATUS_SKIPPED) {
            send({ done: true, status: "skipped" });
            await cleanup("skipped");
          } else {
            send({ done: true, status: `unknown:${processed}` });
            await cleanup("unknown");
          }
        } catch (err) {
          if (closed) return;
          console.error("Stream poll error:", err);
          send({ done: true, status: "error", error: String(err) });
          await cleanup("error");
        } finally {
          polling = false;
        }
      };

      pollHandle = setInterval(poll, POLL_INTERVAL_MS);
      timeoutHandle = setTimeout(() => {
        if (!closed) {
          send({ done: true, status: "timeout" });
          void cleanup("timeout");
        }
      }, STREAM_TIMEOUT_MS);

      // Kick off an immediate first poll instead of waiting for the interval.
      void poll();
    },

    async cancel() {
      // Client disconnected before we hit a terminal status — close the
      // db handle and cancel the poll interval so we don't leak resources.
      await cleanup("client-cancel");
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
