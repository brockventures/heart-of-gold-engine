"use client";

import { useEffect, useState, useCallback, useRef } from "react";

interface QueueMessage {
  id: number;
  channel: string;
  author: string;
  content: string;
  content_full_length: number;
  created_at: string;
  state: "pending" | "processing";
}

interface AgentModalProps {
  agentName: string;
  agentState: string;
  onClose: () => void;
  onAgentChange: () => void;
}

export default function AgentModal({
  agentName,
  agentState,
  onClose,
  onAgentChange,
}: AgentModalProps) {
  const [queue, setQueue] = useState<QueueMessage[]>([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [pokeOpen, setPokeOpen] = useState(false);
  const [pokeMessage, setPokeMessage] = useState("");
  const [pokeChannel, setPokeChannel] = useState("general");
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  const fetchQueue = useCallback(async () => {
    try {
      const res = await fetch(`/api/agents/${agentName}/queue`);
      if (res.ok) {
        const data = await res.json();
        setQueue(data.messages || []);
      }
    } catch {
      // ignore
    } finally {
      setQueueLoading(false);
    }
  }, [agentName]);

  // Fetch queue on mount and poll every 5s
  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, 5000);
    return () => clearInterval(interval);
  }, [fetchQueue]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  // Close on backdrop click
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
      onClose();
    }
  };

  const showFeedback = (type: "ok" | "err", text: string) => {
    setFeedback({ type, text });
    setTimeout(() => setFeedback(null), 3000);
  };

  const handleInterrupt = async () => {
    setActionPending("interrupt");
    try {
      const res = await fetch(`/api/agents/${agentName}/interrupt`, {
        method: "POST",
      });
      if (res.ok) {
        showFeedback("ok", "Interrupted");
        onAgentChange();
      } else {
        showFeedback("err", "Interrupt failed");
      }
    } catch {
      showFeedback("err", "Network error");
    } finally {
      setActionPending(null);
    }
  };

  const handleReload = async () => {
    setActionPending("reload");
    try {
      const res = await fetch(`/api/agents/${agentName}/reload`, {
        method: "POST",
      });
      if (res.ok) {
        showFeedback("ok", "Reloading...");
        onAgentChange();
      } else {
        showFeedback("err", "Reload failed");
      }
    } catch {
      showFeedback("err", "Network error");
    } finally {
      setActionPending(null);
    }
  };

  const handlePoke = async () => {
    if (!pokeMessage.trim()) return;
    setActionPending("poke");
    try {
      const res = await fetch(`/api/agents/${agentName}/poke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: pokeMessage.trim(),
          channel: pokeChannel,
        }),
      });
      if (res.ok) {
        showFeedback("ok", "Message sent");
        setPokeMessage("");
        setPokeOpen(false);
        fetchQueue();
      } else {
        showFeedback("err", "Send failed");
      }
    } catch {
      showFeedback("err", "Network error");
    } finally {
      setActionPending(null);
    }
  };

  const handleDeleteQueueItem = async (messageId: number) => {
    try {
      const res = await fetch(`/api/agents/${agentName}/queue`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messageId }),
      });
      if (res.ok) {
        setQueue((prev) => prev.filter((m) => m.id !== messageId));
      } else {
        const data = await res.json();
        showFeedback("err", data.error || "Delete failed");
      }
    } catch {
      showFeedback("err", "Network error");
    }
  };

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso + "Z");
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={handleBackdropClick}
    >
      <div
        ref={modalRef}
        className="bg-gray-900 border border-gray-700 rounded-lg w-full max-w-lg max-h-[80vh] flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold">{agentName}</h2>
            <span
              className={`text-xs px-2 py-1 rounded-full ${
                agentState === "IDLE"
                  ? "bg-green-900 text-green-300"
                  : agentState === "PROCESSING"
                  ? "bg-yellow-900 text-yellow-300"
                  : "bg-red-900 text-red-300"
              }`}
            >
              {agentState}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-xl leading-none"
          >
            &times;
          </button>
        </div>

        {/* Feedback banner */}
        {feedback && (
          <div
            className={`px-4 py-2 text-sm ${
              feedback.type === "ok"
                ? "bg-green-900/50 text-green-300"
                : "bg-red-900/50 text-red-300"
            }`}
          >
            {feedback.text}
          </div>
        )}

        {/* Actions */}
        <div className="p-4 border-b border-gray-800 flex flex-wrap gap-2">
          <button
            onClick={handleInterrupt}
            disabled={actionPending !== null}
            className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 disabled:bg-gray-700 text-white text-sm rounded transition-colors"
          >
            {actionPending === "interrupt" ? "..." : "Interrupt"}
          </button>
          <button
            onClick={handleReload}
            disabled={actionPending !== null}
            className="px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:bg-gray-700 text-white text-sm rounded transition-colors"
          >
            {actionPending === "reload" ? "..." : "Reload"}
          </button>
          <button
            onClick={() => setPokeOpen(!pokeOpen)}
            disabled={actionPending !== null}
            className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 text-white text-sm rounded transition-colors"
          >
            Send Message
          </button>
        </div>

        {/* Poke input */}
        {pokeOpen && (
          <div className="px-4 py-3 border-b border-gray-800 space-y-2">
            <div className="flex gap-2">
              <select
                value={pokeChannel}
                onChange={(e) => setPokeChannel(e.target.value)}
                className="bg-gray-800 text-gray-200 text-sm rounded px-2 py-1.5 border border-gray-700"
              >
                <option value="general">#general</option>
                <option value="signals">#signals</option>
                <option value="urgent">#urgent</option>
                <option value="staff-comms">#staff-comms</option>
                <option value="silent">silent</option>
              </select>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={pokeMessage}
                onChange={(e) => setPokeMessage(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handlePoke()}
                placeholder="Message..."
                className="flex-1 bg-gray-800 text-gray-200 text-sm rounded px-3 py-1.5 border border-gray-700 placeholder-gray-500 focus:outline-none focus:border-gray-500"
                autoFocus
              />
              <button
                onClick={handlePoke}
                disabled={actionPending === "poke" || !pokeMessage.trim()}
                className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 text-white text-sm rounded transition-colors"
              >
                {actionPending === "poke" ? "..." : "Send"}
              </button>
            </div>
          </div>
        )}

        {/* Queue */}
        <div className="flex-1 overflow-y-auto p-4">
          <h3 className="text-sm text-gray-400 mb-2">
            Message Queue
            {!queueLoading && (
              <span className="ml-2 text-gray-500">({queue.length})</span>
            )}
          </h3>

          {queueLoading ? (
            <p className="text-gray-500 text-sm">Loading...</p>
          ) : queue.length === 0 ? (
            <p className="text-gray-500 text-sm">Queue empty</p>
          ) : (
            <div className="space-y-2">
              {queue.map((msg) => (
                <div
                  key={msg.id}
                  className="bg-gray-800 rounded p-3 text-sm group"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-gray-300 font-medium">
                          {msg.author}
                        </span>
                        <span className="text-gray-500 text-xs">
                          #{msg.channel}
                        </span>
                        <span className="text-gray-600 text-xs">
                          {formatTime(msg.created_at)}
                        </span>
                        {msg.state === "processing" && (
                          <span className="text-yellow-400 text-xs">
                            processing
                          </span>
                        )}
                      </div>
                      <p className="text-gray-400 truncate">{msg.content}</p>
                      {msg.content_full_length > 200 && (
                        <span className="text-gray-600 text-xs">
                          ...{msg.content_full_length} chars
                        </span>
                      )}
                    </div>
                    {msg.state === "pending" && (
                      <button
                        onClick={() => handleDeleteQueueItem(msg.id)}
                        className="text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-lg leading-none shrink-0"
                        title="Remove from queue"
                      >
                        &times;
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
