import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Server, Boxes, Ruler, Trash2, Pencil, Zap, CheckCircle2, XCircle, Loader2, ShieldCheck, RefreshCw, AlertCircle, Check, ChevronsUpDown, Wrench, FileJson, ShieldAlert, FlaskConical, History, Terminal } from "lucide-react";
import { api, apiError, codeInterpreterStatus } from "@/lib/api";
import { useModels, useProviders, useProviderModelCatalog, useRubrics, useToolRegistry, useTool, useToolVersions } from "@/lib/hooks";
import { ModelRecord, Provider, ProviderModelCatalogItem, Rubric, ToolRecord } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { EmptyState, SectionHeader, FamilyDot, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { fmtCost } from "@/lib/format";
import { CapabilityBadges } from "@/components/models/CapabilityBadges";
import { CapabilityOverrideDialog } from "@/components/models/CapabilityOverrideDialog";

const registeredPrice = (input: number | null | undefined, output: number | null | undefined) => {
  if (input == null && output == null) return "Pricing unavailable";
  return `${input == null ? "Pricing unavailable" : fmtCost(input, true)} / ${output == null ? "Pricing unavailable" : fmtCost(output, true)}`;
};

const catalogPrice = (price: number | null | undefined) => (price == null ? "n/a" : `$${price}/1M`);

const compactMetadata = (value: Record<string, any> | undefined) => {
  const entries = Object.entries(value || {});
  if (!entries.length) return "Not provided";
  const summary = entries.slice(0, 4).map(([key, item]) => `${key}: ${Array.isArray(item) ? item.join(", ") : String(item)}`).join(" · ");
  return entries.length > 4 ? `${summary} · +${entries.length - 4} more` : summary;
};

const jsonMetadata = (value: Record<string, any> | undefined) => JSON.stringify(value || {}, null, 2);

const parseMetadata = (value: string) => {
  if (!value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

/* -------------------------------------------------------------- Capabilities */
// Shared normalized badges + provenance live in
// components/models/CapabilityBadges (single source of truth with the
// workspace compatibility inspector).
/* ------------------------------------------------------------------ Providers */
const ProviderDialog: React.FC<{ open: boolean; onOpenChange: (v: boolean) => void; editing?: Provider | null }> = ({ open, onOpenChange, editing }) => {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [type, setType] = useState<"openai_compatible">("openai_compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  React.useEffect(() => {
    if (open) {
      setName(editing?.name || "");
      setType((editing?.provider_type as any) || "openai_compatible");
      setBaseUrl(editing?.base_url || "");
      setApiKey("");
    }
  }, [open, editing]);

  const mut = useMutation({
    mutationFn: async () => {
      const body: any = { name, provider_type: type, base_url: baseUrl || null };
      if (apiKey) body.api_key = apiKey;
      if (editing) return (await api.patch(`/providers/${editing.id}`, body)).data;
      return (await api.post("/providers", body)).data;
    },
    onSuccess: () => { toast.success(editing ? "Provider updated" : "Provider added"); qc.invalidateQueries({ queryKey: ["providers"] }); onOpenChange(false); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="provider-dialog">
        <DialogHeader><DialogTitle className="font-display">{editing ? "Edit provider" : "Add provider"}</DialogTitle>
          <DialogDescription>Register an OpenAI-compatible (BYOK) provider. API keys are encrypted server-side.</DialogDescription></DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Name</label>
            <Input data-testid="provider-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" placeholder="e.g. OpenRouter" />
          </div>
          {!editing && (
            <div>
              <label className="text-xs font-medium text-muted-foreground">Type</label>
              <Select value={type} onValueChange={(v) => setType(v as any)}>
                <SelectTrigger className="mt-1" data-testid="provider-type-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="openai_compatible">OpenAI-compatible (BYOK)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          {type === "openai_compatible" && (
            <>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Base URL <span className="text-muted-foreground/70">(include /v1)</span></label>
                <Input data-testid="provider-baseurl-input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-1 font-mono text-xs" placeholder="https://openrouter.ai/api/v1" />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">API key {editing && <span className="text-muted-foreground/70">(leave blank to keep)</span>}</label>
                <Input data-testid="provider-apikey-input" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mt-1 font-mono text-xs" placeholder="sk-…" />
                <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground"><ShieldCheck className="h-3 w-3" /> Stored encrypted server-side. Never sent to the browser.</p>
              </div>
            </>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="provider-submit" disabled={!name.trim() || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? "Saving…" : "Save"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const ProvidersTab: React.FC = () => {
  const { data: providers = [], isLoading } = useProviders();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/providers/${id}`)).data,
    onSuccess: () => { toast.success("Provider deleted"); qc.invalidateQueries({ queryKey: ["providers"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  const test = async (p: Provider) => {
    setTesting(p.id);
    try {
      const { data } = await api.post(`/providers/${p.id}/test`);
      data.ok ? toast.success(`${p.name}: ${data.message}`) : toast.error(`${p.name}: ${data.message}`);
    } catch (e) { toast.error(apiError(e)); }
    finally { setTesting(null); }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" data-testid="add-provider-button" onClick={() => { setEditing(null); setDialogOpen(true); }}><Plus className="mr-1.5 h-4 w-4" /> Add provider</Button>
      </div>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading…</p> : providers.length === 0 ? (
        <EmptyState icon={<Server className="h-6 w-6" />} title="No providers" description="Add an OpenAI-compatible provider to connect your own models." testId="providers-empty" />
      ) : (
        <div className="grid gap-3">
          {providers.map((p) => (
            <Card key={p.id} className="flex items-center justify-between p-4" data-testid={`provider-row-${p.id}`}>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.name}</span>
                  {p.seeded && <Badge variant="secondary" className="text-[10px]">seeded</Badge>}
                  <Badge variant="outline" className="font-mono text-[10px]">{p.provider_type}</Badge>
                  {!p.enabled && <Badge variant="outline" className="text-[10px] text-destructive">disabled</Badge>}
                </div>
                <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{p.base_url || "—"} · {p.has_api_key ? "key set" : "no key"}</p>
              </div>
              <div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" data-testid={`test-provider-${p.id}`} onClick={() => test(p)} disabled={testing === p.id}>
                  {testing === p.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                </Button>
                <Button size="sm" variant="ghost" data-testid={`edit-provider-${p.id}`} onClick={() => { setEditing(p); setDialogOpen(true); }}><Pencil className="h-4 w-4" /></Button>
                <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-provider-${p.id}`} onClick={() => del.mutate(p.id)}><Trash2 className="h-4 w-4" /></Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <ProviderDialog open={dialogOpen} onOpenChange={setDialogOpen} editing={editing} />
    </div>
  );
};

/* ------------------------------------------------------------------ Models */
const ModelDialog: React.FC<{ open: boolean; onOpenChange: (v: boolean) => void; editing?: ModelRecord | null; providers: Provider[] }> = ({ open, onOpenChange, editing, providers }) => {
  const qc = useQueryClient();
  const [providerId, setProviderId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [modelId, setModelId] = useState("");
  const [family, setFamily] = useState("");
  const [inputCost, setInputCost] = useState("");
  const [outputCost, setOutputCost] = useState("");
  const [tags, setTags] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [capabilitiesText, setCapabilitiesText] = useState("{}");
  const [modalitiesText, setModalitiesText] = useState("{}");
  const [providerMetadata, setProviderMetadata] = useState<Record<string, any>>({});
  const [pricingSource, setPricingSource] = useState("");
  const [pricingUpdatedAt, setPricingUpdatedAt] = useState("");
  const [cacheReadPrice, setCacheReadPrice] = useState<string>("");
  const [cacheWritePrice, setCacheWritePrice] = useState<string>("");
  const [reasoningPrice, setReasoningPrice] = useState<string>("");
  const [rawPricing, setRawPricing] = useState<Record<string, any> | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [selectedCatalogId, setSelectedCatalogId] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  React.useEffect(() => {
    if (open) {
      setProviderId(editing?.provider_id || providers[0]?.id || "");
      setDisplayName(editing?.display_name || "");
      setModelId(editing?.model_identifier || "");
      setFamily(editing?.family || "");
      setContextWindow(editing?.context_window != null ? String(editing.context_window) : "");
      setInputCost(String(editing?.input_price_per_1m ?? editing?.input_price_per_1m_tokens ?? editing?.input_cost ?? ""));
      setOutputCost(String(editing?.output_price_per_1m ?? editing?.output_price_per_1m_tokens ?? editing?.output_cost ?? ""));
      setCacheReadPrice(editing?.cache_read_price_per_1m != null ? String(editing.cache_read_price_per_1m) : "");
      setCacheWritePrice(editing?.cache_write_price_per_1m != null ? String(editing.cache_write_price_per_1m) : "");
      setReasoningPrice(editing?.reasoning_price_per_1m != null ? String(editing.reasoning_price_per_1m) : "");
      setRawPricing(editing?.raw_pricing ?? null);
      setCapabilitiesText(jsonMetadata(editing?.capabilities));
      setModalitiesText(jsonMetadata(editing?.modalities));
      setProviderMetadata(editing?.provider_metadata || {});
      setPricingSource(editing?.pricing_source || "");
      setPricingUpdatedAt(editing?.pricing_updated_at || "");
      setSelectedCatalogId("");
      setCatalogOpen(false);
      setManualMode(Boolean(editing));
      setAdvancedOpen(Boolean(editing));
      setTags((editing?.tags || []).join(", "));
    }
  }, [open, editing, providers]);

  const selectedProvider = providers.find((p) => p.id === providerId);

  const catalogQuery = useProviderModelCatalog(
    providerId,
    open && selectedProvider?.supports_model_discovery !== false,
  );
  const catalogModels = catalogQuery.data?.models || [];
  const selectedCatalogItem = catalogModels.find((item) => item.model_identifier === selectedCatalogId);
  const catalogLocked = Boolean(selectedCatalogItem && !manualMode);

  const dialogCapabilities = parseMetadata(capabilitiesText);
  const dialogModalities = parseMetadata(modalitiesText) as Record<string, string[]>;
  const capabilityModel = { id: editing?.id || "new", capabilities: dialogCapabilities, modalities: dialogModalities };

  const clearModelDetails = () => {
    setDisplayName("");
    setModelId("");
    setContextWindow("");
    setInputCost("");
    setOutputCost("");
    setCacheReadPrice("");
    setCacheWritePrice("");
    setReasoningPrice("");
    setRawPricing(null);
    setCapabilitiesText("{}");
    setModalitiesText("{}");
    setProviderMetadata({});
    setPricingSource("");
    setPricingUpdatedAt("");
  };

  const applyCatalogItem = (item: ProviderModelCatalogItem) => {
    setSelectedCatalogId(item.model_identifier);
    setDisplayName(item.display_name);
    setModelId(item.model_identifier);
    setContextWindow(item.context_window == null ? "" : String(item.context_window));
    setInputCost(String(item.input_price_per_1m ?? item.input_price_per_1m_tokens ?? item.input_cost ?? ""));
    setOutputCost(String(item.output_price_per_1m ?? item.output_price_per_1m_tokens ?? item.output_cost ?? ""));
    setCacheReadPrice(item.cache_read_price_per_1m != null ? String(item.cache_read_price_per_1m) : "");
    setCacheWritePrice(item.cache_write_price_per_1m != null ? String(item.cache_write_price_per_1m) : "");
    setReasoningPrice(item.reasoning_price_per_1m != null ? String(item.reasoning_price_per_1m) : "");
    setRawPricing(item.raw_pricing ?? null);
    setCapabilitiesText(jsonMetadata(item.capabilities));
    setModalitiesText(jsonMetadata(item.modalities));
    setProviderMetadata(item.provider_metadata || {});
    setPricingSource(item.pricing_source || "");
    setPricingUpdatedAt(item.pricing_updated_at || "");
    setManualMode(false);
    setCatalogOpen(false);
  };

  const enterManualMode = () => {
    setManualMode(true);
    setSelectedCatalogId("");
    setAdvancedOpen(true);
  };

  const selectProvider = (value: string) => {
    setProviderId(value);
    setSelectedCatalogId("");
    setManualMode(false);
    clearModelDetails();
  };

  const applyModelMetadata = (model: Partial<ModelRecord>) => {
    setDisplayName(model.display_name || "");
    setModelId(model.model_identifier || "");
    setContextWindow(model.context_window == null ? "" : String(model.context_window));
    setInputCost(String(model.input_price_per_1m ?? model.input_price_per_1m_tokens ?? model.input_cost ?? ""));
    setOutputCost(String(model.output_price_per_1m ?? model.output_price_per_1m_tokens ?? model.output_cost ?? ""));
    setCacheReadPrice(model.cache_read_price_per_1m != null ? String(model.cache_read_price_per_1m) : "");
    setCacheWritePrice(model.cache_write_price_per_1m != null ? String(model.cache_write_price_per_1m) : "");
    setReasoningPrice(model.reasoning_price_per_1m != null ? String(model.reasoning_price_per_1m) : "");
    setRawPricing(model.raw_pricing ?? null);
    setCapabilitiesText(jsonMetadata(model.capabilities));
    setModalitiesText(jsonMetadata(model.modalities));
    setProviderMetadata(model.provider_metadata || {});
    setPricingSource(model.pricing_source || "");
    setPricingUpdatedAt(model.pricing_updated_at || "");
    setSelectedCatalogId(model.model_identifier || "");
  };
  const mut = useMutation({
    mutationFn: async () => {
      const body: any = {
        provider_id: providerId, display_name: displayName, model_identifier: modelId,
        family: family || null,
        context_window: contextWindow === "" ? null : parseInt(contextWindow, 10),
        input_cost: inputCost === "" ? null : parseFloat(inputCost),
        output_cost: outputCost === "" ? null : parseFloat(outputCost),
        input_price_per_1m: inputCost === "" ? null : parseFloat(inputCost),
        output_price_per_1m: outputCost === "" ? null : parseFloat(outputCost),
        input_price_per_1m_tokens: inputCost === "" ? null : parseFloat(inputCost),
        output_price_per_1m_tokens: outputCost === "" ? null : parseFloat(outputCost),
        cache_read_price_per_1m: cacheReadPrice === "" ? null : parseFloat(cacheReadPrice),
        cache_write_price_per_1m: cacheWritePrice === "" ? null : parseFloat(cacheWritePrice),
        cache_creation_price_per_1m: cacheWritePrice === "" ? null : parseFloat(cacheWritePrice),
        reasoning_price_per_1m: reasoningPrice === "" ? null : parseFloat(reasoningPrice),
        raw_pricing: rawPricing,
        capabilities: parseMetadata(capabilitiesText),
        modalities: parseMetadata(modalitiesText),
        provider_metadata: providerMetadata,
        pricing_source: pricingSource || null,
        pricing_updated_at: pricingUpdatedAt || null,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      };
      if (editing) return (await api.patch(`/models/${editing.id}`, body)).data;
      return (await api.post("/models", body)).data;
    },
    onSuccess: () => { toast.success(editing ? "Model updated" : "Model added"); qc.invalidateQueries({ queryKey: ["models"] }); onOpenChange(false); },
    onError: (e) => toast.error(apiError(e)),
  });


  const refresh = useMutation({
    mutationFn: async () => (await api.post(`/models/${editing!.id}/refresh-metadata`)).data,
    onSuccess: (result: any) => {
      qc.invalidateQueries({ queryKey: ["models"] });
      if (result?.refreshed && result.model) {
        applyModelMetadata(result.model);
        toast.success(result.message || "Model metadata refreshed");
      } else {
        toast.message(result?.message || "Model metadata was not refreshed");
      }
    },
    onError: (e) => toast.error(apiError(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="model-dialog">
        <DialogHeader><DialogTitle className="font-display">{editing ? "Edit model" : "Add model"}</DialogTitle>
          <DialogDescription>Select a provider model, or enter an identifier manually. Additional metadata is optional.</DialogDescription></DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="text-xs font-medium text-muted-foreground">Provider</label>
            <Select value={providerId} onValueChange={selectProvider}>
              <SelectTrigger className="mt-1" data-testid="model-provider-select"><SelectValue placeholder="Select provider" /></SelectTrigger>
              <SelectContent>{providers.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {(catalogQuery.isLoading || catalogQuery.isError || catalogQuery.data) && (
            <div className="col-span-2 space-y-2 rounded-md border border-border bg-muted/20 p-2.5" data-testid="model-catalog-section">
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs font-medium text-muted-foreground">Provider catalog</label>
              </div>
                {catalogQuery.data?.supports_model_discovery && (
                  <div className="flex items-center gap-2">
                    <Button type="button" size="sm" variant="ghost" className="h-7 text-[11px]" data-testid="model-refresh-models" onClick={() => catalogQuery.refetch()} disabled={catalogQuery.isFetching}>{catalogQuery.isFetching ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />} Refresh models</Button>
                    {editing && <Button type="button" size="sm" variant="ghost" className="h-7 text-[11px]" data-testid="model-refresh-metadata" onClick={() => refresh.mutate()} disabled={refresh.isPending}>{refresh.isPending ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <RefreshCw className="mr-1 h-3 w-3" />} Refresh metadata</Button>}
                    <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <Switch data-testid="model-manual-mode" checked={manualMode} onCheckedChange={setManualMode} />
                      Edit fields manually
                    </label>
                  </div>
                )}
              {catalogQuery.isLoading ? <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading available models…</p> : catalogQuery.isError ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
                  <span>Catalog unavailable: {apiError(catalogQuery.error)}</span>
                  <Button type="button" size="sm" variant="ghost" data-testid="model-catalog-retry" onClick={() => catalogQuery.refetch()}>Retry</Button>
                  <Button type="button" size="sm" variant="outline" data-testid="model-manual-button" onClick={enterManualMode}>Enter model manually</Button>
                </div>
              ) : catalogQuery.data?.supports_model_discovery ? (
                <>
                  <Popover open={catalogOpen} onOpenChange={setCatalogOpen}>
                    <PopoverTrigger asChild>
                      <Button type="button" variant="outline" className="w-full justify-between" data-testid="model-catalog-trigger">
                        {selectedCatalogItem ? `${selectedCatalogItem.display_name} · ${selectedCatalogItem.model_identifier}` : "Choose a model from the provider catalog"}
                        <ChevronsUpDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent align="start" className="w-[min(540px,calc(100vw-3rem))] p-0" data-testid="model-catalog-menu">
                      <Command>
                        <CommandInput data-testid="model-catalog-search" placeholder="Search display name or identifier…" />
                        <CommandList>
                          <CommandEmpty>No catalog models found.</CommandEmpty>
                          <CommandGroup>
                            {catalogModels.map((item) => (
                              <CommandItem key={item.model_identifier} value={`${item.display_name} ${item.model_identifier}`} onSelect={() => applyCatalogItem(item)} data-testid={`model-catalog-item-${item.model_identifier}`}>
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center justify-between gap-2"><span className="truncate font-medium">{item.display_name}</span><span className="shrink-0 text-[10px] text-muted-foreground">{item.context_window == null ? "Context unavailable" : `${item.context_window.toLocaleString()} tokens`}</span></div>
                                  <div className="truncate font-mono text-[11px] text-muted-foreground">{item.model_identifier}</div>
                                  <div className="mt-1 text-[10px] text-muted-foreground">
                                    Input {catalogPrice(item.input_price_per_1m ?? item.input_price_per_1m_tokens ?? item.input_cost)} · Output {catalogPrice(item.output_price_per_1m ?? item.output_price_per_1m_tokens ?? item.output_cost)}
                                    {(item.cache_read_price_per_1m != null || item.reasoning_price_per_1m != null) && (
                                      <span>
                                        {item.cache_read_price_per_1m != null && ` · Cache ${catalogPrice(item.cache_read_price_per_1m)}`}
                                        {item.reasoning_price_per_1m != null && ` · Reasoning ${catalogPrice(item.reasoning_price_per_1m)}`}
                                      </span>
                                    )}
                                  </div>
                                </div>
                                {selectedCatalogId === item.model_identifier && <Check className="h-4 w-4 shrink-0" />}
                              </CommandItem>
                            ))}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                  {!selectedCatalogItem && (
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-[11px] text-muted-foreground">Choose a catalog model to populate metadata, or add one manually.</p>
                      <Button type="button" size="sm" variant="outline" data-testid="model-manual-button" onClick={enterManualMode}>Enter model manually</Button>
                    </div>
                  )}
                </>
              ) : (
                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>Model discovery is not available for this provider.</span>
                  <Button type="button" size="sm" variant="outline" data-testid="model-manual-button" onClick={enterManualMode}>Enter model manually</Button>
                </div>
              )}
            </div>
          )}
          {manualMode && (
            <div className="col-span-2 rounded-md border border-border bg-muted/20 p-2.5" data-testid="model-manual-entry">
              <label className="text-xs font-medium text-muted-foreground">Model identifier</label>
              <Input data-testid="model-manual-identifier" value={modelId} onChange={(e) => { setModelId(e.target.value); if (!displayName) setDisplayName(e.target.value); }} className="mt-1 font-mono text-xs" placeholder="e.g. llama-3.1-8b" autoFocus={!editing} />
              <p className="mt-1 text-[11px] text-muted-foreground">The provider metadata endpoint was unavailable or manual entry was selected.</p>
            </div>
          )}
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen} className="col-span-2">
            <CollapsibleTrigger asChild>
              <Button type="button" variant="ghost" className="w-full justify-between px-2 text-xs" data-testid="model-advanced-toggle">
                <span>Advanced configuration</span><ChevronsUpDown className="h-3.5 w-3.5" />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="grid grid-cols-2 gap-3 pt-2">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Display name</label>
            <Input data-testid="model-name-input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} readOnly={catalogLocked} className="mt-1" placeholder="GPT-4o mini" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Model identifier</label>
            <Input data-testid="model-identifier-input" value={modelId} onChange={(e) => setModelId(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="gpt-4o-mini" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Context window</label>
            <Input data-testid="model-context-window" type="number" min="1" value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="optional" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Input $ / 1M</label>
            <Input data-testid="model-input-cost" value={inputCost} onChange={(e) => setInputCost(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="optional" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Output $ / 1M</label>
            <Input data-testid="model-output-cost" value={outputCost} onChange={(e) => setOutputCost(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="optional" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Cache Read $ / 1M</label>
            <Input data-testid="model-cache-read-cost" value={cacheReadPrice} onChange={(e) => setCacheReadPrice(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="optional" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Reasoning $ / 1M</label>
            <Input data-testid="model-reasoning-cost" value={reasoningPrice} onChange={(e) => setReasoningPrice(e.target.value)} readOnly={catalogLocked} className="mt-1 font-mono text-xs" placeholder="optional" />
          </div>
          {catalogLocked ? (
            <div className="col-span-2 space-y-1 rounded-md bg-muted/30 p-2 text-[11px] text-muted-foreground" data-testid="model-metadata-summary">
              <div><span className="font-medium text-foreground">Capabilities:</span> {compactMetadata(parseMetadata(capabilitiesText))}</div>
              <div><span className="font-medium text-foreground">Modalities:</span> {compactMetadata(parseMetadata(modalitiesText))}</div>
              <div><span className="font-medium text-foreground">Pricing:</span> {pricingSource || "Pricing source unavailable"}{pricingUpdatedAt ? ` · updated ${new Date(pricingUpdatedAt).toLocaleString()}` : ""}</div>
            </div>
          ) : (
            <>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Capabilities (JSON)</label>
                <Textarea data-testid="model-capabilities-input" value={capabilitiesText} onChange={(e) => setCapabilitiesText(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-medium text-muted-foreground">Modalities (JSON)</label>
                <Textarea data-testid="model-modalities-input" value={modalitiesText} onChange={(e) => setModalitiesText(e.target.value)} rows={2} className="mt-1 font-mono text-[11px]" />
              </div>
            </>
          )}
          <div className="col-span-2" data-testid="model-dialog-capabilities">
            <label className="text-xs font-medium text-muted-foreground">Capabilities</label>
            <CapabilityBadges model={capabilityModel} className="mt-1" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Pricing source</label>
            <Input data-testid="model-pricing-source" value={pricingSource} onChange={(e) => setPricingSource(e.target.value)} readOnly={catalogLocked} className="mt-1 text-xs" placeholder="optional" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Pricing updated</label>
            <Input data-testid="model-pricing-updated" value={pricingUpdatedAt} onChange={(e) => setPricingUpdatedAt(e.target.value)} readOnly={catalogLocked} className="mt-1 text-xs" placeholder="optional" />
          </div>
          <div className="col-span-2">
            <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
            <Input data-testid="model-tags-input" value={tags} onChange={(e) => setTags(e.target.value)} className="mt-1" placeholder="general, reasoning, judge" />
          </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button data-testid="model-submit" disabled={!(displayName.trim() || modelId.trim()) || !providerId || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? "Saving…" : "Add model"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const ModelsTab: React.FC = () => {
  const { data: models = [], isLoading } = useModels();
  const { data: providers = [] } = useProviders();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ModelRecord | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [capsOpen, setCapsOpen] = useState(false);
  const [capsFor, setCapsFor] = useState<ModelRecord | null>(null);
  const providerName = (id: string) => providers.find((p) => p.id === id)?.name || "—";

  const patch = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: any }) => (await api.patch(`/models/${id}`, body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/models/${id}`)).data,
    onSuccess: () => { toast.success("Model removed"); qc.invalidateQueries({ queryKey: ["models"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  const test = async (m: ModelRecord) => {
    setTesting(m.id);
    try {
      const { data } = await api.post(`/models/${m.id}/test`);
      data.ok ? toast.success(`${m.display_name}: OK (${data.latency_ms}ms)`) : toast.error(`${m.display_name}: ${data.error || "failed"}`);
    } catch (e) { toast.error(apiError(e)); }
    finally { setTesting(null); }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" data-testid="add-model-button" disabled={providers.length === 0} onClick={() => { setEditing(null); setDialogOpen(true); }}><Plus className="mr-1.5 h-4 w-4" /> Add model</Button>
      </div>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading…</p> : models.length === 0 ? (
        <EmptyState icon={<Boxes className="h-6 w-6" />} title="No models" description="Register a model against a provider to start experimenting." testId="models-empty" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5">Model</th>
                <th className="px-4 py-2.5">Provider</th>
                <th className="px-4 py-2.5">Cost /1M</th>
                <th className="px-4 py-2.5">Tags</th>
                <th className="px-4 py-2.5 text-center">Enabled</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {models.map((m) => (
                <tr key={m.id} className="transition-colors hover:bg-accent/40" data-testid={`model-row-${m.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <FamilyDot family={m.family} />
                      <div>
                        <div className="font-medium">{m.display_name}</div>
                        <div className="font-mono text-[11px] text-muted-foreground">{m.model_identifier}</div>
                        <CapabilityBadges model={m} showProvenance className="mt-1" />
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{providerName(m.provider_id)}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    <div>
                      {registeredPrice(m.input_price_per_1m ?? m.input_price_per_1m_tokens ?? m.input_cost, m.output_price_per_1m ?? m.output_price_per_1m_tokens ?? m.output_cost)}
                    </div>
                    {(m.cache_read_price_per_1m != null || m.reasoning_price_per_1m != null) && (
                      <div className="text-[10px] text-muted-foreground/70">
                        {m.cache_read_price_per_1m != null && `Cache: $${m.cache_read_price_per_1m}/1M`}
                        {m.cache_read_price_per_1m != null && m.reasoning_price_per_1m != null && " · "}
                        {m.reasoning_price_per_1m != null && `Reasoning: $${m.reasoning_price_per_1m}/1M`}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-wrap gap-1">{m.tags.slice(0, 3).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}</div>
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    <Switch data-testid={`model-enable-${m.id}`} checked={m.enabled} onCheckedChange={(v) => patch.mutate({ id: m.id, body: { enabled: v } })} />
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="ghost" data-testid={`test-model-${m.id}`} onClick={() => test(m)} disabled={testing === m.id}>
                        {testing === m.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                      </Button>
                      <Button size="sm" variant="ghost" title="Override capabilities" data-testid={`capabilities-model-${m.id}`} onClick={() => { setCapsFor(m); setCapsOpen(true); }}><Wrench className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" data-testid={`edit-model-${m.id}`} onClick={() => { setEditing(m); setDialogOpen(true); }}><Pencil className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-model-${m.id}`} onClick={() => del.mutate(m.id)}><Trash2 className="h-4 w-4" /></Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ModelDialog open={dialogOpen} onOpenChange={setDialogOpen} editing={editing} providers={providers} />
      <CapabilityOverrideDialog model={capsFor} open={capsOpen} onOpenChange={setCapsOpen} />
    </div>
  );
};

/* ------------------------------------------------------------------ Rubrics */
const RubricsTab: React.FC = () => {
  const { data: rubrics = [], isLoading } = useRubrics();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Rubric | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dims, setDims] = useState("");

  const openDialog = (r?: Rubric) => {
    setEditing(r || null);
    setName(r?.name || "");
    setDescription(r?.description || "");
    setDims((r?.dimensions || []).map((d) => `${d.key}: ${d.label}`).join("\n"));
    setOpen(true);
  };

  const save = useMutation({
    mutationFn: async () => {
      const dimensions = dims.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        const [key, ...rest] = l.split(":");
        const k = key.trim().replace(/\s+/g, "_").toLowerCase();
        return { key: k, label: (rest.join(":").trim() || key.trim()), description: "", max_score: 10 };
      });
      const body = { name, description, dimensions };
      if (editing) return (await api.patch(`/rubrics/${editing.id}`, body)).data;
      return (await api.post("/rubrics", body)).data;
    },
    onSuccess: () => { toast.success("Rubric saved"); qc.invalidateQueries({ queryKey: ["rubrics"] }); setOpen(false); },
    onError: (e) => toast.error(apiError(e)),
  });
  const del = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/rubrics/${id}`)).data,
    onSuccess: () => { toast.success("Rubric deleted"); qc.invalidateQueries({ queryKey: ["rubrics"] }); },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" data-testid="add-rubric-button" onClick={() => openDialog()}><Plus className="mr-1.5 h-4 w-4" /> New rubric</Button>
      </div>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading…</p> : rubrics.length === 0 ? (
        <EmptyState icon={<Ruler className="h-6 w-6" />} title="No rubrics" description="Rubrics define the scoring dimensions used by the AI Judge." testId="rubrics-empty" />
      ) : (
        <div className="grid gap-3">
          {rubrics.map((r) => (
            <Card key={r.id} className="p-4" data-testid={`rubric-row-${r.id}`}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2"><span className="font-medium">{r.name}</span>{r.seeded && <Badge variant="secondary" className="text-[10px]">seeded</Badge>}</div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{r.description}</p>
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" data-testid={`edit-rubric-${r.id}`} onClick={() => openDialog(r)}><Pencil className="h-4 w-4" /></Button>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-rubric-${r.id}`} onClick={() => del.mutate(r.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {r.dimensions.map((d) => <Badge key={d.key} variant="outline" className="text-[10px]">{d.label} · 0-{d.max_score}</Badge>)}
              </div>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="rubric-dialog">
          <DialogHeader><DialogTitle className="font-display">{editing ? "Edit rubric" : "New rubric"}</DialogTitle>
            <DialogDescription>Define the scoring dimensions the AI Judge uses to evaluate responses.</DialogDescription></DialogHeader>
          <div className="space-y-3">
            <div><label className="text-xs font-medium text-muted-foreground">Name</label><Input data-testid="rubric-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" /></div>
            <div><label className="text-xs font-medium text-muted-foreground">Description</label><Input data-testid="rubric-desc-input" value={description} onChange={(e) => setDescription(e.target.value)} className="mt-1" /></div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Dimensions (one per line, <span className="font-mono">key: Label</span>)</label>
              <Textarea data-testid="rubric-dims-input" value={dims} onChange={(e) => setDims(e.target.value)} rows={6} className="mt-1 font-mono text-xs" placeholder={"correctness: Correctness\nclarity: Clarity"} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="rubric-submit" disabled={!name.trim() || !dims.trim() || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

/* ------------------------------------------------------------------ Tools */
const riskVariant = (risk?: string | null) => {
  if (risk === "critical" || risk === "high") return "destructive";
  if (risk === "low") return "outline";
  return "secondary";
};

const JsonBlock: React.FC<{ label: string; value: any }> = ({ label, value }) => (
  <div>
    <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{label}</h4>
    <pre className="max-h-64 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">
      {JSON.stringify(value || {}, null, 2)}
    </pre>
  </div>
);

// Shared predicate: versioned custom tools (registry + project test flows).
const isVersionedCustomTool = (t: any) =>
  !t?.builtin && (t?.tool_type === "http" || t?.tool_type === "openapi" || t?.tool_type === "code_interpreter" || !!t?.current_version_id);

const TOOL_TYPE_BADGE: Record<string, string> = {
  function: "Built-in function",
  http: "HTTP tool",
  openapi: "OpenAPI tool",
  code_interpreter: "Code interpreter",
};

const toolTypeLabel = (t?: string | null) => TOOL_TYPE_BADGE[t || ""] || t || "function";

// Sandbox policy is read live from GET /code-interpreter/status (limits,
// packages, image, availability). The policy card renders only allowlisted,
// secret-free fields — status/execute payloads never carry credential values.
const SandboxPolicyBlock: React.FC = () => {
  const [state, setState] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(true);
  const [errText, setErrText] = React.useState<string | null>(null);
  React.useEffect(() => {
    let alive = true;
    codeInterpreterStatus()
      .then((s) => { if (alive) { setState(s); setLoading(false); } })
      .catch((e) => { if (alive) { setErrText(apiError(e)); setLoading(false); } });
    return () => { alive = false; };
  }, []);
  if (loading) return <LoadingSpinner label="Checking sandbox status…" />;
  if (errText) return <ErrorNote message={errText} />;
  const limits = state?.limits || {};
  const packages: string[] = state?.policy?.packages || [];
  return (
    <div className="space-y-2 rounded-md border border-border/60 p-2.5" data-testid="tool-sandbox-policy">
      <div className="flex flex-wrap items-center gap-2">
        {state?.available === true ? (
          <Badge variant="secondary" className="text-[10px]">sandbox available</Badge>
        ) : (
          <Badge variant="destructive" className="text-[10px]">
            sandbox unavailable{state?.reason ? `: ${state.reason}` : ""}
          </Badge>
        )}
        <Badge variant="outline" className="font-mono text-[10px]">network OFF</Badge>
        {state?.image && <Badge variant="outline" className="font-mono text-[10px]">{state.image}</Badge>}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Sandboxed Docker execution. Network is always off; CPU/RAM/PID/tmpfs/timeout limits enforced per run.
        {packages.length > 0 && <> Preinstalled: <span className="font-mono">{packages.join(", ")}</span>.</>}
      </p>
      {Object.keys(limits).length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[11px] text-muted-foreground sm:grid-cols-3">
          {Object.entries(limits).map(([k, v]) => <span key={k}>{k}: {String(v)}</span>)}
        </div>
      )}
    </div>
  );
};

// Implementation summary for http/openapi tools. Backend already sanitizes
// these fields (secret placeholders → [secret:NAME], credential ids only),
// so the dialog can render them verbatim without leaking secret values.
const HttpEndpointBlock: React.FC<{ tool: any }> = ({ tool }) => {
  const impl = tool?.implementation_config || tool?.implementation || null;
  const method = impl?.method;
  const url = impl?.url_template || impl?.url || tool?.url_template;
  if (!method && !url) return null;
  return (
    <div className="rounded-md border border-border/60 p-2.5" data-testid="tool-http-endpoint">
      <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Endpoint</h4>
      <p className="break-all font-mono text-xs">
        {method && <span className="mr-2 rounded bg-muted px-1.5 py-0.5">{method}</span>}
        {url || "—"}
      </p>
      {(tool?.risk_level || (tool?.permissions || []).length > 0) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {tool?.risk_level && <Badge variant={riskVariant(tool.risk_level)} className="text-[10px]">risk: {tool.risk_level}</Badge>}
          {(tool?.permissions || []).map((perm: string) => <Badge key={perm} variant="outline" className="font-mono text-[10px]">{perm}</Badge>)}
        </div>
      )}
    </div>
  );
};

const ToolTestBlock: React.FC<{ tool: any }> = ({ tool }) => {
  const [args, setArgs] = useState("{}");
  const [code, setCode] = useState('print("hello from sandbox")');
  const [result, setResult] = useState<any>(null);
  const isCode = tool?.tool_type === "code_interpreter";
  const mut = useMutation({
    mutationFn: async () => {
      let parsed: any = {};
      try { parsed = args.trim() ? JSON.parse(args) : {}; } catch { throw new Error("Arguments must be valid JSON"); }
      const body: any = { arguments: { ...parsed, ...(isCode ? { code } : {}) }, ...(isCode ? { code } : {}) };
      return (await api.post(`/tools/${tool.id}/test`, body)).data;
    },
    onSuccess: (r) => { setResult(r); toast.success("Tool test completed"); },
    onError: (e) => toast.error(apiError(e)),
  });
  return (
    <div className="space-y-2" data-testid="tool-test-tab">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Arguments (JSON)</label>
        <Textarea data-testid="tool-test-args" value={args} onChange={(e) => setArgs(e.target.value)} rows={4} className="mt-1 font-mono text-xs" />
      </div>
      {isCode && (
        <div>
          <label className="text-xs font-medium text-muted-foreground">Python code</label>
          <Textarea data-testid="tool-test-code" value={code} onChange={(e) => setCode(e.target.value)} rows={6} className="mt-1 font-mono text-xs" />
        </div>
      )}
      {result != null && (
        <div className="space-y-1.5" data-testid="tool-test-result">
          {(result?.tool_call?.duration_ms != null || result?.latency_ms != null) && (
            <p className="font-mono text-[11px] text-muted-foreground">
              latency: {result?.tool_call?.duration_ms ?? result?.latency_ms} ms
              {result?.tool_call?.execution_status ? ` · status: ${result.tool_call.execution_status}` : ""}
            </p>
          )}
          <pre className="max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[10px] scrollbar-thin">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
      <div>
        <Button size="sm" data-testid="tool-test-submit" disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? (<><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> Running…</>) : (<><FlaskConical className="mr-1 h-3.5 w-3.5" /> Run test</>)}
        </Button>
      </div>
      {mut.isError && <ErrorNote message={apiError(mut.error)} />}
    </div>
  );
};

const ToolSchemaDialog: React.FC<{
  toolId: string | null;
  fallback?: ToolRecord | null;
  onOpenChange: (o: boolean) => void;
}> = ({ toolId, fallback, onOpenChange }) => {
  const { data, isLoading, isError, error } = useTool(toolId);
  const tool: any = data || fallback || null;
  const [tab, setTab] = useState("schema");
  React.useEffect(() => { if (toolId) setTab("schema"); }, [toolId]);
  const showTest = isVersionedCustomTool(tool);
  return (
    <Dialog open={toolId != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl" data-testid="tool-schema-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">{tool?.display_name || tool?.name || "Tool schema"}</DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {tool?.name}{tool?.version ? ` · v${tool.version}` : ""}
            {tool?.current_version_id ? ` · ${String(tool.current_version_id).slice(0, 8)}` : ""}
          </DialogDescription>
        </DialogHeader>
        {isLoading && !tool ? (
          <LoadingSpinner label="Loading schema…" />
        ) : isError ? (
          <ErrorNote message={apiError(error)} />
        ) : tool ? (
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              {tool.builtin && <Badge variant="secondary" className="text-[10px]">built-in</Badge>}
              <Badge variant="outline" className="text-[10px]" title={toolTypeLabel(tool.tool_type)}>{tool.tool_type}</Badge>
              <Badge variant={riskVariant(tool.risk_level)} className="text-[10px]">risk: {tool.risk_level}</Badge>
              {tool.available === false ? (
                <Badge variant="destructive" className="text-[10px]">unavailable{tool.availability_reason ? `: ${tool.availability_reason}` : ""}</Badge>
              ) : (
                <Badge variant="secondary" className="text-[10px]">available</Badge>
              )}
              {(tool.permissions || []).map((perm: string) => <Badge key={perm} variant="outline" className="text-[10px] font-mono">{perm}</Badge>)}
            </div>
            {tool.description && <p className="text-xs text-muted-foreground">{tool.description}</p>}
            {tool.tool_type === "code_interpreter" && <SandboxPolicyBlock />}
            {(tool.tool_type === "http" || tool.tool_type === "openapi") && <HttpEndpointBlock tool={tool} />}
            {tool.implementation && typeof tool.implementation === "string" && (
              <div>
                <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Implementation</h4>
                <p className="break-all font-mono text-xs">{tool.implementation}</p>
              </div>
            )}
            {showTest ? (
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList data-testid="tool-dialog-tabs">
                  <TabsTrigger value="schema" data-testid="tool-dialog-tab-schema">Schema</TabsTrigger>
                  <TabsTrigger value="test" data-testid="tool-dialog-tab-test">Test</TabsTrigger>
                </TabsList>
                <TabsContent value="schema" className="mt-3 space-y-4">
                  <JsonBlock label="Input schema" value={tool.input_schema} />
                  <JsonBlock label="Output schema" value={tool.output_schema} />
                </TabsContent>
                <TabsContent value="test" className="mt-3">
                  <ToolTestBlock tool={tool} />
                </TabsContent>
              </Tabs>
            ) : (
              <>
                <JsonBlock label="Input schema" value={tool.input_schema} />
                <JsonBlock label="Output schema" value={tool.output_schema} />
              </>
            )}
          </div>
        ) : (
          <ErrorNote message="Tool not found." />
        )}
      </DialogContent>
    </Dialog>
  );
};

const RegistryVersionsDialog: React.FC<{ tool: ToolRecord | null; onClose: () => void }> = ({ tool, onClose }) => {
  const { data, isLoading, isError, error } = useToolVersions(tool?.id ?? null);
  const items = data?.items || [];
  return (
    <Dialog open={tool != null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent data-testid="tool-versions-dialog">
        <DialogHeader>
          <DialogTitle className="font-display">Versions · {tool?.display_name || tool?.name}</DialogTitle>
          <DialogDescription>GET /tools/{tool?.id}/versions — versions are immutable snapshots.</DialogDescription>
        </DialogHeader>
        {isLoading ? <LoadingSpinner label="Loading versions…" /> : isError ? <ErrorNote message={apiError(error)} /> : (
          data?.builtin ? <p className="text-xs text-muted-foreground">Built-in tools have no versions.</p> :
          items.length === 0 ? <p className="text-xs text-muted-foreground">No versions recorded.</p> : (
            <div className="max-h-64 space-y-2 overflow-y-auto scrollbar-thin">
              {items.map((v: any) => (
                <div key={v.id} className="rounded-md border border-border/60 p-2 text-xs" data-testid={`tool-version-${v.id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium">{v.version_label || `v${v.version ?? "?"}`}</span>
                    <Badge variant="outline" className="text-[10px]">{v.tool_type}</Badge>
                    <Badge variant="secondary" className="text-[10px]">risk: {v.risk_level || "low"}</Badge>
                    {v.created_at && <span className="font-mono text-[10px] text-muted-foreground">{v.created_at}</span>}
                    {(v.permissions || []).length > 0 && (
                      <span className="font-mono text-[10px] text-muted-foreground">{(v.permissions || []).join(", ")}</span>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-muted-foreground">Immutable — versions are never edited; new changes create a new version.</p>
                  <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 p-1.5 font-mono text-[10px] scrollbar-thin">{JSON.stringify(v.implementation_config || {}, null, 2)}</pre>
                </div>
              ))}
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
};

const RegistryTestDialog: React.FC<{ tool: ToolRecord | null; onClose: () => void }> = ({ tool, onClose }) => (
  <Dialog open={tool != null} onOpenChange={(o) => { if (!o) onClose(); }}>
    <DialogContent data-testid="tool-test-dialog">
      <DialogHeader>
        <DialogTitle className="font-display">Test {tool?.display_name || tool?.name}</DialogTitle>
        <DialogDescription>POST /tools/{tool?.id}/test — real execution. Errors are sanitized server-side.</DialogDescription>
      </DialogHeader>
      {tool && <ToolTestBlock tool={tool} />}
    </DialogContent>
  </Dialog>
);

const ToolRegistryTab: React.FC = () => {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useToolRegistry(undefined);
  const [selected, setSelected] = useState<ToolRecord | null>(null);
  const [versionsFor, setVersionsFor] = useState<ToolRecord | null>(null);
  const [testFor, setTestFor] = useState<ToolRecord | null>(null);

  const patch = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: any }) => (await api.patch(`/tools/${id}`, body)).data,
    onSuccess: (_updated, vars) => {
      qc.invalidateQueries({ queryKey: ["tools"] });
      qc.invalidateQueries({ queryKey: ["tool", vars.id] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const tools = data?.items || [];

  return (
    <div className="space-y-4" data-testid="tool-registry-tab">
      {isLoading ? (
        <LoadingSpinner label="Loading tool registry…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : tools.length === 0 ? (
        <EmptyState icon={<Wrench className="h-6 w-6" />} title="No tools" description="No tools are registered for this owner yet." testId="tools-empty" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5">Tool</th>
                <th className="px-4 py-2.5">Type</th>
                <th className="px-4 py-2.5">Version</th>
                <th className="px-4 py-2.5">Risk</th>
                <th className="px-4 py-2.5">Permissions</th>
                <th className="px-4 py-2.5">Availability</th>
                <th className="px-4 py-2.5 text-center">Enabled</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {tools.map((t) => (
                <tr key={t.id} className="transition-colors hover:bg-accent/40" data-testid={`tool-row-${t.id}`}>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{t.display_name || t.name}</span>
                          {t.builtin && <Badge variant="secondary" className="text-[10px]">built-in</Badge>}
                        </div>
                        <div className="font-mono text-[11px] text-muted-foreground">{t.name}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant="outline" className="text-[10px]" title={toolTypeLabel(t.tool_type)} data-testid={`tool-type-${t.id}`}>
                      {t.tool_type || "function"}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground" data-testid={`tool-version-${t.id}`}>
                    {t.version != null ? `v${t.version}` : "—"}
                    {(t as any).current_version_id && (
                      <span className="ml-1.5 text-[10px]" title={`current version ${(t as any).current_version_id}`}>
                        {(t as any).current_version_id.slice(0, 8)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5"><Badge variant={riskVariant(t.risk_level)} className="text-[10px]">{t.risk_level || "low"}</Badge></td>
                  <td className="px-4 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(t.permissions || []).length === 0 ? (
                        <span className="text-[11px] text-muted-foreground">none</span>
                      ) : (
                        (t.permissions || []).map((perm) => <Badge key={perm} variant="outline" className="text-[9px] font-mono">{perm}</Badge>)
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {t.available === false ? (
                      <div className="flex items-center gap-1.5 text-[11px] text-destructive">
                        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                        <span>{t.availability_reason || "unavailable"}</span>
                      </div>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">available</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    <Switch
                      data-testid={`tool-enabled-${t.id}`}
                      checked={t.enabled}
                      onCheckedChange={(v) => patch.mutate({ id: t.id, body: { enabled: v } })}
                    />
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {isVersionedCustomTool(t) && (
                        <>
                          <Button size="sm" variant="ghost" title="Test tool" data-testid={`tool-test-${t.id}`} onClick={() => setTestFor(t)}>
                            <FlaskConical className="h-4 w-4" />
                          </Button>
                          <Button size="sm" variant="ghost" title="Version history" data-testid={`tool-versions-${t.id}`} onClick={() => setVersionsFor(t)}>
                            <History className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      <Button size="sm" variant="ghost" title="View schema" data-testid={`tool-schema-${t.id}`} onClick={() => setSelected(t)}>
                        <FileJson className="h-4 w-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ToolSchemaDialog toolId={selected?.id ?? null} fallback={selected} onOpenChange={(o) => { if (!o) setSelected(null); }} />
      <RegistryVersionsDialog tool={versionsFor} onClose={() => setVersionsFor(null)} />
      <RegistryTestDialog tool={testFor} onClose={() => setTestFor(null)} />
    </div>
  );
};

export default function Models() {
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 lg:p-6">
      <SectionHeader title="Models & Providers" caption="Configure the execution layer. Providers hold credentials; models are what you run." />
      <Tabs defaultValue="models">
        <TabsList data-testid="registry-tabs">
          <TabsTrigger value="models" data-testid="tab-models"><Boxes className="mr-1.5 h-4 w-4" /> Models</TabsTrigger>
          <TabsTrigger value="providers" data-testid="tab-providers"><Server className="mr-1.5 h-4 w-4" /> Providers</TabsTrigger>
          <TabsTrigger value="rubrics" data-testid="tab-rubrics"><Ruler className="mr-1.5 h-4 w-4" /> Rubrics</TabsTrigger>
          <TabsTrigger value="tools" data-testid="tab-tools"><Wrench className="mr-1.5 h-4 w-4" /> Tools</TabsTrigger>
        </TabsList>
        <TabsContent value="models" className="mt-4"><ModelsTab /></TabsContent>
        <TabsContent value="providers" className="mt-4"><ProvidersTab /></TabsContent>
        <TabsContent value="rubrics" className="mt-4"><RubricsTab /></TabsContent>
        <TabsContent value="tools" className="mt-4"><ToolRegistryTab /></TabsContent>
      </Tabs>
    </div>
  );
}
