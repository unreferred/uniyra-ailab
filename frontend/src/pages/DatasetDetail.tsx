import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Plus, Trash2, Pencil, Save, FlaskConical, Link2, Snowflake, GitBranch, Lock } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useProjects } from "@/context/ProjectContext";
import { Dataset, DatasetVersion, DatasetVersionCase, Experiment } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorNote, LoadingSpinner, SectionHeader } from "@/components/common/Common";
import { cn } from "@/lib/utils";
import { fmtDate, relTime } from "@/lib/format";

const PAGE_SIZE = 50;

type VersionDetail = DatasetVersion & { cases: DatasetVersionCase[]; case_count: number; limit: number; offset: number };

// Tri-state policy values: "unset" is omitted from the payload entirely so a
// PATCH never clobbers an existing field with an implicit false.
type Tri = "unset" | "true" | "false";
const triToBool = (v: Tri): boolean | undefined => (v === "unset" ? undefined : v === "true");
const asTri = (v: unknown): Tri => (v === true ? "true" : v === false ? "false" : "unset");
const splitList = (v: string): string[] => v.split(",").map((t) => t.trim()).filter(Boolean);
const listToStr = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : "");
const numToStr = (v: unknown): string => (typeof v === "number" ? String(v) : "");

const TriField: React.FC<{ label: string; value: Tri; onChange: (v: Tri) => void; testId: string }> = ({ label, value, onChange, testId }) => (
  <div className="flex items-center gap-2">
    <Checkbox
      checked={value === "unset" ? "indeterminate" : value === "true"}
      onCheckedChange={() => onChange(value === "unset" ? "true" : value === "true" ? "false" : "unset")}
      data-testid={testId}
    />
    <span className="text-xs text-muted-foreground">{label}: <span className="font-mono text-foreground">{value}</span></span>
  </div>
);

export default function DatasetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { activeId } = useProjects();
  const qc = useQueryClient();

  const [selectedVid, setSelectedVid] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  const [caseOpen, setCaseOpen] = useState(false);
  const [editingCase, setEditingCase] = useState<DatasetVersionCase | null>(null);
  const [inputText, setInputText] = useState("");
  const [expected, setExpected] = useState("");
  const [metadata, setMetadata] = useState("{}");
  const [caseTags, setCaseTags] = useState("");
  const [ordering, setOrdering] = useState(0);
  const [metadataError, setMetadataError] = useState<string | null>(null);

  // OmniLab V5 research-quality case fields (all optional).
  const [referenceAnswer, setReferenceAnswer] = useState("");
  const [assertionsText, setAssertionsText] = useState("");
  const [assertionsError, setAssertionsError] = useState<string | null>(null);
  const [allowTools, setAllowTools] = useState<Tri>("unset");
  const [expectedToolCallCount, setExpectedToolCallCount] = useState("");
  const [maxToolCalls, setMaxToolCalls] = useState("");
  const [requiredTools, setRequiredTools] = useState("");
  const [forbiddenTools, setForbiddenTools] = useState("");
  const [knowledgeExpected, setKnowledgeExpected] = useState<Tri>("unset");
  const [expectRetrieval, setExpectRetrieval] = useState<Tri>("unset");
  const [expectInjection, setExpectInjection] = useState<Tri>("unset");
  const [requireValidMarkers, setRequireValidMarkers] = useState<Tri>("unset");
  const [requireNoKnowledge, setRequireNoKnowledge] = useState<Tri>("unset");
  const [requireCitation, setRequireCitation] = useState<Tri>("unset");
  const [forbidFabricatedMarkers, setForbidFabricatedMarkers] = useState<Tri>("unset");

  const [newVersionOpen, setNewVersionOpen] = useState(false);
  const [sourceVersionId, setSourceVersionId] = useState("current");
  const [newVersionDesc, setNewVersionDesc] = useState("");

  const [freezeOpen, setFreezeOpen] = useState(false);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [attachExp, setAttachExp] = useState("");

  const { data: dataset, isLoading } = useQuery<Dataset>({
    queryKey: ["dataset-detail", id],
    queryFn: async () => (await api.get(`/datasets/${id}`)).data,
    enabled: !!id,
  });

  const { data: versions = [], isLoading: versionsLoading } = useQuery<DatasetVersion[]>({
    queryKey: ["dataset-versions", id],
    queryFn: async () => (await api.get(`/datasets/${id}/versions`)).data,
    enabled: !!id,
  });

  const { data: versionDoc, isLoading: versionLoading } = useQuery<VersionDetail>({
    queryKey: ["dataset-version", id, selectedVid, offset],
    queryFn: async () => (await api.get(`/datasets/${id}/versions/${selectedVid}?limit=${PAGE_SIZE}&offset=${offset}`)).data,
    enabled: !!id && !!selectedVid,
  });

  const { data: experiments = [] } = useQuery<Experiment[]>({
    queryKey: ["experiments", activeId], queryFn: async () => (await api.get(`/experiments?project_id=${activeId}`)).data, enabled: !!activeId,
  });

  useEffect(() => {
    if (dataset) {
      setName(dataset.name); setDescription(dataset.description || "");
      setTagsInput((dataset.tags || []).join(", "));
    }
  }, [dataset?.id]); // eslint-disable-line

  useEffect(() => {
    if (!versions.length) return;
    if (selectedVid && versions.some((v) => v.id === selectedVid)) return;
    if (!selectedVid) {
      const draft = versions.find((v) => v.status === "draft");
      setSelectedVid((draft ?? versions[0]).id);
    }
  }, [versions, selectedVid]);

  const selectedVersion = versions.find((v) => v.id === selectedVid) || null;
  const version = versionDoc ?? selectedVersion;
  const isDraft = selectedVersion?.status === "draft";
  const cases = versionDoc?.cases ?? [];
  const total = versionDoc?.case_count ?? selectedVersion?.case_count ?? 0;

  const invalidateVersion = () => {
    qc.invalidateQueries({ queryKey: ["dataset-version", id] });
    qc.invalidateQueries({ queryKey: ["dataset-versions", id] });
    qc.invalidateQueries({ queryKey: ["datasets"] });
  };

  const saveMeta = useMutation({
    mutationFn: async () => (await api.patch(`/datasets/${id}`, {
      name, description, tags: tagsInput.split(",").map((t) => t.trim()).filter(Boolean),
    })).data,
    onSuccess: () => { toast.success("Dataset updated"); qc.invalidateQueries({ queryKey: ["dataset-detail", id] }); qc.invalidateQueries({ queryKey: ["datasets"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  const createVersion = useMutation({
    mutationFn: async () => {
      const body: { description: string; source_version_id?: string } = { description: newVersionDesc };
      if (sourceVersionId !== "current") body.source_version_id = sourceVersionId;
      return (await api.post(`/datasets/${id}/versions`, body)).data as DatasetVersion;
    },
    onSuccess: (v) => {
      toast.success(`Version v${v.version} created`);
      setNewVersionOpen(false); setNewVersionDesc(""); setSourceVersionId("current"); setOffset(0);
      qc.invalidateQueries({ queryKey: ["dataset-versions", id] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      setSelectedVid(v.id);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const freeze = useMutation({
    mutationFn: async () => (await api.post(`/datasets/${id}/versions/${selectedVid}/freeze`)).data,
    onSuccess: () => { toast.success("Version frozen — now immutable"); setFreezeOpen(false); invalidateVersion(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const resetV5Fields = () => {
    setReferenceAnswer(""); setAssertionsText(""); setAssertionsError(null);
    setAllowTools("unset"); setExpectedToolCallCount(""); setMaxToolCalls("");
    setRequiredTools(""); setForbiddenTools("");
    setKnowledgeExpected("unset"); setExpectRetrieval("unset"); setExpectInjection("unset");
    setRequireValidMarkers("unset"); setRequireNoKnowledge("unset");
    setRequireCitation("unset"); setForbidFabricatedMarkers("unset");
  };
  const openNewCase = () => {
    setEditingCase(null); setInputText(""); setExpected(""); setMetadata("{}");
    setMetadataError(null); setCaseTags(""); setOrdering(total); resetV5Fields(); setCaseOpen(true);
  };
  const openEditCase = (c: DatasetVersionCase) => {
    setEditingCase(c); setInputText(c.input_text); setExpected(c.expected_output || "");
    setMetadata(JSON.stringify(c.metadata || {}, null, 2)); setMetadataError(null);
    setCaseTags((c.tags || []).join(", ")); setOrdering(c.ordering);
    const tp = (c.tool_policy || {}) as Record<string, any>;
    const kp = (c.knowledge_policy || {}) as Record<string, any>;
    const gx = (c.grounding_expectations || {}) as Record<string, any>;
    setReferenceAnswer(c.reference_answer ?? "");
    setAssertionsText(Array.isArray(c.assertions) && c.assertions.length ? JSON.stringify(c.assertions, null, 2) : "");
    setAssertionsError(null);
    setAllowTools(asTri(tp.allow_tools));
    setExpectedToolCallCount(numToStr(tp.expected_tool_call_count));
    setMaxToolCalls(numToStr(tp.max_tool_calls));
    setRequiredTools(listToStr(tp.required_tools));
    setForbiddenTools(listToStr(tp.forbidden_tools));
    setKnowledgeExpected(asTri(kp.knowledge_expected));
    setExpectRetrieval(asTri(kp.expect_retrieval));
    setExpectInjection(asTri(kp.expect_injection));
    setRequireValidMarkers(asTri(kp.require_valid_markers));
    setRequireNoKnowledge(asTri(kp.require_no_knowledge));
    setRequireCitation(asTri(gx.require_citation));
    setForbidFabricatedMarkers(asTri(gx.forbid_fabricated_markers));
    setCaseOpen(true);
  };
  const saveCase = useMutation({
    mutationFn: async (parsedAssertions: any[] | undefined) => {
      let meta: Record<string, any> = {};
      try { meta = metadata.trim() ? JSON.parse(metadata) : {}; }
      catch { throw new Error("Metadata must be valid JSON."); }
      const body: Record<string, any> = {
        input_text: inputText, expected_output: expected, metadata: meta,
        tags: caseTags.split(",").map((t) => t.trim()).filter(Boolean), ordering,
      };
      // Only send additive fields that are actually set.
      if (referenceAnswer.trim()) body.reference_answer = referenceAnswer.trim();
      else if (editingCase && (editingCase.reference_answer ?? null) !== null) body.reference_answer = null;
      if (parsedAssertions !== undefined) body.assertions = parsedAssertions;

      const toolPolicy: Record<string, any> = {};
      const allow = triToBool(allowTools);
      if (allow !== undefined) toolPolicy.allow_tools = allow;
      if (expectedToolCallCount.trim() !== "") toolPolicy.expected_tool_call_count = Number(expectedToolCallCount);
      if (maxToolCalls.trim() !== "") toolPolicy.max_tool_calls = Number(maxToolCalls);
      const req = splitList(requiredTools);
      if (req.length) toolPolicy.required_tools = req;
      const forb = splitList(forbiddenTools);
      if (forb.length) toolPolicy.forbidden_tools = forb;
      if (Object.keys(toolPolicy).length) body.tool_policy = toolPolicy;

      const knowledgePolicy: Record<string, any> = {};
      const ke = triToBool(knowledgeExpected);
      if (ke !== undefined) knowledgePolicy.knowledge_expected = ke;
      const er = triToBool(expectRetrieval);
      if (er !== undefined) knowledgePolicy.expect_retrieval = er;
      const ei = triToBool(expectInjection);
      if (ei !== undefined) knowledgePolicy.expect_injection = ei;
      const rvm = triToBool(requireValidMarkers);
      if (rvm !== undefined) knowledgePolicy.require_valid_markers = rvm;
      const rnk = triToBool(requireNoKnowledge);
      if (rnk !== undefined) knowledgePolicy.require_no_knowledge = rnk;
      if (Object.keys(knowledgePolicy).length) body.knowledge_policy = knowledgePolicy;

      const grounding: Record<string, any> = {};
      const rc = triToBool(requireCitation);
      if (rc !== undefined) grounding.require_citation = rc;
      const ffm = triToBool(forbidFabricatedMarkers);
      if (ffm !== undefined) grounding.forbid_fabricated_markers = ffm;
      if (Object.keys(grounding).length) body.grounding_expectations = grounding;

      if (editingCase) return (await api.patch(`/datasets/version-cases/${editingCase.id}`, body)).data;
      return (await api.post(`/datasets/${id}/versions/${selectedVid}/cases`, body)).data;
    },
    onSuccess: () => { toast.success(editingCase ? "Case updated" : "Case added"); setCaseOpen(false); invalidateVersion(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const submitCase = () => {
    if (metadata.trim()) {
      try { JSON.parse(metadata); }
      catch { setMetadataError("Metadata must be valid JSON."); return; }
    }
    setMetadataError(null);
    let parsedAssertions: any[] | undefined;
    if (assertionsText.trim()) {
      let parsed: any;
      try { parsed = JSON.parse(assertionsText); }
      catch {
        setAssertionsError("Assertions must be valid JSON — expected an array of objects with a string \"type\".");
        return;
      }
      if (!Array.isArray(parsed) || !parsed.every((a) => a && typeof a === "object" && !Array.isArray(a) && typeof (a as any).type === "string")) {
        setAssertionsError('Assertions must be a JSON array of objects, each with a string "type".');
        return;
      }
      parsedAssertions = parsed;
    }
    setAssertionsError(null);
    saveCase.mutate(parsedAssertions);
  };
  const delCase = useMutation({
    mutationFn: async (cid: string) => (await api.delete(`/datasets/version-cases/${cid}`)).data,
    onSuccess: () => { toast.success("Case deleted"); invalidateVersion(); },
    onError: (e) => toast.error(apiError(e)),
  });
  const attach = useMutation({
    mutationFn: async () => (await api.post(`/datasets/${id}/attach-experiment`, { experiment_id: attachExp })).data,
    onSuccess: () => { toast.success("Experiment attached to dataset"); setAttachExp(""); },
    onError: (e) => toast.error(apiError(e)),
  });

  // V5: freeze a dataset version containing the three research-quality fixtures.
  const createFixtures = useMutation({
    mutationFn: async () => (await api.post("/datasets/regression-fixtures", { project_id: activeId })).data,
    onSuccess: (data: any) => {
      const v = data?.version;
      const label = v && typeof v === "object" ? `v${v.version ?? "?"}` : v !== undefined && v !== null ? `v${v}` : "version";
      toast.success(`V5 regression fixtures created (${label})`);
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset-versions", id] });
      qc.invalidateQueries({ queryKey: ["dataset-detail", id] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  if (isLoading || !dataset) return <div className="p-6"><LoadingSpinner label="Loading…" /></div>;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 lg:p-6">
      <button onClick={() => navigate("/datasets")} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground" data-testid="back-to-datasets"><ArrowLeft className="h-4 w-4" /> Datasets</button>
      <SectionHeader title={dataset.name} caption={`${versions.length} version(s)`}
        action={
          <div className="flex shrink-0 gap-2">
            {activeId && (
              <Button size="sm" variant="outline" data-testid="create-regression-fixtures" disabled={createFixtures.isPending} onClick={() => createFixtures.mutate()}>
                <FlaskConical className="mr-1.5 h-4 w-4" /> {createFixtures.isPending ? "Creating…" : "V5 fixtures"}
              </Button>
            )}
            <Button size="sm" variant="outline" data-testid="dataset-new-version" onClick={() => setNewVersionOpen(true)}><GitBranch className="mr-1.5 h-4 w-4" /> New version</Button>
            {isDraft && <Button size="sm" data-testid="dataset-freeze" onClick={() => setFreezeOpen(true)}><Snowflake className="mr-1.5 h-4 w-4" /> Freeze version</Button>}
          </div>
        } />

      <Card className="space-y-3 p-4" data-testid="dataset-meta">
        <div className="grid gap-3 md:grid-cols-2">
          <div><label className="text-xs font-medium text-muted-foreground">Name</label><Input data-testid="dataset-name-edit" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" /></div>
          <div><label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label><Input data-testid="dataset-tags-edit" value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} className="mt-1" /></div>
        </div>
        <div><label className="text-xs font-medium text-muted-foreground">Description</label><Textarea data-testid="dataset-desc-edit" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1" /></div>
        <div className="flex justify-end"><Button size="sm" data-testid="save-dataset-meta" onClick={() => saveMeta.mutate()} disabled={saveMeta.isPending}><Save className="mr-1.5 h-4 w-4" /> Save</Button></div>
      </Card>

      <Card className="p-4" data-testid="dataset-attach">
        <div className="mb-2 flex items-center gap-1.5 font-display font-semibold"><Link2 className="h-4 w-4" /> Attach experiment</div>
        <div className="flex gap-2">
          <Select value={attachExp} onValueChange={setAttachExp}>
            <SelectTrigger className="flex-1" data-testid="attach-experiment-select"><SelectValue placeholder="Select an experiment to tag with this dataset…" /></SelectTrigger>
            <SelectContent>{experiments.map((e) => <SelectItem key={e.id} value={e.id}>{e.title}</SelectItem>)}</SelectContent>
          </Select>
          <Button size="sm" data-testid="attach-experiment-submit" disabled={!attachExp || attach.isPending} onClick={() => attach.mutate()}><FlaskConical className="mr-1.5 h-4 w-4" /> Attach</Button>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card className="p-3" data-testid="versions-panel">
          <div className="mb-2 font-display text-sm font-semibold">Versions</div>
          {versionsLoading ? <LoadingSpinner label="Loading versions…" /> : versions.length === 0 ? (
            <p className="text-xs text-muted-foreground">No versions yet.</p>
          ) : (
            <div className="space-y-1">
              {versions.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  data-testid="version-select"
                  onClick={() => { setSelectedVid(v.id); setOffset(0); }}
                  className={cn(
                    "w-full rounded-md border p-2 text-left text-sm transition-colors",
                    v.id === selectedVid ? "border-primary bg-accent" : "border-transparent hover:bg-accent/50"
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">v{v.version}</span>
                    <Badge variant={v.status === "frozen" ? "secondary" : "default"} className="text-[10px]">{v.status}</Badge>
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">{v.case_count} case(s)</div>
                  {v.content_hash && <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground" title={v.content_hash}>{v.content_hash.slice(0, 12)}</div>}
                  <div className="mt-0.5 text-[10px] text-muted-foreground">
                    {v.status === "frozen" && v.frozen_at ? `frozen ${relTime(v.frozen_at)}` : `created ${relTime(v.created_at)}`}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-4" data-testid="version-detail">
          {!selectedVid ? (
            versionsLoading ? <LoadingSpinner label="Loading version…" /> : <p className="text-sm text-muted-foreground">Select a version to view its cases.</p>
          ) : versionLoading && !versionDoc ? (
            <LoadingSpinner label="Loading cases…" />
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-display text-lg font-semibold">Version v{version?.version}</span>
                  {version?.status === "frozen" ? (
                    <>
                      <Badge variant="secondary">frozen</Badge>
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" data-testid="frozen-readonly"><Lock className="h-3 w-3" /> Frozen — read-only</span>
                    </>
                  ) : (
                    <Badge>draft</Badge>
                  )}
                </div>
                {isDraft && <Button size="sm" data-testid="case-add" onClick={openNewCase}><Plus className="mr-1.5 h-4 w-4" /> Add case</Button>}
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
                <div><span className="block font-mono text-[10px] uppercase tracking-wider">cases</span><span className="text-foreground">{total}</span></div>
                <div><span className="block font-mono text-[10px] uppercase tracking-wider">content hash</span><span className="font-mono text-foreground" title={version?.content_hash || undefined}>{version?.content_hash ? version.content_hash.slice(0, 12) : "—"}</span></div>
                <div><span className="block font-mono text-[10px] uppercase tracking-wider">created</span><span className="text-foreground">{fmtDate(version?.created_at)}</span></div>
                <div><span className="block font-mono text-[10px] uppercase tracking-wider">frozen</span><span className="text-foreground">{version?.frozen_at ? fmtDate(version.frozen_at) : "—"}</span></div>
              </div>
              {version?.description && <p className="mt-2 text-sm text-muted-foreground">{version.description}</p>}

              <div className="mt-4 space-y-2">
                {cases.length === 0 && <p className="text-sm text-muted-foreground">No cases in this version yet.{isDraft ? " Add input / expected-output pairs." : ""}</p>}
                {cases.map((c) => (
                  <div key={c.id} data-testid="case-row" className="rounded-md border p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline" className="text-[10px]">#{c.ordering}</Badge>
                          {(c.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
                        </div>
                        <p className="mt-1.5 whitespace-pre-wrap text-sm">{c.input_text}</p>
                        {c.expected_output && <p className="mt-1.5 whitespace-pre-wrap text-xs text-muted-foreground">Expected: {c.expected_output}</p>}
                      </div>
                      {isDraft && (
                        <div className="flex shrink-0 gap-1">
                          <Button size="sm" variant="ghost" data-testid={`edit-case-${c.id}`} onClick={() => openEditCase(c)}><Pencil className="h-4 w-4" /></Button>
                          <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-case-${c.id}`} onClick={() => delCase.mutate(c.id)}><Trash2 className="h-4 w-4" /></Button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground" data-testid="cases-pagination">
                <span>{total === 0 ? "0 cases" : `${offset + 1}–${Math.min(offset + cases.length, total)} of ${total}`}</span>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
                  <Button size="sm" variant="outline" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
                </div>
              </div>
            </>
          )}
        </Card>
      </div>

      <Dialog open={newVersionOpen} onOpenChange={setNewVersionOpen}>
        <DialogContent data-testid="new-version-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New version</DialogTitle>
            <DialogDescription>Creates a new draft version. Its cases are copied from the selected source; the source is left untouched.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Copy cases from</label>
              <Select value={sourceVersionId} onValueChange={setSourceVersionId}>
                <SelectTrigger className="mt-1" data-testid="new-version-source"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="current">Current dataset cases</SelectItem>
                  {versions.map((v) => (
                    <SelectItem key={v.id} value={v.id}>v{v.version} · {v.status} · {v.case_count} case(s)</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div><label className="text-xs font-medium text-muted-foreground">Description (optional)</label><Textarea data-testid="new-version-desc" value={newVersionDesc} onChange={(e) => setNewVersionDesc(e.target.value)} rows={2} className="mt-1" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewVersionOpen(false)}>Cancel</Button>
            <Button data-testid="new-version-submit" disabled={createVersion.isPending} onClick={() => createVersion.mutate()}>{createVersion.isPending ? "Creating…" : "Create version"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={freezeOpen} onOpenChange={setFreezeOpen}>
        <DialogContent data-testid="freeze-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Freeze version v{version?.version}?</DialogTitle>
            <DialogDescription>Freezing is permanent: this version becomes immutable, its cases can no longer be added, edited, or deleted, and its content hash is fixed as a reproducibility snapshot.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setFreezeOpen(false)}>Cancel</Button>
            <Button variant="destructive" data-testid="freeze-confirm" disabled={freeze.isPending} onClick={() => freeze.mutate()}>{freeze.isPending ? "Freezing…" : "Freeze version"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={caseOpen} onOpenChange={setCaseOpen}>
        <DialogContent className="max-w-lg" data-testid="case-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">{editingCase ? "Edit case" : "New case"}</DialogTitle>
            <DialogDescription>{isDraft ? `This case is stored in draft version v${version?.version}.` : "This version is frozen and read-only."}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Input text</label><Textarea data-testid="case-input" value={inputText} onChange={(e) => setInputText(e.target.value)} rows={4} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Expected output</label><Textarea data-testid="case-expected" value={expected} onChange={(e) => setExpected(e.target.value)} rows={3} className="mt-1" /></div>

            <div className="space-y-3 rounded-md border border-border p-3" data-testid="case-v5-fields">
              <p className="text-xs font-semibold text-muted-foreground">V5 research-quality fields (optional)</p>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Reference answer</label>
                <Input data-testid="case-reference-answer" value={referenceAnswer} onChange={(e) => setReferenceAnswer(e.target.value)} className="mt-1" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Assertions (JSON array of objects with a &quot;type&quot;)</label>
                <Textarea data-testid="case-assertions" value={assertionsText} onChange={(e) => { setAssertionsText(e.target.value); setAssertionsError(null); }} rows={3} className="mt-1 font-mono text-xs" placeholder='[{"type": "contains", "value": "..."}]' />
                {assertionsError && <p className="mt-1 text-xs text-destructive" data-testid="case-assertions-error">{assertionsError}</p>}
              </div>
              <div className="space-y-2">
                <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">Tool policy</p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">allow_tools</label>
                    <Select value={allowTools} onValueChange={(v) => setAllowTools(v as Tri)}>
                      <SelectTrigger className="mt-1" data-testid="case-allow-tools"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unset">unset</SelectItem>
                        <SelectItem value="true">true</SelectItem>
                        <SelectItem value="false">false</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">expected_tool_call_count</label>
                    <Input data-testid="case-expected-tool-calls" type="number" value={expectedToolCallCount} onChange={(e) => setExpectedToolCallCount(e.target.value)} className="mt-1" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">max_tool_calls</label>
                    <Input data-testid="case-max-tool-calls" type="number" value={maxToolCalls} onChange={(e) => setMaxToolCalls(e.target.value)} className="mt-1" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">required_tools (comma-separated)</label>
                    <Input data-testid="case-required-tools" value={requiredTools} onChange={(e) => setRequiredTools(e.target.value)} className="mt-1" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">forbidden_tools (comma-separated)</label>
                    <Input data-testid="case-forbidden-tools" value={forbiddenTools} onChange={(e) => setForbiddenTools(e.target.value)} className="mt-1" />
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">Knowledge policy</p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground">knowledge_expected</label>
                    <Select value={knowledgeExpected} onValueChange={(v) => setKnowledgeExpected(v as Tri)}>
                      <SelectTrigger className="mt-1" data-testid="case-knowledge-expected"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unset">unset</SelectItem>
                        <SelectItem value="true">true</SelectItem>
                        <SelectItem value="false">false</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5 pt-4">
                    <TriField label="expect_retrieval" value={expectRetrieval} onChange={setExpectRetrieval} testId="case-expect-retrieval" />
                    <TriField label="expect_injection" value={expectInjection} onChange={setExpectInjection} testId="case-expect-injection" />
                    <TriField label="require_valid_markers" value={requireValidMarkers} onChange={setRequireValidMarkers} testId="case-require-valid-markers" />
                    <TriField label="require_no_knowledge" value={requireNoKnowledge} onChange={setRequireNoKnowledge} testId="case-require-no-knowledge" />
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">Grounding expectations</p>
                <div className="space-y-1.5">
                  <TriField label="require_citation" value={requireCitation} onChange={setRequireCitation} testId="case-require-citation" />
                  <TriField label="forbid_fabricated_markers" value={forbidFabricatedMarkers} onChange={setForbidFabricatedMarkers} testId="case-forbid-fabricated-markers" />
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label><Input data-testid="case-tags" value={caseTags} onChange={(e) => setCaseTags(e.target.value)} className="mt-1" /></div>
              <div><label className="text-xs font-medium text-muted-foreground">Ordering</label><Input data-testid="case-ordering" type="number" value={ordering} onChange={(e) => setOrdering(Number(e.target.value))} className="mt-1" /></div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Metadata (JSON)</label>
              <Textarea data-testid="case-metadata" value={metadata} onChange={(e) => { setMetadata(e.target.value); setMetadataError(null); }} rows={3} className="mt-1 font-mono text-xs" />
              <ErrorNote message={metadataError} className="mt-1.5" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCaseOpen(false)}>Cancel</Button>
            <Button data-testid="case-save-submit" disabled={!inputText.trim() || saveCase.isPending} onClick={submitCase}>{saveCase.isPending ? "Saving…" : editingCase ? "Save changes" : "Add case"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
