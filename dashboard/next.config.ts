import type { NextConfig } from "next";

// `output: "standalone"` trims server.js down to only its runtime deps —
// but Next does NOT copy `.next/static/` or `public/` into the standalone
// folder for you. Skip that copy and every CSS/JS asset the page
// references 404s: page loads, looks completely unstyled, and never
// hydrates (stuck on whatever server-rendered fallback text existed,
// e.g. "Loading..."). See package.json's `postbuild` script, which does
// the copy automatically after every `npm run build` — don't remove it
// without replacing the copy step some other way.
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
