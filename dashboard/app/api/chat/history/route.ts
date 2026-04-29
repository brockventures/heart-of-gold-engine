import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated, unauthorizedResponse } from "@/lib/api";
import { join } from "path";

const WORKSPACE_ROOT = process.env.WORKSPACE_ROOT || "/workspace";

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  const url = request.nextUrl;
  const agent = url.searchParams.get("agent");
  const limit = parseInt(url.searchParams.get("limit") || "50", 10);

  if (!agent) {
    return NextResponse.json({ error: "Missing agent" }, { status: 400 });
  }

  try {
    const dbPath = join(WORKSPACE_ROOT, "data/memory/agent-server.db");
    const sqlite3 = await import("sqlite3").then((m) => m.default);
    const { open } = await import("sqlite");
    const db = await open({ filename: dbPath, driver: sqlite3.Database });

    const rows = await db.all<
      Array<{
        message_id: string;
        content: string;
        response: string | null;
        created_at: string;
        processed: number;
      }>
    >(
      `SELECT message_id, content, response, created_at, processed
       FROM message_queue
       WHERE agent = ? AND channel = 'dashboard'
       ORDER BY created_at DESC
       LIMIT ?`,
      agent,
      limit
    );
    await db.close();

    // Reverse so callers get chronological order
    const ordered = rows.reverse();
    const messages: {
      role: "user" | "assistant";
      content: string;
      ts: string;
      messageId: string;
      processed?: number;
    }[] = [];
    for (const row of ordered) {
      if (row.content) {
        messages.push({
          role: "user",
          content: row.content,
          ts: row.created_at,
          messageId: row.message_id,
        });
      }
      // Include in-progress assistant turns (processed=1) as well as completed
      // ones (processed=2) so a mid-stream reload can render the partial reply.
      if (row.processed >= 1) {
        messages.push({
          role: "assistant",
          content: row.response ?? "",
          ts: row.created_at,
          messageId: row.message_id,
          processed: row.processed,
        });
      }
    }

    return NextResponse.json({ messages });
  } catch (error) {
    return NextResponse.json(
      { error: `Failed to load history: ${error instanceof Error ? error.message : "unknown"}` },
      { status: 500 }
    );
  }
}
