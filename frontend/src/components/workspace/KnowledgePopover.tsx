import React from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { useKnowledgeCollections } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";

export interface RetrievalOptions {
  top_k?: number;
  score_threshold?: number | null;
}

export const KnowledgePopover: React.FC<{
  projectId: string | null;
  value: string[];
  onChange: (ids: string[]) => void;
  knowledgeEnabled?: boolean;
  onKnowledgeEnabledChange?: (enabled: boolean) => void;
  retrievalOptions?: RetrievalOptions;
  onRetrievalChange?: (o: RetrievalOptions) => void;
  disabled?: boolean;
}> = ({ projectId, value, onChange, knowledgeEnabled = value.length > 0, onKnowledgeEnabledChange, retrievalOptions, onRetrievalChange, disabled }) => {
  const { data, isLoading, isError } = useKnowledgeCollections(projectId);
  const collections = Array.isArray(data) ? data : data?.items || data?.collections || [];

  const toggle = (id: string) =>
    value.includes(id) ? onChange(value.filter((x) => x !== id)) : onChange([...value, id]);

  const topK = retrievalOptions?.top_k;
  const threshold = retrievalOptions?.score_threshold;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={disabled || !projectId} data-testid="workspace-knowledge-button">
          <BookOpen className="mr-1.5 h-3.5 w-3.5" /> Knowledge: {value.length} · {knowledgeEnabled ? "ON" : "OFF"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-0" align="start" data-testid="workspace-knowledge">
        <div className="space-y-2 border-b border-border px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <div>
              <span className="text-xs font-semibold">Workspace Knowledge</span>
              <span className="ml-2 font-mono text-[10px] text-muted-foreground">{knowledgeEnabled ? "ON" : "OFF"}</span>
            </div>
            <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span>{knowledgeEnabled ? "ON" : "OFF"}</span>
              <Switch
                data-testid="knowledge-enabled-switch"
                checked={knowledgeEnabled}
                disabled={disabled || (!knowledgeEnabled && value.length === 0)}
                onCheckedChange={(next) => {
                  if (next && value.length === 0) return;
                  onKnowledgeEnabledChange?.(!!next);
                  if (!next) onChange([]);
                }}
              />
            </label>
          </div>
          <div className="text-[10px] text-muted-foreground" data-testid="knowledge-selected-ids">
            Selected collection IDs: {value.length > 0 ? value.join(", ") : "none"}
          </div>
          {!knowledgeEnabled && <p className="text-[10px] text-muted-foreground">Knowledge retrieval is disabled. Select a collection, then turn it ON.</p>}
          {knowledgeEnabled && value.length === 0 && <p className="text-[10px] text-amber-600">Select at least one collection before sending with Knowledge ON.</p>}
          {value.length > 0 && (
            <div className="flex justify-end">
              <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground" onClick={() => onChange([])}>Clear</button>
            </div>
          )}
        </div>
        <div className="max-h-72 overflow-y-auto p-2 scrollbar-thin">
          {isLoading && (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading collections…
            </div>
          )}
          {isError && <p className="px-2 py-3 text-xs text-destructive">Could not load collections.</p>}
          {!isLoading && !isError && collections.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted-foreground">No knowledge collections in this project.</p>
          )}
          {collections.map((c: any) => (
            <label
              key={c.id}
              data-testid={`knowledge-${c.id}`}
              className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50"
            >
              <Checkbox className="mt-0.5" checked={value.includes(c.id)} onCheckedChange={() => toggle(c.id)} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate font-medium">{c.name}</span>
                  {c.status && <Badge variant="outline" className="px-1 py-0 text-[9px]">{c.status}</Badge>}
                </span>
                {c.description && <span className="mt-0.5 block line-clamp-2 text-muted-foreground">{c.description}</span>}
                {(c.doc_count ?? c.document_count ?? c.chunk_count) != null && (
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    {[c.doc_count ?? c.document_count != null ? `${c.doc_count ?? c.document_count} docs` : null, c.chunk_count != null ? `${c.chunk_count} chunks` : null].filter(Boolean).join(" · ")}
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
        {knowledgeEnabled && value.length > 0 && onRetrievalChange && (
          <div className="space-y-2 border-t border-border p-3" data-testid="knowledge-retrieval-options">
            <span className="text-xs font-semibold">Retrieval options</span>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11px] text-muted-foreground">top_k</label>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={topK ?? ""}
                  onChange={(e) => onRetrievalChange({ ...(retrievalOptions || {}), top_k: e.target.value === "" ? undefined : parseInt(e.target.value, 10) })}
                  className="mt-1 h-8 font-mono text-xs"
                  data-testid="knowledge-top-k"
                  placeholder="5"
                />
              </div>
              <div>
                <label className="text-[11px] text-muted-foreground">score threshold</label>
                <Input
                  type="number"
                  step="0.01"
                  min={0}
                  max={1}
                  value={threshold ?? ""}
                  onChange={(e) => onRetrievalChange({ ...(retrievalOptions || {}), score_threshold: e.target.value === "" ? null : parseFloat(e.target.value) })}
                  className="mt-1 h-8 font-mono text-xs"
                  data-testid="knowledge-threshold"
                  placeholder="optional"
                />
              </div>
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
};
