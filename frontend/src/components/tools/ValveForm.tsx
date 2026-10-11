import React, { useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface ValveFieldDef {
  type: "STRING" | "SECRET" | "INTEGER" | "NUMBER" | "BOOLEAN" | "SELECT" | "URL" | "MULTISELECT" | "JSON";
  label: string;
  description?: string;
  required?: boolean;
  default?: any;
  options?: { value: any; label: string }[];
  secret?: boolean;
  scope?: string;
  min?: number | null;
  max?: number | null;
}

export interface ValveFormProps {
  schema: Record<string, ValveFieldDef>;
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
  prefix: string;
  disabled?: boolean;
}

const stringifyJson = (value: any): string => {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const parseJsonLoose = (raw: string): any => {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
};

// Schema-driven renderer for a set of Valve definitions. SECRET fields never
// render stored plaintext: they expose a "Stored" marker and a local-only input
// used to supply a replacement value.
export const ValveForm: React.FC<ValveFormProps> = ({
  schema,
  values,
  onChange,
  prefix,
  disabled,
}) => {
  const [secrets, setSecrets] = useState<Record<string, string>>({});

  if (Object.keys(schema).length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-testid={`${prefix}-valve-empty`}>
        No configurable settings
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid={`${prefix}-valve-form`}>
      {Object.entries(schema).map(([key, def]) => {
        const stored = values?.[key];
        const setSecret = (v: string) => {
          setSecrets((prev) => ({ ...prev, [key]: v }));
          onChange(key, v);
        };
        return (
          <div key={key} className="space-y-1" data-testid={`${prefix}-valve-${key}`}>
            <div className="flex items-center gap-1.5">
              <label className="text-xs font-medium text-muted-foreground">{def.label || key}</label>
              {def.required && (
                <span className="text-[10px] font-medium text-destructive">required</span>
              )}
              {def.type === "SECRET" && stored?.stored === true && (
                <Badge variant="secondary" className="text-[10px]" data-testid={`${prefix}-valve-stored-${key}`}>
                  Stored
                </Badge>
              )}
            </div>
            {def.description && (
              <p className="text-[11px] text-muted-foreground">{def.description}</p>
            )}

            {def.type === "SECRET" && (
              <Input
                type="password"
                autoComplete="new-password"
                className="font-mono text-xs"
                placeholder="Stored — enter new value to replace"
                disabled={disabled}
                value={secrets[key] ?? ""}
                onChange={(e) => setSecret(e.target.value)}
                data-testid={`${prefix}-valve-input-${key}`}
              />
            )}

            {def.type === "BOOLEAN" && (
              <div>
                <Switch
                  checked={!!values?.[key]}
                  disabled={disabled}
                  onCheckedChange={(v) => onChange(key, v)}
                  data-testid={`${prefix}-valve-switch-${key}`}
                />
              </div>
            )}

            {def.type === "SELECT" && (
              <Select
                value={values?.[key] ?? def.default ?? ""}
                disabled={disabled}
                onValueChange={(v) => onChange(key, v)}
              >
                <SelectTrigger data-testid={`${prefix}-valve-select-${key}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(def.options || []).map((o) => (
                    <SelectItem key={String(o.value)} value={String(o.value)}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {def.type === "INTEGER" && (
              <Input
                type="number"
                min={def.min ?? undefined}
                max={def.max ?? undefined}
                disabled={disabled}
                value={values?.[key] ?? ""}
                onChange={(e) => onChange(key, e.target.value === "" ? null : Number(e.target.value))}
                data-testid={`${prefix}-valve-input-${key}`}
              />
            )}

            {def.type === "NUMBER" && (
              <Input
                type="number"
                min={def.min ?? undefined}
                max={def.max ?? undefined}
                disabled={disabled}
                value={values?.[key] ?? ""}
                onChange={(e) => onChange(key, e.target.value === "" ? null : Number(e.target.value))}
                data-testid={`${prefix}-valve-input-${key}`}
              />
            )}

            {(def.type === "STRING" || def.type === "URL") && (
              <Input
                type={def.type === "URL" ? "url" : "text"}
                disabled={disabled}
                value={values?.[key] ?? ""}
                onChange={(e) => onChange(key, e.target.value)}
                data-testid={`${prefix}-valve-input-${key}`}
              />
            )}

            {def.type === "JSON" && (
              <Textarea
                className="font-mono text-xs"
                disabled={disabled}
                value={stringifyJson(values?.[key])}
                onChange={(e) => onChange(key, parseJsonLoose(e.target.value))}
                data-testid={`${prefix}-valve-input-${key}`}
              />
            )}

            {def.type === "MULTISELECT" && (
              <div className="space-y-1">
                {(def.options || []).map((o, index) => {
                  const current = Array.isArray(values?.[key]) ? values[key] : [];
                  const checked = current.some((v: any) => v === o.value);
                  return (
                    <label key={String(o.value)} className="flex cursor-pointer items-center gap-2 text-xs">
                      <Checkbox
                        checked={checked}
                        disabled={disabled}
                        onCheckedChange={(v) =>
                          onChange(
                            key,
                            v === true
                              ? [...current, o.value]
                              : current.filter((x: any) => x !== o.value),
                          )
                        }
                        data-testid={`${prefix}-valve-option-${key}-${index}`}
                      />
                      <span>{o.label}</span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default ValveForm;
