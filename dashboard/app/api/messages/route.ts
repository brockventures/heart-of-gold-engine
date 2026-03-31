import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated, unauthorizedResponse } from "@/lib/api";
import { readdir, readFile } from "fs/promises";
import { join } from "path";

const DATA_DIR = process.env.WORKSPACE_ROOT
  ? join(process.env.WORKSPACE_ROOT, "data/messages")
  : "/workspace/data/messages";

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  try {
    const searchParams = request.nextUrl.searchParams;
    const date = searchParams.get("date") || new Date().toISOString().split("T")[0];
    const channel = searchParams.get("channel");
    const agent = searchParams.get("agent");
    const limit = parseInt(searchParams.get("limit") || "50", 10);
    const offset = parseInt(searchParams.get("offset") || "0", 10);

    const filename = `messages-${date}.jsonl`;
    const filePath = join(DATA_DIR, filename);

    let lines: any[] = [];
    try {
      const content = await readFile(filePath, "utf-8");
      lines = content
        .split("\n")
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l));
    } catch (err) {
      // File doesn't exist for this date
      return NextResponse.json({ messages: [], total: 0 });
    }

    // Apply filters
    let filtered = lines;
    if (channel) {
      filtered = filtered.filter((m) => m.channel_name === channel);
    }
    if (agent) {
      filtered = filtered.filter((m) => m.author_name === agent || m.is_bot);
    }

    // Sort newest first
    filtered.sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());

    const paginated = filtered.slice(offset, offset + limit);

    return NextResponse.json({
      messages: paginated,
      total: filtered.length,
      offset,
      limit,
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to read messages" },
      { status: 500 }
    );
  }
}
