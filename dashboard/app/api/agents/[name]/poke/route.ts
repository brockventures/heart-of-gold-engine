import { NextRequest, NextResponse } from "next/server";
import { agentFetch, isAuthenticated, unauthorizedResponse } from "@/lib/api";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;

  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  try {
    const body = await request.json();
    const { message, channel } = body;

    if (!message) {
      return NextResponse.json({ error: "message required" }, { status: 400 });
    }

    const response = await agentFetch("/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent: name,
        channel: channel || "general",
        channel_id: "dashboard",
        author: "dashboard",
        author_id: "dashboard",
        content: message,
        message_id: `dashboard-poke-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        mentions_agent: true,
      }),
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to send message" },
      { status: 500 }
    );
  }
}
