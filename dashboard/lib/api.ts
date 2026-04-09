/**
 * Dashboard API utilities — shared fetch helpers for agent server proxy calls.
 */

import * as crypto from "crypto";

const AGENT_SERVER_URL = process.env.AGENT_SERVER_URL || "http://localhost:18791";
const AGENT_SERVER_TOKEN = process.env.AGENT_SERVER_TOKEN || "";
const SESSION_SECRET = process.env.SESSION_SECRET || crypto.randomBytes(32).toString('hex');

/**
 * Fetch from the agent server with bearer token auth.
 */
export async function agentFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = `${AGENT_SERVER_URL}${path}`;
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${AGENT_SERVER_TOKEN}`);

  return fetch(url, {
    ...options,
    headers,
  });
}

/**
 * Check if the request has a valid signed session token.
 * Token format: base64(username:timestamp:hmac_signature)
 */
export function isAuthenticated(cookieValue: string | undefined): boolean {
  if (!cookieValue) return false;

  try {
    const decoded = Buffer.from(cookieValue, 'base64').toString('utf-8');
    const [username, timestamp, signature] = decoded.split(':');

    if (!username || !timestamp || !signature) return false;

    // Verify HMAC signature
    const data = `${username}:${timestamp}`;
    const expectedSignature = crypto
      .createHmac('sha256', SESSION_SECRET)
      .update(data)
      .digest('hex');

    if (signature !== expectedSignature) return false;

    // Check token age (24 hours)
    const ts = parseInt(timestamp, 10);
    if (Math.floor(Date.now() / 1000) - ts > 86400) return false;

    return true;
  } catch {
    return false;
  }
}

/**
 * Return 401 response for unauthenticated requests.
 */
export function unauthorizedResponse(): Response {
  return new Response(JSON.stringify({ error: "Unauthorized" }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}
