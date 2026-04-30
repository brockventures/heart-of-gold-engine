import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
import { resolve } from "path";
import { isAuthenticated, unauthorizedResponse } from "@/lib/api";

const execFileP = promisify(execFile);

// Validate agent name to prevent injection into the AppleScript string.
const NAME_RE = /^[a-zA-Z0-9_-]+$/;

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;

  if (!isAuthenticated(request.cookies.get("karakos_session")?.value)) {
    return unauthorizedResponse();
  }

  if (!NAME_RE.test(name)) {
    return NextResponse.json({ error: "Invalid agent name" }, { status: 400 });
  }

  if (process.platform !== "darwin") {
    return NextResponse.json(
      { error: "Open Terminal is macOS-only", platform: process.platform },
      { status: 501 }
    );
  }

  // Repo root: dashboard/ lives one directory below the package root.
  const repoRoot = resolve(process.cwd(), "..");

  // Defense-in-depth: repoRoot comes from process.cwd() so it shouldn't
  // contain quotes/backslashes/$ in practice, but we escape both layers
  // anyway because the value flows through (a) bash single-quoted strings
  // and (b) AppleScript double-quoted strings.
  //
  // Bash single-quote: ' becomes '\''
  const shellEscape = (s: string) => s.replace(/'/g, `'\\''`);
  // AppleScript double-quote: \ -> \\, " -> \"
  const applescriptEscape = (s: string) =>
    s.replace(/\\/g, `\\\\`).replace(/"/g, `\\"`);

  const safeRepoRoot = shellEscape(repoRoot);
  // Agent name already passes NAME_RE — no shell metachars possible.
  const innerCmd = `cd '${safeRepoRoot}' && KARA_AGENT='${name}' KARA_CHANNEL=dashboard ./bin/kara`;
  const cmd = applescriptEscape(innerCmd);

  // Each AppleScript line is a separate -e arg. Embedding newlines in a
  // single quoted arg breaks AppleScript parsing.
  const args = [
    "-e", `tell application "Terminal"`,
    "-e", "activate",
    "-e", `do script "${cmd}"`,
    "-e", "end tell",
  ];

  try {
    await execFileP("osascript", args, { timeout: 5000 });
    return NextResponse.json({ status: "opened", agent: name });
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown";
    return NextResponse.json(
      { error: `Failed to open Terminal: ${message}` },
      { status: 500 }
    );
  }
}
