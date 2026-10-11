import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Settings2, RefreshCw, Star, CheckCircle2, AlertCircle, KeyRound } from "lucide-react";
import {
  apiError,
  testFetchProvider,
  testSearchProvider,
  updateFetchProvider,
  updateSearchProvider,
} from "@/lib/api";
import { useFetchProviders, useSearchProviders } from "@/lib/hooks";
import { providerItems, providerKeySourceOf, providerStatusOf } from "@/lib/providers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";

type ProviderKind = "search" | "fetch";

interface ConfigureState {
  kind: ProviderKind;
  provider: any;
}

export interface ProviderConfigureDialogProps {
  target: ConfigureState | null;
  onClose: () => void;
}

// Configure dialog: API key (password, never displays an existing key),
// reuse-other-provider checkbox, save via PATCH. Keyboard-closable via the
// shared Dialog (Esc + close button).
export const ProviderConfigureDialog: React.FC<ProviderConfigureDialogProps> = ({
  target,
  onClose,
}) => {
  const qc = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState("");
  const [reuseOther, setReuseOther] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok?: boolean; status?: string; message?: string } | null>(null);

  React.useEffect(() => {
    if (target) {
      setApiKey("");
      setName(target.provider?.name || "");
      setReuseOther(false);
      setSaveError(null);
      setTestResult(null);
    }
  }, [target?.provider?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const kind: ProviderKind = target?.kind || "search";
  const provider = target?.provider;
  const queryKey = kind === "search" ? "search-providers" : "fetch-providers";
  const reuseField = kind === "search" ? "reuse_fetch_credential" : "reuse_search_credential";
  const testIdPrefix = kind === "search" ? "search" : "fetch";

  const testAfterSave = useMutation({
    mutationFn: async (id: string) =>
      kind === "search" ? testSearchProvider(id) : testFetchProvider(id),
    onSuccess: (res) => {
      setTestResult(res);
      qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (e: any) =>
      setTestResult({ ok: false, status: "ERROR", message: apiError(e) }),
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      if (!provider) throw new Error("No provider selected");
      const body: Record<string, any> = {};
      if (name.trim() && name.trim() !== String(provider.name || "")) body.name = name.trim();
      if (apiKey) body.api_key = apiKey;
      if (reuseOther) body[reuseField] = true;
      if (Object.keys(body).length === 0) throw new Error("Enter a new key or reuse option to save");
      return kind === "search"
        ? updateSearchProvider(provider.id, body)
        : updateFetchProvider(provider.id, body);
    },
    onSuccess: () => {
      toast.success("Provider updated");
      setApiKey("");
      setSaveError(null);
      qc.invalidateQueries({ queryKey: [queryKey] });
      // Auto-run Test Connection after save.
      if (provider) testAfterSave.mutate(provider.id);
    },
    onError: (e) => {
      const msg = apiError(e);
      setSaveError(msg);
      toast.error(msg);
    },
  });

  return (
    <Dialog open={!!target} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent data-testid={`provider-configure-dialog-${testIdPrefix}`}>
        <DialogHeader>
          <DialogTitle className="font-display">
            Configure {provider?.name || "provider"}
          </DialogTitle>
          <DialogDescription>
            Stored server-side only. Existing secrets are never displayed —
            enter a new value to replace.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Display name</label>
            <Input
              data-testid={`provider-configure-name-${testIdPrefix}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">API key (new value)</label>
            <Input
              data-testid={`provider-configure-key-${testIdPrefix}`}
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="mt-1 font-mono text-xs"
              placeholder="Stored — enter new value to replace"
            />
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-xs">
            <Checkbox
              checked={reuseOther}
              onCheckedChange={(v) => setReuseOther(v === true)}
            />
            <span data-testid={`provider-configure-reuse-label-${testIdPrefix}`}>
              {kind === "search"
                ? "Reuse the fetch provider credential"
                : "Reuse the search provider credential"}
            </span>
          </label>
          {saveError && <ErrorNote message={saveError} />}
          {testResult && (
            <div
              className={`flex items-start gap-1.5 rounded border p-2 text-[11px] ${
                testResult.ok
                  ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                  : "border-destructive/20 bg-destructive/10 text-destructive"
              }`}
              data-testid={`provider-configure-test-result-${testIdPrefix}`}
            >
              {testResult.ok ? (
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : (
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <span className="font-semibold">
                  {testResult.status || (testResult.ok ? "CONNECTED" : "FAILED")}:{" "}
                </span>
                <span>{testResult.message}</span>
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} data-testid={`provider-configure-cancel-${testIdPrefix}`}>
            Cancel
          </Button>
          <Button
            data-testid={`provider-configure-save-${testIdPrefix}`}
            disabled={saveMut.isPending || testAfterSave.isPending}
            onClick={() => saveMut.mutate()}
          >
            {(saveMut.isPending || testAfterSave.isPending) && (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            )}
            {saveMut.isPending ? "Saving…" : testAfterSave.isPending ? "Testing…" : "Save & test"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const STATUS_STYLES: Record<string, string> = {
  CONNECTED: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
  NOT_CONFIGURED: "text-amber-500 border-amber-500/20",
  ERROR: "text-destructive border-destructive/30",
};

const ProviderCard: React.FC<{
  kind: ProviderKind;
  provider: any;
  showSetDefault: boolean;
  onConfigure: (p: any) => void;
}> = ({ kind, provider, showSetDefault, onConfigure }) => {
  const qc = useQueryClient();
  const [testResult, setTestResult] = useState<{ ok?: boolean; status?: string; message?: string } | null>(null);
  const status = providerStatusOf(provider);
  const keySource = providerKeySourceOf(provider);
  const testIdPrefix = kind === "search" ? "search" : "fetch";
  const queryKey = kind === "search" ? "search-providers" : "fetch-providers";

  const testMut = useMutation({
    mutationFn: async () =>
      kind === "search" ? testSearchProvider(provider.id) : testFetchProvider(provider.id),
    onSuccess: (res) => {
      setTestResult(res);
      qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (e: any) =>
      setTestResult({ ok: false, status: "ERROR", message: apiError(e) }),
  });

  const defaultMut = useMutation({
    mutationFn: async () =>
      kind === "search"
        ? updateSearchProvider(provider.id, { is_default: true })
        : updateFetchProvider(provider.id, { is_default: true }),
    onSuccess: () => {
      toast.success("Default provider updated");
      qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div
      className="space-y-2 rounded-md border border-border/60 p-3 text-xs"
      data-testid={`provider-card-${testIdPrefix}-${provider.id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{provider.name}</span>
        <Badge variant="outline" className="text-[10px]" data-testid={`provider-capability-${testIdPrefix}-${provider.id}`}>
          {kind === "search" ? "Search" : "Fetch"}
        </Badge>
        <Badge
          variant="outline"
          className={`text-[10px] ${STATUS_STYLES[status.badge] || ""}`}
          data-testid={`provider-status-${testIdPrefix}-${provider.id}`}
        >
          {status.badge}
        </Badge>
        {provider.is_default && (
          <Badge variant="secondary" className="text-[10px]" data-testid={`provider-default-${testIdPrefix}-${provider.id}`}>
            <Star className="mr-0.5 h-3 w-3" /> default
          </Badge>
        )}
        <Badge
          variant="outline"
          className="ml-auto font-mono text-[10px]"
          title="Where this provider reads its secret"
          data-testid={`provider-key-source-${testIdPrefix}-${provider.id}`}
        >
          <KeyRound className="mr-1 h-3 w-3" /> {keySource}
        </Badge>
      </div>
      <div className="font-mono text-[10px] text-muted-foreground">
        {(provider.provider_type || "unknown")} · {(provider.id || "")}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          data-testid={`provider-configure-${testIdPrefix}-${provider.id}`}
          onClick={() => onConfigure(provider)}
        >
          <Settings2 className="mr-1.5 h-3.5 w-3.5" /> Configure
        </Button>
        <Button
          size="sm"
          variant="outline"
          data-testid={`provider-test-${testIdPrefix}-${provider.id}`}
          disabled={testMut.isPending}
          onClick={() => {
            setTestResult(null);
            testMut.mutate();
          }}
        >
          {testMut.isPending ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          Test Connection
        </Button>
        {showSetDefault && !provider.is_default && (
          <Button
            size="sm"
            variant="ghost"
            data-testid={`provider-set-default-${testIdPrefix}-${provider.id}`}
            disabled={defaultMut.isPending}
            onClick={() => defaultMut.mutate()}
          >
            <Star className="mr-1.5 h-3.5 w-3.5" /> Set Default
          </Button>
        )}
      </div>
      {testResult && (
        <div
          className={`flex items-start gap-1.5 rounded border p-2 text-[11px] ${
            testResult.ok
              ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "border-destructive/20 bg-destructive/10 text-destructive"
          }`}
          data-testid={`provider-test-result-${testIdPrefix}-${provider.id}`}
        >
          {testResult.ok ? (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : (
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          )}
          <div className="min-w-0 flex-1">
            <span className="font-semibold">
              {testResult.status || (testResult.ok ? "CONNECTED" : "FAILED")}:{" "}
            </span>
            <span>{testResult.message}</span>
          </div>
        </div>
      )}
    </div>
  );
};

const ReadOnlyValve: React.FC<{ label: string; value: any }> = ({ label, value }) =>
  value == null || value === "" ? null : (
    <span className="inline-flex items-center gap-1 rounded border border-border/60 bg-muted/30 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
      <span className="opacity-70">{label}</span>
      <span className="text-foreground/80">{String(value)}</span>
    </span>
  );

// Grouped TinyFish configuration: one shared secret powers both the search and
// fetch providers. The key is written server-side only and never displayed.
const TinyFishGroup: React.FC<{ search: any; fetch: any }> = ({ search, fetch }) => {
  const qc = useQueryClient();
  const [sharedKey, setSharedKey] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const missing = !search || !fetch;

  const saveMut = useMutation({
    mutationFn: async (secret: string) => {
      if (!search || !fetch) throw new Error("Both TinyFish providers must be registered");
      await updateSearchProvider(search.id, { api_key: secret });
      await updateFetchProvider(fetch.id, { api_key: secret });
    },
    onSuccess: () => {
      setSharedKey("");
      setSaveError(null);
      toast.success("TinyFish configuration saved");
      qc.invalidateQueries({ queryKey: ["search-providers"] });
      qc.invalidateQueries({ queryKey: ["fetch-providers"] });
    },
    onError: (e) => {
      const msg = apiError(e);
      setSaveError(msg);
      toast.error(msg);
    },
  });

  const searchValves = ((search as any)?.valve_values || {}) as Record<string, any>;
  const fetchValves = ((fetch as any)?.valve_values || {}) as Record<string, any>;
  const batchSupported = [search, fetch].some(
    (p) =>
      !!(p as any)?.capabilities?.supports_batch_fetch || !!(p as any)?.supports_batch_fetch
  );

  const providerTile = (kind: ProviderKind, p: any) => {
    if (!p) {
      return (
        <div
          key={`tinyfish-missing-${kind}`}
          className="rounded-md border border-dashed border-border/60 p-3 text-[11px] text-muted-foreground"
          data-testid={`tinyfish-provider-missing-${kind}`}
        >
          No TinyFish {kind} provider registered.
        </div>
      );
    }
    const status = providerStatusOf(p);
    return (
      <div
        key={`tinyfish-provider-${kind}`}
        className="space-y-1.5 rounded-md border border-border/60 p-3 text-xs"
        data-testid={`tinyfish-provider-${kind}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{p.name}</span>
          <Badge variant="outline" className="text-[10px]">
            {kind === "search" ? "Search" : "Fetch"}
          </Badge>
          <Badge
            variant="outline"
            className={`text-[10px] ${STATUS_STYLES[status.badge] || ""}`}
            data-testid={`tinyfish-status-${kind}`}
          >
            {status.badge}
          </Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {kind === "search" ? (
            <>
              <ReadOnlyValve label="location" value={searchValves.location} />
              <ReadOnlyValve label="language" value={searchValves.language} />
              <ReadOnlyValve label="max_results" value={searchValves.max_results} />
            </>
          ) : (
            <>
              <ReadOnlyValve label="format" value={fetchValves.format} />
              <ReadOnlyValve label="timeout_seconds" value={fetchValves.timeout_seconds} />
              <ReadOnlyValve label="batch size" value={fetchValves.max_urls} />
              <ReadOnlyValve label="URLs/min" value={fetchValves.fetch_urls_per_minute_limit} />
              <ReadOnlyValve label="URLs/day" value={fetchValves.fetch_urls_per_day_limit} />
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <Card className="space-y-3 p-4" data-testid="providers-tinyfish-group">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-display text-sm font-semibold">TinyFish Web</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground" data-testid="providers-tinyfish-hint">
            One TinyFish configuration powers Web Search, Web Fetch and Web Research. The secret is entered once.
          </p>
        </div>
        {batchSupported && (
          <Badge variant="outline" className="text-[10px]" data-testid="tinyfish-batch-fetch-badge">
            Batch fetch supported
          </Badge>
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {providerTile("search", search)}
        {providerTile("fetch", fetch)}
      </div>

      {missing ? (
        <p className="text-xs text-muted-foreground" data-testid="tinyfish-missing-note">
          Register both a TinyFish search provider and a TinyFish fetch provider to configure the shared
          secret.
        </p>
      ) : (
        <div className="space-y-2">
          <label
            htmlFor="tinyfish-shared-key"
            className="text-xs font-medium text-muted-foreground"
          >
            TinyFish API key
          </label>
          <Input
            id="tinyfish-shared-key"
            data-testid="tinyfish-shared-key"
            type="password"
            autoComplete="new-password"
            className="font-mono text-xs"
            placeholder="Stored server-side — entered once"
            value={sharedKey}
            onChange={(e) => setSharedKey(e.target.value)}
          />
          {saveError && <ErrorNote message={saveError} />}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              data-testid="tinyfish-shared-save"
              disabled={!sharedKey || saveMut.isPending}
              onClick={() => saveMut.mutate(sharedKey)}
            >
              {saveMut.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              Save
            </Button>
            <span className="text-[10px] text-muted-foreground">
              Applies to both search and fetch. The key is never displayed after saving.
            </span>
          </div>
        </div>
      )}
    </Card>
  );
};

// Providers tab: TOOL vs PROVIDER vs CREDENTIAL explanation plus the
// SEARCH PROVIDERS / FETCH PROVIDERS sections.
export const ProvidersPanel: React.FC = () => {
  const searchQuery = useSearchProviders();
  const fetchQuery = useFetchProviders();
  const [configureTarget, setConfigureTarget] = useState<ConfigureState | null>(null);

  const searchProviders = providerItems(searchQuery.data);
  const fetchProviders = providerItems(fetchQuery.data);

  const section = (
    kind: ProviderKind,
    title: string,
    items: any[],
    isLoading: boolean,
    isError: boolean,
    error: any,
  ) => (
    <section data-testid={`providers-section-${kind}`}>
      <h3 className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {isLoading ? (
        <LoadingSpinner label={`Loading ${title.toLowerCase()}…`} />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : items.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid={`providers-empty-${kind}`}>
          No {title.toLowerCase()} registered.
        </p>
      ) : (
        <div className="grid gap-2">
          {items.map((p: any) => (
            <ProviderCard
              key={p.id}
              kind={kind}
              provider={p}
              showSetDefault={items.length > 1}
              onConfigure={(prov) => setConfigureTarget({ kind, provider: prov })}
            />
          ))}
        </div>
      )}
    </section>
  );

  return (
    <div className="space-y-4" data-testid="providers-panel">
      <Card className="space-y-1.5 p-4">
        <h3 className="font-display text-sm font-semibold">Search & fetch providers</h3>
        <p className="text-[11px] text-muted-foreground" data-testid="providers-explainer">
          A TOOL is a capability exposed to the model. A PROVIDER is the backend
          service supplying it. A CREDENTIAL is the secret a provider uses to
          authenticate.
        </p>
      </Card>
      <TinyFishGroup
        search={searchProviders.find((p) => p.provider_type === "tinyfish")}
        fetch={fetchProviders.find((p) => p.provider_type === "tinyfish")}
      />
      {section("search", "Search providers", searchProviders, searchQuery.isLoading, searchQuery.isError, searchQuery.error)}
      {section("fetch", "Fetch providers", fetchProviders, fetchQuery.isLoading, fetchQuery.isError, fetchQuery.error)}
      <ProviderConfigureDialog target={configureTarget} onClose={() => setConfigureTarget(null)} />
    </div>
  );
};
