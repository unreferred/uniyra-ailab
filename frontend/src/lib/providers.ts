// Shared search/fetch provider helpers (OMNILAB V4.2.3.1).
// Defensive against missing backend fields: every accessor tolerates
// undefined/null and falls back to a safe default.

export type ProviderStatusBadge = "NOT_CONFIGURED" | "CONNECTED" | "ERROR";

export interface ProviderStatusInfo {
  badge: ProviderStatusBadge;
  connected: boolean;
  label: string;
}

const HEALTH_OK = new Set(["CONNECTED", "CONFIGURED", "OK", "HEALTHY", "AVAILABLE"]);
const HEALTH_ERR = new Set([
  "ERROR",
  "FAILED",
  "INVALID",
  "DISCONNECTED",
  "UNAVAILABLE",
  "AUTH_FAILED",
]);

const CONFIGURED_CREDENTIALS = new Set([
  "CONFIGURED",
  "NOT_REQUIRED",
  "NONE_REQUIRED",
  "CONFIGURED_ENV",
]);

export const providerItems = (data: any): any[] => {
  if (Array.isArray(data)) return data;
  if (Array.isArray((data as any)?.items)) return (data as any).items;
  return [];
};

const norm = (v: unknown): string => String(v ?? "").trim().toUpperCase();

export function providerStatusOf(p: any, lastTest?: any): ProviderStatusInfo {
  const credential = norm(p?.credential_status);
  const health = norm(p?.health_status);
  const test = lastTest ?? (p as any)?.last_test ?? null;
  const testOk: boolean | undefined =
    test && typeof test === "object" && "ok" in test ? !!(test as any).ok : undefined;

  if (testOk === false || HEALTH_ERR.has(health) || credential === "INVALID") {
    return { badge: "ERROR", connected: false, label: "Error" };
  }
  if (testOk === true || HEALTH_OK.has(health)) {
    return { badge: "CONNECTED", connected: true, label: "Connected" };
  }
  const hasVault = !!(p?.has_vault_credential || (p as any)?.credential_id);
  const configured = CONFIGURED_CREDENTIALS.has(credential) || hasVault;
  if (configured) {
    return { badge: "CONNECTED", connected: true, label: "Connected" };
  }
  if (!credential || credential === "NOT_CONFIGURED" || credential === "UNKNOWN") {
    return { badge: "NOT_CONFIGURED", connected: false, label: "Not configured" };
  }
  return { badge: "NOT_CONFIGURED", connected: false, label: "Not configured" };
}

export function providerKeySourceOf(p: any): string {
  const explicit = p?.key_source ?? p?.configuration?.key_source;
  if (typeof explicit === "string" && explicit.trim()) {
    const t = explicit.trim().toLowerCase();
    if (t === "vault") return "Vault";
    if (t === "direct") return "Direct";
    if (t === "env") return "Env";
    if (t === "none") return "None";
    return explicit.trim();
  }
  if (p?.credential_id || p?.has_vault_credential) return "Vault";
  const cfg = p?.configuration && typeof p.configuration === "object" ? p.configuration : {};
  if (cfg.api_key_env || cfg.from_env || cfg.env_var) return "Env";
  if (norm(p?.credential_status) === "CONFIGURED") return "Direct";
  return "None";
}

export const isProviderConfigured = (p: any): boolean =>
  providerStatusOf(p).connected;
