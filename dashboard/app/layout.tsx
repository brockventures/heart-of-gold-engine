import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Karakos Dashboard",
  description: "Agent system monitoring and control",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="m-0 font-sans bg-gray-950 text-gray-100">
        <div className="flex min-h-screen">
          <nav className="w-52 p-4 border-r border-gray-800 flex-shrink-0">
            <h2 className="text-lg mb-6 text-white font-semibold">Karakos</h2>
            <ul className="list-none p-0 m-0 space-y-2">
              {[
                { href: "/", label: "Home" },
                { href: "/agents", label: "Agents" },
                { href: "/conversations", label: "Conversations" },
                { href: "/chat", label: "Chat" },
                { href: "/system", label: "System" },
                { href: "/settings", label: "Settings" },
              ].map(({ href, label }) => (
                <li key={href}>
                  <a
                    href={href}
                    className="text-gray-400 hover:text-gray-200 no-underline text-sm block transition-colors"
                  >
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <main className="flex-1 p-6 overflow-auto">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
