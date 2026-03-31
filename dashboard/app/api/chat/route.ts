import { NextRequest, NextResponse } from "next/server";
import { agentFetch, isAuthenticated, unauthorizedResponse } from "@/lib/api";

export async function POST(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  try {
    const body = await request.json();
    const { agent, content } = body;

    if (!agent || !content) {
      return NextResponse.json(
        { error: "Missing agent or content" },
        { status: 400 }
      );
    }

    const ownerName = process.env.OWNER_NAME || "User";
    const ownerId = process.env.OWNER_DISCORD_ID || "0";

    const messageId = `dash-${Date.now()}-${Math.floor(Math.random() * 65536)}`;

    const payload = {
      agent,
      channel: "dashboard",
      channel_id: "0", // Silent — no Discord post
      server: "dashboard",
      author: ownerName,
      author_id: ownerId,
      is_bot: false,
      content,
      message_id: messageId,
      mentions_agent: true,
    };

    const response = await agentFetch("/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(errorData, { status: response.status });
    }

    const data = await response.json();
    return NextResponse.json({ ...data, message_id: messageId });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to send message" },
      { status: 500 }
    );
  }
}
