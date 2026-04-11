import { NextRequest, NextResponse } from "next/server";
import { generateSessionToken } from "@/lib/api";

const DASHBOARD_USER = process.env.DASHBOARD_USER || "admin";
const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD || "";

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
        secure: process.env.NODE_ENV === "production",
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

