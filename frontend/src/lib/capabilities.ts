// Shared model-capability + workspace tool-gating logic (V4.1 closure).
//
// Mirrors the backend contracts without changing them:
// - `model_capability_status` in backend/app/providers.py reports
//   SUPPORTED / UNSUPPORTED / UNKNOWN and never guesses missing metadata.
// - `_public_tool` in backend/app/services/tools.py reports registry-level
//   `available` / `availability_reason` (disabled flag, cached sandbox
//   snapshot for code_interpreter, executor resolvability). Model gating
//   (MODEL_UNSUPPORTED / MODEL_CAPABILITY_UNKNOWN) and project scoping
//   (DISABLED_BY_PROJECT) are resolved here on the frontend by combining
//   registry availability with the selected model's normalized capabilities.
//
// Sandbox policy is only *read* (via useCodeInterpreterStatus); nothing here
// changes it.

export type CapabilityStatus = "SUPPORTED" | "UNSUPPORTED" | "UNKNOWN";
export type CapabilitySource = "PROVIDER" | "VERIFIED" | "MANUAL" | "UNKNOWN";
export type CapabilityKey =
  | "text"
  | "vision"
  | "tools"
  | "streaming"
  | "structured_output"
  | "embeddings";

export type ToolGate =
  | "AVAILABLE"
  | "UNAVAILABLE"
  | "MODEL_UNSUPPORTED"
  | "MODEL_CAPABILITY_UNKNOWN"
  | "SANDBOX_UNAVAILABLE"
  | "PERMISSION_DENIED"
  | "DISABLED_BY_PROJECT";

export interface ToolGateResult {
  gate: ToolGate;
  reason: string | null;
  selectable: boolean;
}

export const CAPABILITY_SOURCES: CapabilitySource[] = [
  "PROVIDER",
  "VERIFIED",
  "MANUAL",
  "UNKNOWN",
];

export const normalizeCapabilitySource = (value: unknown): CapabilitySource => {
  const text = String(value || "").trim().toUpperCase();
  return (CAPABILITY_SOURCES as string[]).includes(text)
    ? (text as CapabilitySource)
    : "UNKNOWN";
};

/** Built-in workspace tools (registry names; `datetime` is the backend name). */
export const BUILTIN_TOOL_NAMES = [
  "calculator",
  "datetime",
  "date_time",
  "json_transform",
  "code_interpreter",
];

const capabilitiesOf = (model: any): Record<string, any> =>
  (model?.capabilities && typeof model.capabilities === "object"
    ? model.capabilities
    : {}) as Record<string, any>;

const modalitiesOf = (model: any): Record<string, string[]> =>
  (model?.modalities && typeof model.modalities === "object"
    ? model.modalities
    : {}) as Record<string, string[]>;

const includes = (value: unknown, needle: string): boolean =>
  Array.isArray(value) && (value as unknown[]).includes(needle);

/**
 * Normalized capability status for one model + capability key.
 * Unknown/missing metadata reports UNKNOWN — never inferred from names.
 * Only tool/function calling (`tools`) gates workspace tool selection;
 * native provider-side code-interpreter support is never required.
 */
export function modelCapabilityStatus(
  model: any,
  key: CapabilityKey,
): CapabilityStatus {
  const caps = capabilitiesOf(model);
  const modalities = modalitiesOf(model);
  switch (key) {
    case "vision": {
      if (caps.vision === true) return "SUPPORTED";
      const capsModalities =
        (caps.modalities as Record<string, string[]> | undefined) || {};
      if (includes(capsModalities?.input, "image")) return "SUPPORTED";
      if (includes(modalities?.input, "image")) return "SUPPORTED";
      if (caps.vision === false) return "UNSUPPORTED";
      return "UNKNOWN";
    }
    case "tools": {
      if (caps.tools === true) return "SUPPORTED";
      if (includes(caps.supported_parameters, "tools")) return "SUPPORTED";
      if (caps.tools === false) return "UNSUPPORTED";
      return "UNKNOWN";
    }
    case "streaming": {
      if (caps.streaming === true || caps.stream === true) return "SUPPORTED";
      if (caps.streaming === false || caps.stream === false)
        return "UNSUPPORTED";
      return "UNKNOWN";
    }
    case "structured_output": {
      if (caps.structured_output === true) return "SUPPORTED";
      if (caps.structured_output === false) return "UNSUPPORTED";
      return "UNKNOWN";
    }
    case "embeddings": {
      if (caps.embeddings === true || caps.embedding === true)
        return "SUPPORTED";
      if (caps.embeddings === false || caps.embedding === false)
        return "UNSUPPORTED";
      return "UNKNOWN";
    }
    case "text":
    default: {
      if (caps.text === true) return "SUPPORTED";
      if (caps.text === false) return "UNSUPPORTED";
      return "UNKNOWN";
    }
  }
}

/** Convenience: tool/function-calling status of the selected model. */
export function toolCallingStatusOf(model: any): CapabilityStatus {
  if (!model) return "UNKNOWN";
  return modelCapabilityStatus(model, "tools");
}

export const isCodeInterpreterTool = (t: any): boolean =>
  t?.name === "code_interpreter" || t?.tool_type === "code_interpreter";

/** Workspace popover grouping: BUILT-IN vs PROJECT TOOLS (everything else). */
export function workspaceGroupOf(t: any): "BUILT-IN" | "PROJECT TOOLS" {
  const name = String(t?.name || "");
  if (t?.builtin || BUILTIN_TOOL_NAMES.includes(name)) return "BUILT-IN";
  return "PROJECT TOOLS";
}

// Cold-cache / not-yet-checked registry snapshots carry no permission signal:
// a reason that only says the status is unknown (or unavailable) must never
// map to PERMISSION_DENIED.
const UNKNOWN_STATUS_RE =
  /\bunknown\b|not yet checked|never checked|availability pending|status pending/i;

/** True when a registry reason only reports an unknown/pending status. */
export const isUnknownStatusReason = (reason: unknown): boolean =>
  typeof reason === "string" && UNKNOWN_STATUS_RE.test(reason);

export const GATE_LABELS: Record<ToolGate, string> = {
  AVAILABLE: "Available",
  UNAVAILABLE: "Unavailable",
  MODEL_UNSUPPORTED: "Model unsupported",
  MODEL_CAPABILITY_UNKNOWN: "Capability unknown",
  SANDBOX_UNAVAILABLE: "Sandbox unavailable",
  PERMISSION_DENIED: "Permission denied",
  DISABLED_BY_PROJECT: "Disabled by project",
};

export interface GateContext {
  tool: any;
  model?: any | null;
  ciStatus?: { available?: boolean; reason?: string | null } | null;
  ciLoading?: boolean;
  projectId?: string | null;
}

/**
 * Resolve why a workspace tool is selectable or not. Every disabled tool
 * carries an explicit gate + human reason — never a bare grey checkbox.
 *
 * Precedence: project scoping → registry enabled flag → live sandbox state
 * (code interpreter only) → backend availability (permission mapping) →
 * selected-model tool-calling capability.
 */
export function resolveToolGate(ctx: GateContext): ToolGateResult {
  const { tool: t, model, ciStatus, ciLoading, projectId } = ctx;
  const backendReason =
    t?.availability_reason != null ? String(t.availability_reason) : null;

  // 1. Project scoping: a tool pinned to another project is not usable here.
  if (t?.project_id && projectId && t.project_id !== projectId) {
    return {
      gate: "DISABLED_BY_PROJECT",
      reason: backendReason || "Not available to this project",
      selectable: false,
    };
  }

  // 2. Registry enabled flag.
  if (t?.enabled === false) {
    return {
      gate: "UNAVAILABLE",
      reason: backendReason || "Tool is disabled",
      selectable: false,
    };
  }

  // 3. Code interpreter additionally requires a live sandbox. Only generic
  // tool/function calling is required of the model — never native
  // provider-side code-interpreter support. A resolved live ONLINE sandbox
  // wins over a stale registry snapshot below (cold-cache override).
  let liveSandboxOnline = false;
  if (isCodeInterpreterTool(t)) {
    if (ciLoading || ciStatus === undefined) {
      return {
        gate: "SANDBOX_UNAVAILABLE",
        reason: "Checking sandbox status…",
        selectable: false,
      };
    }
    if (!ciStatus || ciStatus.available !== true) {
      return {
        gate: "SANDBOX_UNAVAILABLE",
        reason:
          (ciStatus && ciStatus.reason) ||
          backendReason ||
          "Sandbox unavailable",
        selectable: false,
      };
    }
    liveSandboxOnline = true;
  }

  // 4a. Canonical effective-permission state from the backend resolver.
  // Pre-selection availability reports AVAILABLE_TO_SELECT (or GRANTED for
  // safe builtins) so legitimate tools can be selected in the UI. Explicit
  // DENIED or PERMISSION_DENIED states block selection.
  const permEffective =
    t?.permission_effective != null ? String(t.permission_effective) : null;
  if (permEffective === "DENIED" || permEffective === "PERMISSION_DENIED") {
    const permReason =
      (t?.permission_reason != null ? String(t.permission_reason) : null) ||
      backendReason ||
      "Tool permissions are not granted";
    if (/disabled/i.test(permReason)) {
      return { gate: "UNAVAILABLE", reason: permReason, selectable: false };
    }
    // Explicit permission denial from backend blocks selection
    const required = Array.isArray(t?.required_permissions)
      ? t.required_permissions.join(", ")
      : Array.isArray(t?.permissions)
        ? t.permissions.join(", ")
        : "";
    return {
      gate: "PERMISSION_DENIED",
      reason: required
        ? `Missing permission: ${required}`
        : permReason,
      selectable: false,
    };
  }

  // 4b. Backend-reported unavailability (executor, credentials, permissions).
  // UNKNOWN ("status unknown" / "unavailable") never maps to PERMISSION_DENIED.
  if (t?.available === false) {
    const reason = backendReason || "Unavailable";
    const unknownStatus = isUnknownStatusReason(reason);
    const permissionLike =
      !unknownStatus &&
      /permission|denied|not granted|forbidden|unauthorized/i.test(reason);
    // Cold-cache override: a live ONLINE sandbox wins over a stale registry
    // snapshot (e.g. cold "status unknown"). Only genuine permission denials
    // still block here; everything else falls through to the model gates.
    if (isCodeInterpreterTool(t) && liveSandboxOnline) {
      if (permissionLike) {
        return { gate: "PERMISSION_DENIED", reason, selectable: false };
      }
      // Stale snapshot ignored — continue to permission/model gates below.
    } else {
      if (unknownStatus) {
        if (isCodeInterpreterTool(t)) {
          return { gate: "SANDBOX_UNAVAILABLE", reason, selectable: false };
        }
        return { gate: "UNAVAILABLE", reason, selectable: false };
      }
      if (permissionLike) {
        return { gate: "PERMISSION_DENIED", reason, selectable: false };
      }
      if (/not available to (this|the) project|disabled by project/i.test(reason)) {
        return { gate: "DISABLED_BY_PROJECT", reason, selectable: false };
      }
      if (isCodeInterpreterTool(t)) {
        return { gate: "SANDBOX_UNAVAILABLE", reason, selectable: false };
      }
      return { gate: "UNAVAILABLE", reason, selectable: false };
    }
  }

  // 5. Selected-model tool-calling capability gates every tool.
  if (!model) {
    return { gate: "AVAILABLE", reason: null, selectable: true };
  }
  const toolCap = toolCallingStatusOf(model);
  if (toolCap === "UNSUPPORTED") {
    return {
      gate: "MODEL_UNSUPPORTED",
      reason: "Model does not support tool/function calling",
      selectable: false,
    };
  }
  if (toolCap === "UNKNOWN") {
    return {
      gate: "MODEL_CAPABILITY_UNKNOWN",
      reason: "Tool calling capability unknown",
      selectable: false,
    };
  }
  return { gate: "AVAILABLE", reason: null, selectable: true };
}

export const isToolSelectable = (r: ToolGateResult): boolean => r.selectable;
