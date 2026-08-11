import { NextRequest, NextResponse } from "next/server";
import { generateSessionToken } from "@/lib/api";

const DASHBOARD_USER = process.env.DASHBOARD_USER || "admin";
const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD || "";
// NODE_ENV is always "production" inside a `next build` standalone server
// regardless of the host's actual environment, so gating the cookie's
// Secure flag on NODE_ENV effectively hardcodes it to true. That's correct
// behind real TLS, but this dashboard is also deployed bare over plain
// http://localhost with no TLS termination in front (native/WSL install,
// no reverse proxy) -- a Secure cookie there gets silently dropped or
// refused by the browser, which breaks login in a way that looks like an
// infinite "Loading..." rather than a clear auth error. Opt-in via an
// explicit var instead, default false, so a real HTTPS deployment can
// still turn it on.
const SECURE_COOKIE = process.env.DASHBOARD_SECURE_COOKIE === "true";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { username, password } = body;

    if (!username || !password) {
      return NextResponse.json(
        { error: "Missing credentials" },
        { status: 400 }
      );
    }

    if (username === DASHBOARD_USER && password === DASHBOARD_PASSWORD) {
      const response = NextResponse.json({ success: true });

      // Generate signed session token
      const sessionToken = generateSessionToken(username);

      // Set session cookie with signed token (24h, httpOnly)
      response.cookies.set("karakos_session", sessionToken, {
        httpOnly: true,
        secure: SECURE_COOKIE,
        sameSite: "strict",
        maxAge: 86400, // 24 hours
        path: "/",
      });

      return response;
    }

    return NextResponse.json(
      { error: "Invalid credentials" },
      { status: 401 }
    );
  } catch (error) {
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}

