"use client";

import { usePoll } from "@/lib/hooks";
import { useState } from "react";
import AgentModal from "@/app/components/AgentModal";

interface AgentInfo {
  name: string;
  state: string;
  messages_processed: number;
  session_age_seconds?: number;
  token_usage?: { input: number; output: number };
  cost?: number;
  subprocess_alive?: boolean;
  total_pending?: number;
  queue_depths?: Record<string, number>;
  compaction_count?: number;
}

export default function AgentsPage() {
  const {
    data: agentsData,
    loading,
    refetch,
  } = usePoll<{ agents: AgentInfo[] }>("/api/agents", 15000);
  const [resetting, setResetting] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo | null>(null);

  const handleReset = async (agentName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Reset session for ${agentName}?`)) return;

    setResetting(agentName);
    try {
      const res = await fetch(`/api/agents/${agentName}/reset`, {
        method: "POST",
      });
      if (res.ok) {
        refetch();
      } else {
        alert("Failed to reset agent");
      }
    } catch (err) {
      alert("Network error");
    } finally {
      setResetting(null);
    }
  };

  const handleAgentClick = (agent: AgentInfo) => {
    if (agent.state !== "IDLE") {
      setSelectedAgent(agent);
    }
  };

  if (loading) {
    return <p className="text-gray-400">Loading...</p>;
  }

  const agents = agentsData?.agents || [];

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">Agents</h1>

      <div className="space-y-4">
        {agents.map((agent) => {
          const isClickable = agent.state !== "IDLE";
          return (
            <div
              key={agent.name}
              onClick={() => handleAgentClick(agent)}
              className={`bg-gray-900 border border-gray-800 rounded-lg p-6 ${
                isClickable
                  ? "cursor-pointer hover:border-gray-600 transition-colors"
                  : ""
              }`}
            >
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h2 className="text-xl font-semibold">{agent.name}</h2>
                  <div className="flex items-center gap-2 mt-1">
                    <span
                      className={`inline-block text-xs px-2 py-1 rounded-full ${
                        agent.state === "IDLE"
                          ? "bg-green-900 text-green-300"
                          : agent.state === "PROCESSING"
                          ? "bg-yellow-900 text-yellow-300"
                          : "bg-red-900 text-red-300"
                      }`}
                    >
                      {agent.state}
                    </span>
                    {(agent.total_pending ?? 0) > 0 && (
                      <span className="text-xs text-gray-400">
                        {agent.total_pending} queued
                      </span>
                    )}
                    {!agent.subprocess_alive && agent.subprocess_alive !== undefined && (
                      <span className="text-xs text-red-400">subprocess down</span>
                    )}
                  </div>
                </div>
                <button
                  onClick={(e) => handleReset(agent.name, e)}
                  disabled={resetting === agent.name}
                  className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-700 text-white text-sm rounded transition-colors"
                >
                  {resetting === agent.name ? "Resetting..." : "Reset Session"}
                </button>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-gray-400">Messages Processed</p>
                  <p className="text-lg font-semibold">
                    {agent.messages_processed}
                  </p>
                </div>
                {agent.session_age_seconds !== undefined && (
                  <div>
                    <p className="text-gray-400">Session Age</p>
                    <p className="text-lg font-semibold">
                      {Math.floor(agent.session_age_seconds / 60)}m
                    </p>
                  </div>
                )}
                {agent.token_usage && (
                  <div>
                    <p className="text-gray-400">Token Usage</p>
                    <p className="text-lg font-semibold">
                      {(
                        agent.token_usage.input + agent.token_usage.output
                      ).toLocaleString()}
                    </p>
                  </div>
                )}
                {agent.cost !== undefined && (
                  <div>
                    <p className="text-gray-400">Cost</p>
                    <p className="text-lg font-semibold">
                      ${agent.cost.toFixed(2)}
                    </p>
                  </div>
                )}
                {agent.compaction_count !== undefined &&
                  agent.compaction_count > 0 && (
                    <div>
                      <p className="text-gray-400">Compactions</p>
                      <p className="text-lg font-semibold">
                        {agent.compaction_count}
                      </p>
                    </div>
                  )}
              </div>
            </div>
          );
        })}
      </div>

      {selectedAgent && (
        <AgentModal
          agentName={selectedAgent.name}
          agentState={selectedAgent.state}
          onClose={() => setSelectedAgent(null)}
          onAgentChange={() => {
            refetch();
          }}
        />
      )}
    </div>
  );
}
