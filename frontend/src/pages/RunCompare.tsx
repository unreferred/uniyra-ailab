import React, { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { GitCompare } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useRunCompare } from "@/lib/hooks";
import { ObsPoint, Observability, RunCompare as RunCompareType } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState, ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { fmtCost, fmtDate, fmtLatency, fmtTokens } from "@/lib/format";

const display = (v: any): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

const PARAM_KEYS = ["temperature", "max_tokens", "top_p"];

const RunPicker: React.FC<{
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: ObsPoint[];
  testId: string;
}> = ({ label, value, onChange, options, testId }) => (
  <div className="flex flex-col gap-1">
    <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</span>
    <Select value={value || ""} onValueChange={onChange}>
      <SelectTrigger data-testid={testId}><SelectValue placeholder="Select a run…" /></SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.run_id as string} value={o.run_id as string}>
            {o.model_name} · {String(o.run_id).slice(0, 8)} · {fmtDate(o.created_at)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  </div>
);

const CompareSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <Card className="p-4">
    <h3 className="mb-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{title}</h3>
    {children}
  </Card>
);

const TwoCol: React.FC<{ a: React.ReactNode; b: React.ReactNode }> = ({ a, b }) => (
  <div className="grid gap-3 md:grid-cols-2">
    <div className="space-y-1.5 rounded-md border border-border/70 p-3">
      <Badge variant="outline" className="text-[10px]">A</Badge>
      {a}
    </div>
    <div className="space-y-1.5 rounded-md border border-border/70 p-3">
      <Badge variant="outline" className="text-[10px]">B</Badge>
      {b}
    </div>
  </div>
);

const Pair: React.FC<{ label: string; a: React.ReactNode; b: React.ReactNode; highlight?: boolean }> = ({ label, a, b, highlight }) => (
  <div className={highlight ? "rounded bg-amber-500/10 px-2 py-1" : ""} data-testid={highlight ? "param-diff" : undefined}>
    <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</div>
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="font-mono">{a}</span>
      <span className="text-muted-foreground">vs</span>
      <span className="font-mono">{b}</span>
    </div>
  </div>
);

const ContextDiffView: React.FC<{ diff: any }> = ({ diff }) => {
  const componentDiffs: Record<string, any> = diff.component_diffs || {};
  const attachments = diff.attachments || {};
  const tools = diff.tools || {};
  const params = diff.parameters || {};
  const hasParams = params && Object.keys(params).length > 0;

  return (
    <div className="space-y-4" data-testid="context-diff">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline" className="text-[10px]">hash A {display(diff.context_hash_a).slice(0, 12)}</Badge>
        <Badge variant="outline" className="text-[10px]">hash B {display(diff.context_hash_b).slice(0, 12)}</Badge>
        <Badge variant="secondary" className="text-[10px]">
          tok {fmtTokens(diff.estimated_tokens_a)} → {fmtTokens(diff.estimated_tokens_b)}
          {diff.token_delta != null ? ` (${diff.token_delta > 0 ? "+" : ""}${diff.token_delta})` : ""}
        </Badge>
      </div>

      <div>
        <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Components</h4>
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-xs">
            <thead className="bg-muted/40 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              <tr><th className="px-3 py-2">Component type</th><th className="px-3 py-2">Status</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {Object.entries(componentDiffs).map(([type, d]) => (
                <tr key={type}>
                  <td className="px-3 py-2 font-medium">{type}</td>
                  <td className="px-3 py-2">
                    <Badge
                      variant={d.status === "changed" ? "destructive" : d.status === "unchanged" ? "outline" : "secondary"}
                      className="text-[9px]"
                    >
                      {d.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {Object.entries(componentDiffs)
        .filter(([, d]) => Array.isArray(d.text_diff) && d.text_diff.length > 0)
        .map(([type, d]) => (
          <div key={type}>
            <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{type} text diff</h4>
            <pre className="max-h-64 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] leading-relaxed scrollbar-thin">
              {(d.text_diff as { op: string; line: string }[]).map((entry, i) => (
                <div
                  key={i}
                  className={
                    entry.op === "+"
                      ? "text-[hsl(var(--brand-emerald))]"
                      : entry.op === "-"
                      ? "text-destructive"
                      : "text-muted-foreground"
                  }
                >
                  {entry.op} {entry.line}
                </div>
              ))}
            </pre>
          </div>
        ))}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-md border border-border/70 p-3 text-xs">
          <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Attachments</h4>
          <div className="space-y-1">
            {(attachments.added || []).map((n: string) => <div key={`a-${n}`} className="text-[hsl(var(--brand-emerald))]">+ {n}</div>)}
            {(attachments.removed || []).map((n: string) => <div key={`r-${n}`} className="text-destructive">- {n}</div>)}
            {(attachments.added || []).length === 0 && (attachments.removed || []).length === 0 && (
              <div className="text-muted-foreground">No attachment changes.</div>
            )}
          </div>
        </div>
        <div className="rounded-md border border-border/70 p-3 text-xs">
          <h4 className="mb-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Tools</h4>
          <div className="space-y-1">
            {(tools.added || []).map((n: string) => <div key={`a-${n}`} className="text-[hsl(var(--brand-emerald))]">+ {n}</div>)}
            {(tools.removed || []).map((n: string) => <div key={`r-${n}`} className="text-destructive">- {n}</div>)}
            {(tools.added || []).length === 0 && (tools.removed || []).length === 0 && (
              <div className="text-muted-foreground">No tool changes.</div>
            )}
          </div>
        </div>
      </div>

      {hasParams && (
        <div>
          <h4 className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Parameter changes</h4>
          <div className="space-y-1.5">
            {Object.entries<any>(params).map(([key, v]) => {
              const changed = JSON.stringify((v as any)?.a) !== JSON.stringify((v as any)?.b);
              return (
                <div key={key} className={`flex items-center justify-between gap-2 rounded px-2 py-1 text-xs ${changed ? "bg-amber-500/10" : ""}`}>
                  <span className="font-mono text-muted-foreground">{key}</span>
                  <span className="font-mono">{display((v as any)?.a)} → {display((v as any)?.b)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

const RunCompareView: React.FC<{ data: RunCompareType }> = ({ data }) => {
  const blind = (data as any).blind as { a?: boolean; b?: boolean } | undefined;
  const { identity, parameters, context, tools, result, failures } = data;

  const paramKeys = useMemo(() => {
    const keys = new Set<string>(PARAM_KEYS);
    Object.keys(parameters?.a || {}).forEach((k) => keys.add(k));
    Object.keys(parameters?.b || {}).forEach((k) => keys.add(k));
    return Array.from(keys);
  }, [parameters]);

  const sideCtx = (c: any) => (
    <div className="space-y-1 text-xs">
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">hash</span><span className="font-mono">{display(c?.context_hash).slice(0, 16)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">est. tokens</span><span className="font-mono">{fmtTokens(c?.estimated_tokens)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">provider input</span><span className="font-mono">{fmtTokens(c?.provider_input_tokens)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">attachments</span><span className="font-mono">{(c?.attachment_names || []).length}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">tools</span><span className="font-mono">{(c?.tool_names || []).length}</span></div>
    </div>
  );

  const sideTools = (t: any) => (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap gap-1">
        {(t?.enabled || []).length === 0 ? <span className="text-muted-foreground">No tools enabled</span> :
          (t?.enabled || []).map((n: string) => <Badge key={n} variant="outline" className="text-[9px]">{n}</Badge>)}
      </div>
      <ul className="space-y-1">
        {(t?.calls || []).map((c: any, i: number) => (
          <li key={i} className="flex items-center justify-between gap-2 rounded border border-border/70 px-2 py-1">
            <span className="min-w-0 flex-1 truncate font-medium">{c.name || "tool"}</span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{fmtLatency(c.duration_ms)}</span>
            <Badge variant={c.status === "error" || c.status === "failed" ? "destructive" : "secondary"} className="shrink-0 text-[9px]">{c.status || "unknown"}</Badge>
          </li>
        ))}
        {(t?.calls || []).length === 0 && <li className="text-muted-foreground">No tool calls</li>}
      </ul>
    </div>
  );

  const sideResult = (r: any) => (
    <div className="space-y-1 text-xs">
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">status</span><span className="font-mono">{display(r?.status)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">latency</span><span className="font-mono">{fmtLatency(r?.latency_ms)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">ttft</span><span className="font-mono">{fmtLatency(r?.ttft_ms)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">in / out tok</span><span className="font-mono">{fmtTokens(r?.input_tokens)} / {fmtTokens(r?.output_tokens)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">cost</span><span className="font-mono">{fmtCost(r?.estimated_cost, r?.cost_available)}</span></div>
      <div className="flex justify-between gap-2"><span className="text-muted-foreground">finish reason</span><span className="font-mono">{display(r?.finish_reason)}</span></div>
    </div>
  );

  const sideFailures = (list: any[]) => (
    <div className="space-y-1 text-xs">
      {(list || []).length === 0 ? <p className="text-muted-foreground">No failures</p> :
        (list || []).map((f, i) => (
          <div key={f.id || i} className="flex items-start gap-2 rounded border border-border/70 px-2 py-1">
            <Badge variant={f.failure_class === "execution" ? "destructive" : "secondary"} className="mt-0.5 shrink-0 text-[9px] uppercase">{f.failure_class || "quality"}</Badge>
            <div className="min-w-0">
              <div className="truncate font-medium">{f.title}</div>
              <div className="text-[10px] text-muted-foreground">{f.category} · {f.severity} · {f.status}</div>
            </div>
          </div>
        ))}
    </div>
  );

  return (
    <div className="space-y-4" data-testid="run-compare-page">
      {(blind?.a || blind?.b) && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600" data-testid="run-compare-blind">
          Identity hidden until reveal for {blind?.a && blind?.b ? "both runs" : blind?.a ? "run A" : "run B"}.
        </div>
      )}

      <CompareSection title="Identity">
        <TwoCol
          a={<div className="text-xs"><div className="font-medium">{display(identity?.model_a)}</div><div className="text-muted-foreground">{display(identity?.provider_a)}</div></div>}
          b={<div className="text-xs"><div className="font-medium">{display(identity?.model_b)}</div><div className="text-muted-foreground">{display(identity?.provider_b)}</div></div>}
        />
      </CompareSection>

      <CompareSection title="Parameters">
        <div className="space-y-2">
          {paramKeys.map((key) => (
            <Pair
              key={key}
              label={key}
              a={display(parameters?.a?.[key])}
              b={display(parameters?.b?.[key])}
              highlight={Boolean(parameters?.diff && key in parameters.diff)}
            />
          ))}
        </div>
      </CompareSection>

      <CompareSection title="Context">
        <TwoCol a={sideCtx(context?.a)} b={sideCtx(context?.b)} />
      </CompareSection>

      <CompareSection title="Tools">
        <TwoCol a={sideTools(tools?.a)} b={sideTools(tools?.b)} />
      </CompareSection>

      <CompareSection title="Result">
        <TwoCol a={sideResult(result?.a)} b={sideResult(result?.b)} />
      </CompareSection>

      <CompareSection title="Failures">
        <TwoCol a={sideFailures(failures?.a)} b={sideFailures(failures?.b)} />
      </CompareSection>

      <CompareSection title="Context diff">
        {data.context_diff ? (
          <ContextDiffView diff={data.context_diff} />
        ) : (
          <p className="text-xs text-muted-foreground" data-testid="context-diff-unavailable">Context diff unavailable (legacy run)</p>
        )}
      </CompareSection>
    </div>
  );
};

export default function RunCompare() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runA = searchParams.get("run_a") || "";
  const runB = searchParams.get("run_b") || "";

  const { data: obs, isLoading: obsLoading, isError: obsError, error: obsErr } = useQuery<Observability>({
    queryKey: ["observability", "run-picker"],
    queryFn: async () => (await api.get("/observability")).data,
  });

  const options = useMemo<ObsPoint[]>(() => {
    const seen = new Set<string>();
    const list: ObsPoint[] = [];
    [...(obs?.series || [])]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .forEach((p) => {
        if (!p.run_id || seen.has(p.run_id)) return;
        seen.add(p.run_id);
        list.push(p);
      });
    return list.slice(0, 100);
  }, [obs]);

  const setRun = (key: "run_a" | "run_b", value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const { data, isLoading, isError, error } = useRunCompare(runA, runB);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader
        title="Run Compare"
        caption="Side-by-side comparison of two runs, including a structured context diff."
        action={<Badge variant="outline" className="font-mono text-[10px]"><GitCompare className="mr-1 h-3 w-3" /> /runs/compare</Badge>}
      />

      <Card className="grid gap-3 p-4 md:grid-cols-2" data-testid="run-pickers">
        <RunPicker label="Run A" value={runA} onChange={(v) => setRun("run_a", v)} options={options} testId="run-compare-a" />
        <RunPicker label="Run B" value={runB} onChange={(v) => setRun("run_b", v)} options={options} testId="run-compare-b" />
      </Card>

      {obsLoading && <LoadingSpinner label="Loading runs…" />}
      {obsError && <ErrorNote message={`Could not load run list: ${apiError(obsErr)}`} />}

      {!runA || !runB ? (
        <EmptyState
          icon={<GitCompare className="h-6 w-6" />}
          title="Pick two runs"
          description="Select a run for A and B to compare identity, parameters, context, tools, results, failures and the context diff."
          testId="run-compare-empty"
        />
      ) : isLoading ? (
        <LoadingSpinner label="Comparing runs…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : data ? (
        <RunCompareView data={data} />
      ) : null}
    </div>
  );
}
