"use client";

import { usePoll } from "@/lib/hooks";

interface ComponentHealth {
  name: string;
  status: "healthy" | "stale" | "unknown";
  last_check?: string;
}

interface SystemData {
  components: ComponentHealth[];
  uptime_seconds: number;
}

export default function SystemPage() {
  const { data, loading } = usePoll<SystemData>("/api/health", 15000);

  if (loading) {
    return <p className="text-gray-400">Loading...</p>;
  }

  if (!data) {
    return <p className="text-red-400">Unable to fetch system data</p>;
  }

  const components = data.components || [];

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-4">System Health</h1>

      <div className="mb-8">
        <h2 className="text-xl font-semibold mb-4">Components</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {components.map((comp) => (
            <div
              key={comp.name}
              className="bg-gray-900 border border-gray-800 rounded-lg p-4"
            >
              <div className="flex justify-between items-center mb-2">
                <h3 className="font-semibold">{comp.name}</h3>
                <span
                  className={`w-3 h-3 rounded-full ${
                    comp.status === "healthy"
                      ? "bg-green-500"
                      : comp.status === "stale"
                      ? "bg-red-500"
                      : "bg-gray-500"
                  }`}
                />
              </div>
              <p className="text-sm text-gray-400">
                {comp.status === "healthy"
                  ? "Operational"
                  : comp.status === "stale"
                  ? "Not responding"
                  : "Unknown"}
              </p>
              {comp.last_check && (
                <p className="text-xs text-gray-500 mt-1">
                  Last check: {new Date(comp.last_check).toLocaleTimeString()}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-xl font-semibold mb-4">System Info</h2>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <p className="text-sm text-gray-400">Uptime</p>
          <p className="text-2xl font-semibold">
            {Math.floor(data.uptime_seconds / 3600)}h{" "}
            {Math.floor((data.uptime_seconds % 3600) / 60)}m
          </p>
        </div>
      </div>
    </div>
  );
}
