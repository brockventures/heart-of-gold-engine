import { NextRequest, NextResponse } from "next/server";
import { agentFetch, isAuthenticated, unauthorizedResponse } from "@/lib/api";

export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  try {
    const searchParams = request.nextUrl.searchParams;
    const agent = searchParams.get("agent");
    const period = searchParams.get("period") || "daily";

    const path = agent ? `/cost/${agent}?period=${period}` : `/cost?period=${period}`;
    const response = await agentFetch(path);

    if (!response.ok) {
      return NextResponse.json(
        { error: `Failed to fetch cost data: ${response.statusText}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch cost data" },
      { status: 500 }
    );
  }
}
