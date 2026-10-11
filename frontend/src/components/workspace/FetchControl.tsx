import React, { useState } from "react";
import { Globe, Loader2, CheckCircle2, AlertCircle, RefreshCw, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { testFetchProvider } from "@/lib/api";
import { useFetchProviders } from "@/lib/hooks";
import { providerItems, providerStatusOf } from "@/lib/providers";
import { FetchProvider } from "@/lib/types";

export const FetchControl: React.FC<{
  enabled: boolean;
  onEnabledChange: (v: boolean) => void;
  providerId: string | null;
  onProviderChange: (id: string | null) => void;
  disabled?: boolean;
}> = ({ enabled, onEnabledChange, providerId, onProviderChange, disabled }) => {
  const [testResult, setTestResult] = useState<{ ok?: boolean; status?: string; message?: string } | null>(null);

  const qc = useQueryClient();
  const { data, isLoading } = useFetchProviders();

  const providers: FetchProvider[] = providerItems(data) as FetchProvider[];
  const requestedDefault = providers.find((p) => (p as any).is_default);
  const activeProvider =
    providers.find((p) => p.id === (providerId || requestedDefault?.id || "prov-fetch-standard")) ||
    requestedDefault ||
    providers[0];
  const status = activeProvider ? providerStatusOf(activeProvider) : null;
  const notConfigured = !activeProvider || (status ? !status.connected : true);

  const testMut = useMutation({
    mutationFn: async (id: string) => testFetchProvider(id),
    onSuccess: (res) => {
      setTestResult(res);
      qc.invalidateQueries({ queryKey: ["fetch-providers"] });
    },
    onError: (err: any) => setTestResult({ ok: false, status: "ERROR", message: err.message || "Test failed" }),
  });

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant={enabled ? "secondary" : "outline"}
          disabled={disabled}
          data-testid="workspace-fetch-button"
          className={`h-8 transition-colors ${enabled ? "border-primary/50 text-foreground font-medium" : "text-muted-foreground"}`}
        >
          <Globe className="mr-1.5 h-3.5 w-3.5" />
          Fetch
          {enabled && (
            <Badge variant="outline" className="ml-1.5 px-1 py-0 text-[10px] bg-primary/10 border-primary/30 text-primary">
              On
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-80 space-y-3 p-3 text-xs"
        align="start"
        data-testid="workspace-fetch-popover"
      >
        <div className="flex items-center justify-between border-b border-border pb-2">
          <div className="flex items-center gap-1.5 font-semibold text-foreground">
            <Globe className="h-4 w-4 text-primary" />
            <span>Web Fetch</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">{enabled ? "Enabled" : "Disabled"}</span>
            <Switch
              data-testid="fetch-enable-switch"
              checked={enabled}
              disabled={notConfigured}
              onCheckedChange={onEnabledChange}
            />
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-muted-foreground">Fetch Provider</span>
            {activeProvider && status && (
              <Badge
                variant={status.connected ? "default" : "outline"}
                className={`text-[9px] px-1 py-0 ${
                  status.connected
                    ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20"
                    : "text-amber-500 border-amber-500/20"
                }`}
                data-testid="fetch-provider-status"
              >
                {status.connected ? "Connected" : "Not configured"}
              </Badge>
            )}
          </div>

          {notConfigured && !isLoading && (
            <p className="text-[11px] text-amber-500" data-testid="fetch-not-configured-note">
              {activeProvider?.name || "Standard Fetch"} is not configured{" "}
              <Link
                to="/tools?tab=providers"
                className="text-primary hover:underline"
                data-testid="fetch-configure-link"
              >
                [Configure Provider]
              </Link>
            </p>
          )}

          {isLoading ? (
            <div className="flex items-center gap-2 py-2 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading providers…
            </div>
          ) : (
            <div className="space-y-1.5">
              {providers.map((p) => {
                const isSelected = (providerId || "prov-fetch-standard") === p.id;
                return (
                  <div
                    key={p.id}
                    data-testid={`fetch-provider-${p.id}`}
                    onClick={() => onProviderChange(p.id)}
                    className={`flex items-center justify-between p-2 rounded border cursor-pointer transition-colors ${
                      isSelected
                        ? "border-primary/50 bg-primary/5"
                        : "border-border hover:bg-accent/40"
                    }`}
                  >
                    <div>
                      <div className="font-medium">{p.name}</div>
                      <div className="text-[10px] text-muted-foreground">
                        {p.id === "prov-fetch-standard" ? "SSRF-guarded HTTP engine" : p.provider_type}
                      </div>
                    </div>
                    {isSelected && <span className="text-[10px] font-semibold text-primary">Active</span>}
                  </div>
                );
              })}
            </div>
          )}

          {activeProvider && (
            <div className="pt-1">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="w-full h-7 text-[11px]"
                data-testid="fetch-test-btn"
                disabled={testMut.isPending}
                onClick={() => {
                  setTestResult(null);
                  testMut.mutate(activeProvider.id);
                }}
              >
                {testMut.isPending ? (
                  <>
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> Testing engine…
                  </>
                ) : (
                  <>
                    <RefreshCw className="mr-1.5 h-3 w-3" /> Test Provider
                  </>
                )}
              </Button>

              {testResult && (
                <div
                  className={`mt-2 flex items-start gap-1.5 p-2 rounded text-[11px] border ${
                    testResult.ok
                      ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                      : "bg-destructive/10 border-destructive/20 text-destructive"
                  }`}
                  data-testid="fetch-test-result"
                >
                  {testResult.ok ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  ) : (
                    <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  )}
                  <div className="min-w-0 flex-1">
                    <span className="font-semibold">{testResult.status || (testResult.ok ? "CONNECTED" : "FAILED")}: </span>
                    <span>{testResult.message}</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground border-t border-border pt-2">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
          <span>Strict SSRF protection, private IP blocking, and multi-hop redirect re-validation active.</span>
        </div>
      </PopoverContent>
    </Popover>
  );
};
