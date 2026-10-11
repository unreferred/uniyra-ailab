import React from "react";
import { Link } from "react-router-dom";
import { useProjectOverview } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { EmptyState, LoadingSpinner, ErrorNote, Metric } from "@/components/common/Common";
import { LayoutDashboard } from "lucide-react";
import { fmtCost } from "@/lib/format";
import { apiError } from "@/lib/api";

export const OverviewTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const { data, isLoading, isError, error } = useProjectOverview(projectId) as {
    data: any; isLoading: boolean; isError: boolean; error: any;
  };
  if (isLoading) return <LoadingSpinner label="Loading overview…" />;
  if (isError) return <ErrorNote message={apiError(error)} />;
  if (!data) return <EmptyState icon={<LayoutDashboard className="h-6 w-6" />} title="No overview" description="Overview data is unavailable for this project." testId="project-overview-empty" />;

  const counts: Record<string, number> = data.counts || {};
  const knownCost = data.known_cost;
  const activity: any[] = data.recent_activity || [];
  const convs: any[] = data.recent_conversations || [];
  const exps: any[] = data.recent_experiments || [];

  return (
    <div className="space-y-4" data-testid="project-overview">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {Object.entries(counts).map(([k, v]) => (
          <Card key={k} className="p-3" data-testid={`overview-count-${k}`}>
            <Metric label={k.replace(/_/g, " ")} value={v == null ? "Unknown" : String(v)} />
          </Card>
        ))}
        <Card className="p-3" data-testid="overview-known-cost">
          <Metric label="known cost" value={knownCost == null ? "Unknown" : fmtCost(knownCost, true)} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4" data-testid="overview-recent-activity">
          <h3 className="mb-2 font-display text-sm font-semibold">Recent activity</h3>
          {activity.length === 0 ? (
            <p className="text-xs text-muted-foreground">No activity recorded yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {activity.slice(0, 10).map((a: any, i: number) => (
                <li key={a.id || i} className="text-xs">
                  <span className="font-mono text-muted-foreground">{a.activity_type || a.type || "event"}</span>
                  {a.message && <span className="ml-2">{a.message}</span>}
                  {a.created_at && <span className="ml-2 text-muted-foreground">{new Date(a.created_at).toLocaleString()}</span>}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4">
          <Card className="p-4" data-testid="overview-recent-conversations">
            <h3 className="mb-2 font-display text-sm font-semibold">Recent conversations</h3>
            {convs.length === 0 ? (
              <p className="text-xs text-muted-foreground">No conversations yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {convs.map((c: any) => (
                  <li key={c.id} className="text-xs">
                    <Link className="text-primary hover:underline" to={`/workspace?conv=${c.id}`}>{c.title || c.id}</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="p-4" data-testid="overview-recent-experiments">
            <h3 className="mb-2 font-display text-sm font-semibold">Recent experiments</h3>
            {exps.length === 0 ? (
              <p className="text-xs text-muted-foreground">No experiments yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {exps.map((e: any) => (
                  <li key={e.id} className="text-xs">
                    <Link className="text-primary hover:underline" to={`/experiments/${e.id}`}>{e.title || e.id}</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
