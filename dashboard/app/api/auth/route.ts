import { NextRequest, NextResponse } from "next/server";
import * as crypto from "crypto";

const DASHBOARD_USER = process.env.DASHBOARD_USER || "admin";
const DASHBOARD_PASSWORD = process.env.DASHBOARD_PASSWORD || "";
const SESSION_SECRET = process.env.SESSION_SECRET || crypto.randomBytes(32).toString('hex');

// Generate a signed session token (HMAC-SHA256)
function generateSessionToken(username: string): string {
  const timestamp = Math.floor(Date.now() / 1000);
  const data = `${username}:${timestamp}`;
  const signature = crypto
    .createHmac('sha256', SESSION_SECRET)
    .update(data)
    .digest('hex');
  return Buffer.from(`${data}:${signature}`).toString('base64');
}

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

