import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Fingerprint, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CopyButton, ErrorNote } from "@/components/common/Common";
import { nNumber, nText, shortHash } from "./format";
import { ProvenanceResponse } from "./types";

export const ProvenancePanel: React.FC<{ experimentId: string; enabled: boolean }> = ({ experimentId, enabled }) => {
  const { data, isLoading, isError, error } = useQuery<ProvenanceResponse>({
    queryKey: ["experiment-provenance", experimentId],
    queryFn: async () => (await api.get(`/experiments/${experimentId}/provenance`)).data,
    enabled: enabled && Boolean(experimentId),
  });

  return (
    <Card className="space-y-4 p-4" data-testid="provenance-panel">
      <div className="flex items-center gap-2">
        <Fingerprint className="h-4 w-4 text-brand" />
        <span className="font-display font-semibold">Provenance</span>
        {data?.legacy && <Badge variant="outline" className="text-[10px]">legacy</Badge>}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading provenance…</div>
      ) : isError ? (
        <ErrorNote message={(error as any)?.message || "Could not load provenance."} />
      ) : !data ? null : data.legacy ? (
        <p className="text-sm text-muted-foreground">
          This experiment was created by the legacy Arena flow. It has no frozen protocol, configuration hashes, or dataset snapshot.
        </p>
      ) : (
        <div className="space-y-4 text-sm">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Dataset version" value={data.dataset_version ? `v${data.dataset_version.version} · ${nText(data.dataset_version.status)}` : "n/a"} />
            <Field label="Dataset cases" value={nNumber(data.dataset_version?.case_count ?? null)} />
            <Field label="Repetitions" value={nNumber(data.repetitions ?? null)} />
            <Field label="Runner version" value={nText(data.runner_version)} />
            <Field label="Schema version" value={nText(data.schema_version)} />
            <Field label="Created" value={nText(data.created_at)} />
            <Field label="Ready at" value={nText(data.ready_at)} />
            <Field label="Updated" value={nText(data.updated_at)} />
          </div>

          <div className="grid gap-2 rounded-md border border-border bg-muted/30 p-3 text-xs" data-testid="provenance-hashes">
            <HashRow label="Protocol hash" value={data.protocol_hash || data.hashes?.protocol_hash} />
            <HashRow label="Configuration hash" value={data.configuration_hash || data.hashes?.configuration_hash} />
            <HashRow label="Dataset version hash" value={data.dataset_version_hash || data.hashes?.dataset_version_hash || data.dataset_version?.content_hash} />
          </div>

          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Rubric &amp; judge snapshot</h4>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Rubric" value={data.rubric_snapshot?.name || "n/a"} />
              <Field label="Judge model" value={data.judge_model_snapshot?.display_name || data.judge_model_snapshot?.model_identifier || "n/a"} />
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Configurations</h4>
            <div className="space-y-2">
              {(data.configurations || []).map((c: any) => (
                <div key={c.configuration_id || c.label} className="rounded-md border border-border p-3 text-xs" data-testid={`provenance-config-${c.configuration_id || c.label}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{c.label || "n/a"}</span>
                    {c.is_control && <Badge variant="secondary" className="text-[10px]">control</Badge>}
                    <span className="text-muted-foreground">{c.model_snapshot?.display_name || c.model_id || "n/a"}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{c.model_snapshot?.model_identifier || "n/a"}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-muted-foreground">
                    <span>temperature {nText(c.temperature)}</span>
                    <span>max_tokens {nText(c.max_tokens)}</span>
                    <span>top_p {nText(c.top_p)}</span>
                    <span>hash {shortHash(c.configuration_hash)}</span>
                  </div>
                  {c.system_prompt && <p className="mt-1 line-clamp-2 text-muted-foreground">System: {c.system_prompt}</p>}
                </div>
              ))}
              {(data.configurations || []).length === 0 && <p className="text-xs text-muted-foreground">No configurations recorded.</p>}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
};

const Field: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="flex flex-col">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="break-words text-sm">{value}</span>
  </div>
);

const HashRow: React.FC<{ label: string; value?: string | null }> = ({ label, value }) => (
  <div className="flex items-center gap-1.5">
    <span className="w-40 shrink-0 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <span className="break-all font-mono">{value || "n/a"}</span>
    {value && <CopyButton value={value} />}
  </div>
);
