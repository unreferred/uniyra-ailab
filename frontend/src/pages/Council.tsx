import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Users, Play, Loader2, Plus, Trash2, Gavel, ChevronRight, Save } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { useModels } from "@/lib/hooks";
import { Council as CouncilType, CouncilRound, CouncilParticipant } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { SingleModelSelect } from "@/components/common/ModelPicker";
import { EmptyState, SectionHeader, FamilyDot, TelemetryRow, ErrorNote } from "@/components/common/Common";
import { MarkdownView } from "@/components/common/Markdown";
import { SaveArtifactDialog } from "@/components/common/SaveArtifactDialog";

type Row = {
  role: string; display_name: string; perspective: string; model_id: string;
  temperature: number; max_tokens: number; order: number; enabled: boolean;
};

const PRESETS: Record<string, { role: string; perspective: string }[]> = {
  "Executive": [
    { role: "CEO", perspective: "Weigh vision, growth and opportunity cost." },
    { role: "CFO", perspective: "Weigh cost, runway and financial risk." },
    { role: "COO", perspective: "Weigh execution, operations and delivery." },
  ],
  "Technical": [
    { role: "Chief Architect", perspective: "Weigh scalability, maintainability and technical debt." },
    { role: "Security Auditor", perspective: "Weigh threat surface, data safety and compliance." },
    { role: "SRE", perspective: "Weigh reliability, operability and on-call burden." },
  ],
  "Product": [
    { role: "Product Lead", perspective: "Weigh user value and roadmap fit." },
    { role: "UX Researcher", perspective: "Weigh usability and user evidence." },
    { role: "Data Analyst", perspective: "Weigh measurable impact and metrics." },
  ],
  "Red Team": [
    { role: "Adversary", perspective: "Attack the proposal; find how it fails." },
    { role: "Skeptic", perspective: "Challenge assumptions and hidden risks." },
    { role: "Risk Officer", perspective: "Surface worst-case and mitigations." },
  ],
};

const blankRow = (modelId: string, order: number): Row => ({
  role: "", display_name: "", perspective: "", model_id: modelId,
  temperature: 0.7, max_tokens: 700, order, enabled: true,
});

const RoundView: React.FC<{ round: CouncilRound }> = ({ round }) => (
  <div className="space-y-3" data-testid={`council-round-${round.round}`}>
    <div className="flex items-center gap-2">
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">{round.round}</span>
      <h4 className="font-display font-semibold">{round.name}</h4>
    </div>
    <div className="grid gap-3 md:grid-cols-2">
      {round.messages.map((m) => (
        <Card key={m.participant_id + round.round} className="p-3.5" data-testid={`council-msg-${round.round}-${m.participant_id}`}>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="font-medium">{m.display_name && m.display_name !== m.role ? `${m.display_name} (${m.role})` : m.role}</span>
            <span className="text-right text-[11px] text-muted-foreground">
              {m.model_name || m.model_identifier || ""}
              {m.provider_name ? ` · ${m.provider_name}` : ""}
            </span>
          </div>
          {m.status === "error" ? <ErrorNote message={m.error || "Participant failed"} /> : <MarkdownView content={m.content} />}
          {m.status === "success" && <div className="mt-2 border-t border-border pt-1.5"><TelemetryRow t={m.telemetry} /></div>}
          {m.run_id && (
            <div className="mt-2 flex items-center gap-3 border-t border-border pt-1.5">
              <Link to={`/trace?run=${m.run_id}`} className="text-[11px] text-primary hover:underline" data-testid={`council-trace-${m.run_id}`}>Trace</Link>
              <Link to={`/context-lab?run=${m.run_id}`} className="text-[11px] text-primary hover:underline" data-testid={`council-context-${m.run_id}`}>Context</Link>
            </div>
          )}
        </Card>
      ))}
    </div>
  </div>
);

const SynthList: React.FC<{ title: string; items?: string[] }> = ({ title, items }) =>
  items && items.length ? (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{title}</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm">{items.map((i, idx) => <li key={idx}>{i}</li>)}</ul>
    </div>
  ) : null;

export default function Council() {
  const { activeId, activeProject } = useProjects();
  const { data: models = [] } = useModels();
  const qc = useQueryClient();
  const enabled = models.filter((m) => m.enabled);
  const firstModel = enabled[0]?.id || "";

  const [question, setQuestion] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [moderator, setModerator] = useState("");
  const [result, setResult] = useState<CouncilType | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);

  React.useEffect(() => { if (!moderator && firstModel) setModerator(firstModel); }, [firstModel]); // eslint-disable-line

  const applyPreset = (name: string) => {
    if (name === "Custom") { setRows([blankRow(firstModel, 0)]); return; }
    setRows(PRESETS[name].map((p, i) => ({ role: p.role, display_name: p.role, perspective: p.perspective, model_id: firstModel, temperature: 0.7, max_tokens: 700, order: i, enabled: true })));
  };
  const updateRow = (i: number, patch: Partial<Row>) => setRows((r) => r.map((x, idx) => idx === i ? { ...x, ...patch } : x));
  const addRow = () => setRows((r) => [...r, blankRow(firstModel, r.length)]);
  const removeRow = (i: number) => setRows((r) => r.filter((_, idx) => idx !== i));

  const { data: history = [] } = useQuery<CouncilType[]>({
    queryKey: ["councils", activeId],
    queryFn: async () => (await api.get(`/councils?project_id=${activeId}`)).data,
    enabled: !!activeId,
  });

  const run = useMutation({
    mutationFn: async () => {
      const ordered = [...rows]
        .filter((r) => r.enabled && r.role.trim() && r.model_id)
        .sort((a, b) => (a.order - b.order) || a.role.localeCompare(b.role));
      const participants: CouncilParticipant[] = ordered.map((r) => ({
        role: r.role.trim(),
        display_name: r.display_name.trim() || r.role.trim(),
        perspective: r.perspective,
        model_id: r.model_id,
        temperature: r.temperature,
        max_tokens: Math.min(4096, Math.max(128, Math.round(r.max_tokens))),
        order: r.order,
        enabled: true,
      }));
      return (await api.post("/councils/run", {
        project_id: activeId, question,
        participants,
        moderator_model_id: moderator,
      })).data;
    },
    onSuccess: (data) => {
      setResult(data);
      qc.invalidateQueries({ queryKey: ["councils", activeId] });
      toast.success("Council deliberation complete");
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const openCouncil = async (id: string) => {
    const { data } = await api.get(`/councils/${id}`);
    setResult(data);
  };

  if (!activeId) return <div className="p-6"><EmptyState icon={<Users className="h-6 w-6" />} title="No project selected" description="Select a project to convene a council." /></div>;

  const enabledRows = rows.filter((r) => r.enabled && r.role.trim() && r.model_id);
  const syn = result?.synthesis;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="AI Council" caption={`Convene multiple AI roles for a structured 4-round deliberation in "${activeProject?.name}".`} />

      <Card className="space-y-4 p-4" data-testid="council-config">
        <div>
          <label className="text-xs font-medium text-muted-foreground">Presets</label>
          <div className="mt-2 flex flex-wrap gap-2">
            {[...Object.keys(PRESETS), "Custom"].map((p) => (
              <Button key={p} size="sm" variant="outline" data-testid={`council-preset-${p}`} onClick={() => applyPreset(p)}>{p}</Button>
            ))}
          </div>
        </div>

        {rows.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground">Participants ({enabledRows.length} enabled)</label>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <Switch data-testid="council-advanced-toggle" checked={showAdvanced} onCheckedChange={setShowAdvanced} /> Advanced params
              </label>
            </div>
            {rows.map((r, i) => (
              <div key={i} className="rounded-md border border-border/60 p-2" data-testid={`council-row-${i}`}>
                <div className="grid grid-cols-12 items-center gap-2">
                  <Input className="col-span-2" placeholder="Role" value={r.role} onChange={(e) => updateRow(i, { role: e.target.value, display_name: r.display_name === r.role || !r.display_name ? e.target.value : r.display_name })} data-testid={`council-role-${i}`} />
                  <Input className="col-span-2" placeholder="Display name" value={r.display_name} onChange={(e) => updateRow(i, { display_name: e.target.value })} data-testid={`council-displayname-${i}`} title="Display name shown in results (defaults to role)" />
                  <Input className="col-span-4" placeholder="Perspective / instruction" value={r.perspective} onChange={(e) => updateRow(i, { perspective: e.target.value })} data-testid={`council-perspective-${i}`} />
                  <div className="col-span-3"><SingleModelSelect models={enabled} value={r.model_id} onChange={(v) => updateRow(i, { model_id: v })} testId={`council-model-${i}`} /></div>
                  <div className="col-span-1 flex items-center justify-end gap-1">
                    <Switch data-testid={`council-enabled-${i}`} checked={r.enabled} onCheckedChange={(v) => updateRow(i, { enabled: !!v })} title="Enabled" />
                    <Button size="sm" variant="ghost" onClick={() => removeRow(i)} data-testid={`council-remove-${i}`}><Trash2 className="h-4 w-4" /></Button>
                  </div>
                </div>
                {showAdvanced && (
                  <div className="mt-2 grid grid-cols-12 items-center gap-2 text-xs">
                    <div className="col-span-4 flex items-center gap-2">
                      <span className="w-20 shrink-0 text-muted-foreground">Temp {r.temperature.toFixed(2)}</span>
                      <input type="range" min={0} max={2} step={0.05} value={r.temperature} data-testid={`council-temp-${i}`}
                        onChange={(e) => updateRow(i, { temperature: Number(e.target.value) })} className="w-full" />
                      <Input type="number" min={0} max={2} step={0.05} value={r.temperature} data-testid={`council-temp-num-${i}`}
                        onChange={(e) => updateRow(i, { temperature: Math.min(2, Math.max(0, Number(e.target.value))) })} className="h-7 w-20" />
                    </div>
                    <div className="col-span-4 flex items-center gap-2">
                      <span className="w-20 shrink-0 text-muted-foreground">Max tok</span>
                      <Input type="number" min={128} max={4096} step={64} value={r.max_tokens} data-testid={`council-maxtokens-${i}`}
                        onChange={(e) => updateRow(i, { max_tokens: Number(e.target.value) })} className="h-7 w-28" />
                    </div>
                    <div className="col-span-4 flex items-center gap-2">
                      <span className="w-20 shrink-0 text-muted-foreground">Order</span>
                      <Input type="number" value={r.order} data-testid={`council-order-${i}`}
                        onChange={(e) => updateRow(i, { order: Number(e.target.value) })} className="h-7 w-24" />
                      <span className="text-muted-foreground">send order: sorted by order, then role</span>
                    </div>
                  </div>
                )}
              </div>
            ))}
            <Button size="sm" variant="ghost" onClick={addRow} data-testid="council-add-row"><Plus className="mr-1.5 h-4 w-4" /> Add role</Button>
          </div>
        )}

        <div>
          <label className="text-xs font-medium text-muted-foreground">Question</label>
          <Textarea data-testid="council-question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} className="mt-1" placeholder="What decision or problem should the council deliberate?" />
        </div>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="w-64">
            <label className="text-xs font-medium text-muted-foreground">Moderator model</label>
            <SingleModelSelect models={enabled} value={moderator} onChange={setModerator} testId="council-moderator-select" />
          </div>
          <Button data-testid="council-run-button" disabled={enabledRows.length < 2 || !question.trim() || !moderator || run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Deliberating…</> : <><Play className="mr-1.5 h-4 w-4" /> Convene council</>}
          </Button>
        </div>
        {enabledRows.length < 2 && rows.length > 0 && <p className="text-right text-xs text-muted-foreground">Enable at least 2 participants with a role and model.</p>}
      </Card>

      {run.isPending && <Card className="p-6"><div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Running 4 rounds across {enabledRows.length} participants…</div></Card>}

      {result && (
        <div className="space-y-6" data-testid="council-results">
          <div className="flex items-start justify-between gap-3">
            <h3 className="font-display text-lg font-semibold">{result.title}</h3>
            <Button size="sm" variant="outline" data-testid="council-save-artifact" onClick={() => setSaveOpen(true)}><Save className="mr-1.5 h-4 w-4" /> Save synthesis</Button>
          </div>
          {result.rounds.filter((r) => r.round <= 3).map((r) => <RoundView key={r.round} round={r} />)}

          <Card className="p-4" data-testid="council-synthesis">
            <div className="mb-3 flex items-center gap-2"><Gavel className="h-4 w-4 text-brand" /><span className="font-display font-semibold">Moderator Synthesis</span>
              <span className="text-xs text-muted-foreground">· {result.moderator_model_name}</span>
              {syn?.confidence != null && <Badge variant="outline">confidence {(syn.confidence * 100).toFixed(0)}%</Badge>}
            </div>
            {result.synthesis_error ? <ErrorNote message={result.synthesis_error} /> : (
              <div className="space-y-3">
                {syn?.recommended_decision && (
                  <div className="rounded-md border border-border bg-muted/40 p-3">
                    <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Recommended decision</div>
                    <p className="mt-1 text-sm font-medium">{syn.recommended_decision}</p>
                  </div>
                )}
                <div className="grid gap-4 md:grid-cols-2">
                  <SynthList title="Consensus" items={syn?.consensus} />
                  <SynthList title="Disagreements" items={syn?.disagreements} />
                  <SynthList title="Key risks" items={syn?.key_risks} />
                  <SynthList title="Critical unknowns" items={syn?.critical_unknowns} />
                  <SynthList title="Strongest arguments" items={syn?.strongest_arguments} />
                  <SynthList title="Conditions" items={syn?.conditions} />
                  <SynthList title="Suggested next actions" items={syn?.suggested_next_actions} />
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      {history.length > 0 && !result && (
        <Card className="p-4" data-testid="council-history">
          <h3 className="mb-2 font-display font-semibold">Past councils</h3>
          <div className="space-y-1">
            {history.map((c) => (
              <button key={c.id} onClick={() => openCouncil(c.id)} className="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-accent/50" data-testid={`council-history-${c.id}`}>
                <span className="truncate">{c.title}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">{c.participants?.length} roles <ChevronRight className="h-4 w-4" /></span>
              </button>
            ))}
          </div>
        </Card>
      )}

      <SaveArtifactDialog
        open={saveOpen} onOpenChange={setSaveOpen}
        content={result ? `Question: ${result.question}\n\nRecommended: ${syn?.recommended_decision || ""}\n\nConsensus:\n${(syn?.consensus || []).map((s) => `- ${s}`).join("\n")}\n\nNext actions:\n${(syn?.suggested_next_actions || []).map((s) => `- ${s}`).join("\n")}` : ""}
        defaultTitle={result ? `Council: ${result.title}` : "Council synthesis"}
        source="council" sourceRef={result?.id}
      />
    </div>
  );
}
