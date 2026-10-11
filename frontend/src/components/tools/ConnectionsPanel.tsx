import React from "react";
import { Cable } from "lucide-react";
import { useToolRegistry } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { ScopeBadge, StatusDot, toolTypeLabel } from "@/components/tools/ToolShared";
import { apiError } from "@/lib/api";

type ConnectionStatus = { kind: "online" | "available" | "missing" | "unsupported" | "off"; label: string };

const connectionStatusOf = (t: any): ConnectionStatus => {
  if (t?.available === false) {
    return { kind: "missing", label: String(t?.availability_reason || "Unavailable") };
  }
  if (t?.enabled === false) return { kind: "off", label: "Disabled" };
  return { kind: "available", label: "Available" };
};

const endpointOf = (t: any): string => {
  const endpoint = t?.endpoint || t?.base_url || t?.url || t?.implementation_config?.url_template;
  return endpoint ? String(endpoint) : "Endpoint managed by the tool version";
};

const ConnectionRow: React.FC<{ tool: any }> = ({ tool }) => {
  const status = connectionStatusOf(tool);
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 px-3 py-2 text-xs"
      data-testid={`connection-${tool.id}`}
    >
      <span className="font-medium">{tool.display_name || tool.name}</span>
      <Badge variant="outline" className="text-[10px]">{toolTypeLabel(tool.tool_type)}</Badge>
      <ScopeBadge tool={tool} />
      <span className="flex items-center gap-1.5 text-[11px]">
        <StatusDot kind={status.kind} /> {status.label}
      </span>
      <span className="w-full font-mono text-[10px] text-muted-foreground">{endpointOf(tool)}</span>
    </div>
  );
};

// Extension seam for external integrations: a CONNECTION is the endpoint/service
// relationship; a TOOL exposes the capability; a PROVIDER implements it.
export const ConnectionsPanel: React.FC = () => {
  const query = useToolRegistry(undefined);
  const tools = query.data?.items || [];
  const connections = tools.filter((t: any) => t.tool_type === "http" || t.tool_type === "openapi");
  const openapi = connections.filter((t: any) => t.tool_type === "openapi");
  const http = connections.filter((t: any) => t.tool_type === "http");

  const section = (testId: string, title: string, items: any[]) => (
    <section data-testid={testId}>
      <h3 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">No {title.toLowerCase()} yet.</p>
      ) : (
        <div className="grid gap-2">
          {items.map((t: any) => <ConnectionRow key={t.id} tool={t} />)}
        </div>
      )}
    </section>
  );

  return (
    <div className="space-y-4" data-testid="connections-panel">
      <Card className="space-y-1.5 p-4">
        <h3 className="flex items-center gap-1.5 font-display text-sm font-semibold">
          <Cable className="h-4 w-4" /> Connections
        </h3>
        <p className="text-[11px] text-muted-foreground" data-testid="connections-explainer">
          A CONNECTION is an external endpoint/service relationship (OpenAPI, custom HTTP, future MCP).
          A TOOL is the capability exposed to the model; a PROVIDER implements it.
        </p>
      </Card>

      {query.isLoading ? (
        <LoadingSpinner label="Loading connections…" />
      ) : query.isError ? (
        <ErrorNote message={apiError(query.error)} />
      ) : connections.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="connections-empty">
          No connection tools registered. Import an OpenAPI spec or add a custom HTTP tool.
        </p>
      ) : (
        <>
          {section("connections-openapi", "OpenAPI connections", openapi)}
          {section("connections-http", "Custom HTTP connections", http)}
        </>
      )}

      <Card className="space-y-1 p-4" data-testid="connections-mcp-future">
        <h3 className="font-display text-sm font-semibold">MCP — future extension seam (not implemented)</h3>
        <p className="text-[11px] text-muted-foreground">
          Model Context Protocol connections will appear here when supported.
        </p>
      </Card>
    </div>
  );
};

export default ConnectionsPanel;
