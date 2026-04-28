import { NextRequest, NextResponse } from "next/server";
import { agentFetch, isAuthenticated, unauthorizedResponse } from "@/lib/api";

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value || "")) {
    return unauthorizedResponse();
  }

  try {
    const response = await agentFetch("/status");
    const status = await response.json();

    // Reshape /status response into { agents: AgentInfo[] }
    const agents = Object.entries(status).map(([name, info]: [string, any]) => ({
      name,
      state: info.state || "UNKNOWN",
      messages_processed: 0, // Not tracked in /status
      session_age_seconds: undefined,
      token_usage: info.input_tokens
        ? { input: info.input_tokens, output: 0 }
        : undefined,
      cost: info.session_cost ?? undefined,
      subprocess_alive: info.subprocess_alive,
      subprocess_pid: info.subprocess_pid,
      queue_depths: info.queue_depths || {},
      total_pending: info.total_pending || 0,
      session_id: info.session_id,
      compaction_count: info.compaction_count || 0,
    }));

    return NextResponse.json({ agents });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch agents" },
      { status: 500 }
    );
  }
}
