"use client";

import { useState, useEffect, useRef, FormEvent } from "react";
import { usePoll } from "@/lib/hooks";

interface AgentList {
  agents: Record<string, { state: string }>;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts: string;
}

export default function ChatPage() {
  const { data: agentData } = usePoll<AgentList>("/api/agents", 30000);
  const [agent, setAgent] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloadMsg, setReloadMsg] = useState<string | null>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);

  const agents = agentData?.agents ? Object.keys(agentData.agents) : [];

  // Set default agent
  useEffect(() => {
    if (agents.length > 0 && !agent) {
      setAgent(agents[0]);
    }
  }, [agents, agent]);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleReload() {
    if (!agent || reloading || streaming) return;
    setReloading(true);
    setReloadMsg(`Reloading ${agent}…`);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent)}/reload`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Request failed" }));
        setReloadMsg(`Reload failed: ${err.error || res.statusText}`);
      } else {
        setReloadMsg(`${agent} reloaded — session preserved.`);
        setTimeout(() => setReloadMsg(null), 4000);
      }
    } catch (err) {
      setReloadMsg(`Reload error: ${err instanceof Error ? err.message : "unknown"}`);
    } finally {
      setReloading(false);
    }
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || !agent || streaming) return;

    const userMsg: ChatMessage = { role: "user", content: input, ts: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg]);
    const userInput = input;
    setInput("");
    setStreaming(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, content: userInput }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Request failed" }));
        setMessages((prev) => [...prev, { role: "assistant", content: `Error: ${err.error}`, ts: new Date().toISOString() }]);
        setStreaming(false);
        return;
      }

      const data = await res.json();
      const messageId = data.message_id;

      // Add placeholder for streaming response
      setMessages((prev) => [...prev, { role: "assistant", content: "", ts: new Date().toISOString() }]);

      // Subscribe to SSE stream
      const eventSource = new EventSource(`/api/chat/stream?message_id=${messageId}`);

      eventSource.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.done) {
          eventSource.close();
          setStreaming(false);
        } else if (payload.chunk) {
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1 && m.role === "assistant"
                ? { ...m, content: m.content + payload.chunk }
                : m
            )
          );
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
        setStreaming(false);
      };
    } catch (err) {
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: `Connection error: ${err instanceof Error ? err.message : "unknown"}`,
        ts: new Date().toISOString(),
      }]);
      setStreaming(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-4 mb-4">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <select
          value={agent}
          onChange={(e) => { setAgent(e.target.value); setMessages([]); }}
          className="px-3 py-2 bg-gray-900 border border-gray-800 rounded text-gray-100 focus:outline-none focus:border-gray-600"
        >
          {agents.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={handleReload}
          disabled={reloading || streaming || !agent}
          title="Bounce subprocess, preserve session — picks up SYSTEM_PROMPT / persona / MCP changes"
          className="px-3 py-2 bg-gray-900 hover:bg-gray-800 disabled:opacity-50 border border-gray-800 rounded text-gray-300 text-sm transition-colors"
        >
          {reloading ? "Reloading…" : "↻ Reload"}
        </button>
        {reloadMsg && (
          <span className="text-xs text-gray-400">{reloadMsg}</span>
        )}
      </div>

      <div className="flex-1 overflow-auto py-2 mb-4">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`p-3 mb-2 rounded-lg ${
              msg.role === "assistant"
                ? "bg-gray-900 border border-gray-800"
                : "bg-gray-950"
            }`}
          >
            <strong className={`text-xs block mb-1 ${
              msg.role === "user" ? "text-gray-100" : "text-blue-400"
            }`}>
              {msg.role === "user" ? "You" : agent}
            </strong>
            <p className="text-sm whitespace-pre-wrap text-gray-300">
              {msg.content}
              {streaming && i === messages.length - 1 && msg.role === "assistant" && (
                <span className="opacity-50">▌</span>
              )}
            </p>
          </div>
        ))}
        <div ref={messagesEnd} />
      </div>

      <form onSubmit={handleSend} className="flex gap-2 pt-2 border-t border-gray-800">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Message ${agent || "agent"}...`}
          disabled={streaming}
          className="flex-1 px-3 py-2 bg-gray-900 border border-gray-800 rounded text-gray-100 text-sm focus:outline-none focus:border-gray-600 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={streaming || !input.trim()}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-white rounded transition-colors"
        >
          Send
        </button>
      </form>
    </div>
  );
}
