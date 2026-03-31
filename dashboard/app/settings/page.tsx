"use client";

import { usePoll } from "@/lib/hooks";

interface ConfigData {
  system_name: string;
  version: string;
  owner: string;
  workspace: string;
}

interface AgentConfig {
  agents: Record<string, {
    model: string;
    max_turns: number;
    timeout: number;
  }>;
}

export default function SettingsPage() {
  const { data: config } = usePoll<ConfigData>("/api/health", 60000);
  const { data: agents } = usePoll<AgentConfig>("/api/agents", 60000);

  return (
    <div>
      <h1 style={{ fontSize: "1.3rem", marginBottom: "1rem" }}>Settings</h1>

      <Section title="System">
        <Row label="System Name" value={config?.system_name || "—"} />
        <Row label="Version" value={config?.version || "—"} />
        <Row label="Owner" value={config?.owner || "—"} />
      </Section>

      <Section title="Agent Configuration">
        {agents?.agents && Object.entries(agents.agents).map(([name, cfg]) => (
          <div key={name} style={{ marginBottom: "0.75rem" }}>
            <h3 style={{ fontSize: "0.9rem", margin: "0 0 0.25rem" }}>{name}</h3>
            <Row label="Model" value={cfg.model} />
            <Row label="Max Turns" value={String(cfg.max_turns)} />
            <Row label="Timeout" value={`${cfg.timeout}s`} />
          </div>
        ))}
      </Section>

      <Section title="Integrations">
        <Row label="Discord" value="Connected via relay" />
        <Row label="Dashboard" value="Active (this page)" />
        <Row label="MCP Tools" value="Loaded at agent session start" />
      </Section>

      <p style={{ fontSize: "0.8rem", color: "#555", marginTop: "1rem" }}>
        Configuration files are in <code>config/</code>. Edit <code>config/agents.json</code> to change agent settings.
        Use <code>bin/create-agent.sh</code> to add new agents.
      </p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "#141414", border: "1px solid #222", borderRadius: 8, padding: "1rem", marginBottom: "1rem" }}>
      <h2 style={{ fontSize: "0.95rem", marginBottom: "0.75rem", color: "#888" }}>{title}</h2>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "0.25rem 0", fontSize: "0.85rem" }}>
      <span style={{ color: "#aaa" }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}
