import { NextRequest, NextResponse } from "next/server";
import { agentFetch, isAuthenticated, unauthorizedResponse } from "@/lib/api";

// Proxies GET /agents on agent-server — agent *configuration* (model,
// max_turns, timeout), not runtime state. Kept separate from
// /api/agents on purpose: that route proxies /status and is what the
// chat page's agent dropdown needs (live state); this route is what the
// settings page needs (config). Neither substitutes for the other — see
// the 2026-08-08 settings-page fix (upstream mcarmody/karakos-package#130)
// this was ported from: the settings page used to poll /api/agents (no
// model/max_turns/timeout fields at all) and read the resulting array
// with Object.entries() as if it were a dict, rendering "0"/"1"/"2" for
// agent names and "undefined" for every config value.
export async function GET(request: NextRequest) {
  if (!isAuthenticated(request.cookies.get("karakos_session")?.value || "")) {
    return unauthorizedResponse();
  }

  try {
    const response = await agentFetch("/agents");
    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch agent config" },
      { status: 500 }
    );
  }
}
