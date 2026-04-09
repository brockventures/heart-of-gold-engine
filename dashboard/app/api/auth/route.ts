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

// Verify and decode a session token
function verifySessionToken(token: string): { username: string; timestamp: number } | null {
  try {
    const decoded = Buffer.from(token, 'base64').toString('utf-8');
    const [username, timestamp, signature] = decoded.split(':');

    if (!username || !timestamp || !signature) {
      return null;
    }

    const data = `${username}:${timestamp}`;
    const expectedSignature = crypto
      .createHmac('sha256', SESSION_SECRET)
      .update(data)
      .digest('hex');

    if (signature !== expectedSignature) {
      return null;
    }

    const ts = parseInt(timestamp, 10);
    // Check token age (24 hours)
    if (Math.floor(Date.now() / 1000) - ts > 86400) {
      return null;
    }

    return { username, timestamp: ts };
  } catch {
    return null;
  }
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

// Export session verification for use in middleware
export { verifySessionToken };
