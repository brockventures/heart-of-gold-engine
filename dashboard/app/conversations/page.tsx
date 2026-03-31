"use client";

import { useState, useEffect } from "react";

interface Message {
  ts: string;
  channel_name: string;
  author_name: string;
  is_bot: boolean;
  content: string;
}

export default function ConversationsPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [channel, setChannel] = useState("");
  const [agent, setAgent] = useState("");
  const [date, setDate] = useState(new Date().toISOString().split("T")[0]);

  useEffect(() => {
    fetchMessages();
  }, [date, channel, agent]);

  const fetchMessages = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ date, limit: "50" });
      if (channel) params.set("channel", channel);
      if (agent) params.set("agent", agent);

      const res = await fetch(`/api/messages?${params}`);
      if (res.ok) {
        const data = await res.json();
        setMessages(data.messages || []);
      }
    } catch (err) {
      console.error("Failed to fetch messages:", err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Conversations</h1>

      <div className="flex gap-4 mb-6">
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="px-3 py-2 bg-gray-900 border border-gray-800 rounded text-gray-100 focus:outline-none focus:border-gray-600"
        />
        <input
          type="text"
          placeholder="Filter by channel"
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          className="px-3 py-2 bg-gray-900 border border-gray-800 rounded text-gray-100 focus:outline-none focus:border-gray-600"
        />
        <input
          type="text"
          placeholder="Filter by agent"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          className="px-3 py-2 bg-gray-900 border border-gray-800 rounded text-gray-100 focus:outline-none focus:border-gray-600"
        />
      </div>

      {loading ? (
        <p className="text-gray-400">Loading...</p>
      ) : (
        <div className="space-y-4">
          {messages.length === 0 ? (
            <p className="text-gray-400">No messages found</p>
          ) : (
            messages.map((msg, idx) => (
              <div key={idx} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <span className={`font-semibold ${msg.is_bot ? "text-blue-400" : "text-green-400"}`}>
                      {msg.author_name}
                    </span>
                    <span className="text-gray-500 text-sm ml-2">
                      in #{msg.channel_name}
                    </span>
                  </div>
                  <span className="text-xs text-gray-500">
                    {new Date(msg.ts).toLocaleTimeString()}
                  </span>
                </div>
                <p className="text-gray-300 whitespace-pre-wrap">{msg.content}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
