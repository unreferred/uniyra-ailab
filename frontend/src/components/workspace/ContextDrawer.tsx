import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Eye, EyeOff, AlertTriangle } from "lucide-react";
import { useRunContext } from "@/lib/hooks";
import { ContextComponent } from "@/lib/types";
import { apiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";
import { fmtTokens } from "@/lib/format";
import { KnowledgeProvenance } from "@/components/workspace/KnowledgeProvenance";

interface Group {
  type: string;
  comps: ContextComponent[];
  estimated: number;
  known: number;
}

export const ContextDrawer: React.FC<{
  runId: string | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}> = ({ runId, open, onOpenChange }) => {
  const { data, isLoading, isError, error } = useRunContext(open ? runId : null);
  const [openGroup, setOpenGroup] = useState<Record<string, boolean>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  const snapshot = data?.snapshot;
  const available = data ? data.available !== false : true;

  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, ContextComponent[]>();
    (snapshot?.components || []).forEach((c) => {
      const key = c.component_type || "OTHER";
      const list = map.get(key) || [];
      list.push(c);
      map.set(key, list);
    });
    return Array.from(map.entries())
      .map(([type, comps]) => {
        const sorted = comps.slice().sort((a, b) => (a.ordering ?? 0) - (b.ordering ?? 0));
        return {
          type,
          comps: sorted,
          estimated: sorted.reduce((sum, c) => sum + (c.estimated_tokens ?? 0), 0),
          known: sorted.reduce((sum, c) => sum + (c.token_count ?? 0), 0),
        };
      })
      .sort((a, b) => (a.comps[0]?.ordering ?? 0) - (b.comps[0]?.ordering ?? 0));
  }, [snapshot]);

  const excluded = snapshot?.excluded_components || [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto scrollbar-thin sm:max-w-2xl" data-testid="context-drawer">
        <DialogHeader>
          <DialogTitle className="font-display">Context</DialogTitle>
          <DialogDescription className="break-all">
            {runId ? `run ${runId}` : "Context snapshot"}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-4">
          {isLoading && <LoadingSpinner label="Loading context…" />}
          {isError && <ErrorNote message={apiError(error)} />}
          {!isLoading && data && !available && (
            <ErrorNote message={data.message || "Legacy run — trace unavailable"} />
          )}

          {!isLoading && available && data && snapshot && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {snapshot.strategy && <Badge variant="outline">{snapshot.strategy}</Badge>}
                {snapshot.over_budget && <Badge variant="destructive">over budget</Badge>}
                <Badge variant="secondary">
                  estimated {fmtTokens(snapshot.estimated_total_tokens ?? snapshot.total_context_tokens ?? null)}
                </Badge>
                {data.provider_input_tokens != null && (
                  <Badge variant="outline">provider input {fmtTokens(data.provider_input_tokens)}</Badge>
                )}
              </div>

              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Component</th>
                      <th className="px-3 py-2 text-right">#</th>
                      <th className="px-3 py-2 text-right">Est. tokens</th>
                      <th className="px-3 py-2 text-right">Known</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {groups.map((g) => (
                      <tr key={g.type}>
                        <td className="px-3 py-2 font-medium">{g.type}</td>
                        <td className="px-3 py-2 text-right font-mono">{g.comps.length}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtTokens(g.estimated)}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtTokens(g.known)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {(snapshot.warnings || []).length > 0 && (
                <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
                  {(snapshot.warnings || []).map((w, i) => (
                    <p key={i} className="flex items-start gap-1.5 text-[11px] text-amber-600">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {w}
                    </p>
                  ))}
                </div>
              )}

              <div className="space-y-2">
                <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Included components</h4>
                {groups.map((g) => {
                  const isOpen = !!openGroup[g.type];
                  return (
                    <div key={g.type} className="rounded-md border border-border">
                      <button
                        type="button"
                        data-testid={`context-group-${g.type}`}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
                        onClick={() => setOpenGroup((p) => ({ ...p, [g.type]: !p[g.type] }))}
                      >
                        {isOpen ? (
                          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        )}
                        <span className="flex-1 font-medium">{g.type}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {g.comps.length} · {fmtTokens(g.estimated)}
                        </span>
                      </button>
                      {isOpen && (
                        <div className="space-y-2 border-t border-border p-2">
                          {g.comps.map((c, idx) => {
                            const key = `${g.type}-${idx}`;
                            const show = !!revealed[key];
                            return (
                              <div key={key} className="rounded border border-border/70 bg-card p-2 text-[11px]">
                                <div className="flex flex-wrap items-center gap-2">
                                  {c.component_type === "RETRIEVED_CONTEXT" && <KnowledgeProvenance metadata={c.metadata} testId={"context-drawer-knowledge-provenance-" + key} />}
                                 {c.source_ref && (
                                    <span className="break-all font-mono text-[10px] text-muted-foreground">
                                      {c.source_ref}
                                    </span>
                                  )}
                                  {c.token_count != null && <Badge variant="outline">{c.token_count} tok</Badge>}
                                  {c.truncated && <Badge variant="destructive">truncated{c.truncation_reason ? `: ${c.truncation_reason}` : ""}</Badge>}
                                  <span className="ml-auto font-mono text-[10px] text-muted-foreground">order {c.ordering}</span>
                                </div>
                                {c.content_hash && (
                                  <div className="mt-1 break-all font-mono text-[9px] text-muted-foreground">
                                    hash {c.content_hash.slice(0, 16)}…
                                  </div>
                                )}
                                <div className="mt-1.5">
                                  <button
                                    type="button"
                                    className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                                    onClick={() => setRevealed((p) => ({ ...p, [key]: !p[key] }))}
                                  >
                                    {show ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                                    {show ? "Hide content" : "Reveal content"}
                                  </button>
                                  {show ? (
                                    <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">
                                      {c.content}
                                    </pre>
                                  ) : (
                                    <p className="mt-1 text-[10px] italic text-muted-foreground">
                                      Content hidden ({(c.content || "").length} chars).
                                    </p>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {excluded.length > 0 && (
                <div className="space-y-1">
                  <h4 className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Excluded components</h4>
                  {excluded.map((c, i) => (
                    <div key={i} className="flex items-center justify-between gap-2 rounded border border-border/70 px-3 py-1.5 text-[11px]">
                      <span className="font-medium">{c.component_type}</span>
                      <span className="text-muted-foreground">{c.excluded_reason || "excluded"}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">{fmtTokens(c.estimated_tokens ?? null)}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {!isLoading && available && data && !snapshot && (
            <ErrorNote message="No context snapshot available for this run." />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};
