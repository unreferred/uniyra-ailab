import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, Download, Loader2, Play, ShieldAlert, XCircle } from "lucide-react";
import {
  apiError,
  codeInterpreterStatus,
  downloadExecutionFile,
  executeCode,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNote, LoadingSpinner } from "@/components/common/Common";

// NOTE (registry-level scope): message-level rendering of generated files is
// already handled by the Workspace attachments UI — code tool_calls surface
// their `files` manifests as attachments, which the attachments bar/viewer
// render inline. This panel is a standalone, prop-driven component with no
// Workspace coupling; it is exported for future wiring (e.g. a docked panel
// beside the messages area). Only whitelisted, secret-free fields from the
// status/execute payloads are ever rendered — credential values are never
// part of these contracts and are never displayed here.

export interface CodeInterpreterAttachment {
  id: string;
  filename?: string | null;
  original_filename?: string | null;
}

export interface CodeInterpreterPanelProps {
  projectId: string;
  attachments?: CodeInterpreterAttachment[];
  selectedInputFileIds?: string[];
  onSelectedInputFileIdsChange?: (ids: string[]) => void;
  initialCode?: string;
  onResult?: (result: any) => void;
}

const fileNameOf = (f: any): string => {
  if (typeof f === "string") return f;
  return String(f?.filename || f?.name || f?.path || "output");
};

const fileTestId = (name: string) =>
  `code-interpreter-file-${name.replace(/[^a-zA-Z0-9._-]+/g, "-")}`;

const resultVariant = (status?: string) => {
  if (status === "success") return "secondary";
  if (status === "timeout" || status === "error") return "destructive";
  return "outline";
};

export const CodeInterpreterPanel: React.FC<CodeInterpreterPanelProps> = ({
  projectId,
  attachments = [],
  selectedInputFileIds,
  onSelectedInputFileIdsChange,
  initialCode = "",
  onResult,
}) => {
  const [code, setCode] = useState(initialCode);
  const [internalSelected, setInternalSelected] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  const selected = selectedInputFileIds ?? internalSelected;
  const setSelected = (ids: string[]) => {
    setInternalSelected(ids);
    onSelectedInputFileIdsChange?.(ids);
  };

  const statusQuery = useQuery({
    queryKey: ["code-interpreter-status"],
    queryFn: codeInterpreterStatus,
    retry: false,
  });
  const status = statusQuery.data;
  const available = status?.available === true;

  const runMut = useMutation({
    mutationFn: async () =>
      executeCode({ project_id: projectId, code, input_file_ids: selected }),
    onSuccess: (r) => {
      setResult(r);
      onResult?.(r);
      if (r?.status === "success") toast.success("Code executed");
      else toast.message(`Execution ${r?.status || "finished"}`);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const toggleFile = (id: string) =>
    setSelected(
      selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]
    );

  const disabledReason: string | null = statusQuery.isLoading
    ? "Checking sandbox status…"
    : !available
      ? status?.reason || "Code interpreter is unavailable"
      : !code.trim()
        ? "Enter Python code to run"
        : null;

  const handleDownload = async (filename: string) => {
    if (!result?.execution_id) return;
    setDownloading(filename);
    try {
      const url = await downloadExecutionFile(result.execution_id, filename);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename.split("/").pop() || filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (e) {
      toast.error(apiError(e));
    } finally {
      setDownloading(null);
    }
  };

  const files: string[] = Array.isArray(result?.files)
    ? result.files.map(fileNameOf)
    : [];
  const execError = result?.error || null;
  const limits = status?.limits || {};
  const packages: string[] = status?.policy?.packages || [];
  const network: string | undefined = status?.policy?.network;
  const timeoutS = limits?.timeout_s;

  return (
    <div className="space-y-4" data-testid="code-interpreter-panel">
      <Card className="p-4" data-testid="code-interpreter-status-card">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="font-display text-sm font-semibold">Sandbox</h3>
          {statusQuery.isLoading ? (
            <LoadingSpinner label="Checking status…" />
          ) : available ? (
            <Badge variant="secondary" className="text-[10px]" data-testid="code-interpreter-available">
              <CheckCircle2 className="mr-1 h-3 w-3" /> available
            </Badge>
          ) : (
            <Badge variant="destructive" className="text-[10px]" data-testid="code-interpreter-unavailable">
              <XCircle className="mr-1 h-3 w-3" /> unavailable
            </Badge>
          )}
          {network != null && (
            <Badge
              variant="outline"
              className="font-mono text-[10px]"
              data-testid="code-interpreter-network"
            >
              network {String(network).toUpperCase()}
            </Badge>
          )}
        </div>
        {statusQuery.isError ? (
          <ErrorNote message={apiError(statusQuery.error)} />
        ) : status ? (
          <div className="space-y-2 text-xs">
            {!available && (
              <div className="space-y-1">
                {status.reason && (
                  <p className="text-destructive" data-testid="code-interpreter-unavailable-reason">
                    {status.reason}
                  </p>
                )}
                <p className="text-muted-foreground" data-testid="code-interpreter-unavailable-hint">
                  <ShieldAlert className="mr-1 inline h-3 w-3" />
                  Ensure the Docker daemon is running and the sandbox image
                  {status.image ? ` "${status.image}" is present (docker image inspect ${status.image})` : " is present"}.
                  Build or pull the image, then retry.
                </p>
              </div>
            )}
            <div data-testid="code-interpreter-limits">
              <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Limits</span>
              <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-[11px] text-muted-foreground sm:grid-cols-3">
                {Object.entries(limits).length === 0 ? (
                  <span>Not reported</span>
                ) : (
                  Object.entries(limits).map(([k, v]) => (
                    <span key={k}>
                      {k}: {String(v)}
                    </span>
                  ))
                )}
              </div>
            </div>
            <div data-testid="code-interpreter-packages">
              <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Packages</span>
              <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                {packages.length > 0 ? packages.join(", ") : "Not reported"}
              </p>
            </div>
          </div>
        ) : null}
      </Card>

      {attachments.length > 0 && (
        <div data-testid="code-interpreter-inputs">
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            Input files (staged read-only under /workspace/input)
          </span>
          <div className="mt-1 space-y-1">
            {attachments.map((a) => (
              <label
                key={a.id}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50"
                data-testid={`code-interpreter-input-${a.id}`}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(a.id)}
                  onChange={() => toggleFile(a.id)}
                  className="h-3.5 w-3.5"
                />
                <span className="truncate font-mono">{a.filename || a.original_filename || a.id}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div>
        <label className="text-xs font-medium text-muted-foreground">Python code</label>
        <Textarea
          data-testid="code-interpreter-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          rows={8}
          className="mt-1 font-mono text-xs"
          placeholder='print("hello from sandbox")'
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          data-testid="code-interpreter-run"
          disabled={disabledReason != null || runMut.isPending}
          title={disabledReason || undefined}
          onClick={() => runMut.mutate()}
        >
          {runMut.isPending ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
          ) : (
            <Play className="mr-1.5 h-4 w-4" />
          )}
          {runMut.isPending ? "Running…" : "Run code"}
        </Button>
        {disabledReason && (
          <span className="text-[11px] text-muted-foreground" data-testid="code-interpreter-run-reason">
            {disabledReason}
          </span>
        )}
      </div>
      {runMut.isError && <ErrorNote message={apiError(runMut.error)} />}

      {result != null && (
        <Card className="space-y-2 p-4" data-testid="code-interpreter-result">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant={resultVariant(result.status)} className="text-[10px]" data-testid="code-interpreter-result-status">
              {result.status || "unknown"}
            </Badge>
            {result.exit_code != null && (
              <span className="font-mono text-muted-foreground">exit {result.exit_code}</span>
            )}
            {result.duration_ms != null && (
              <span className="font-mono text-muted-foreground">{result.duration_ms} ms</span>
            )}
            {result.execution_id && (
              <span className="font-mono text-[10px] text-muted-foreground" title={result.execution_id}>
                {String(result.execution_id).slice(0, 8)}
              </span>
            )}
          </div>
          {result.status === "timeout" && (
            <p className="text-xs text-amber-600" data-testid="code-interpreter-timeout-note">
              Execution exceeded the {timeoutS != null ? `${timeoutS}s` : "configured"} time limit and was stopped.
            </p>
          )}
          {execError && (
            <p className="text-xs text-destructive" data-testid="code-interpreter-error">
              {execError.category ? `${execError.category}: ` : ""}{execError.message || "Execution failed"}
            </p>
          )}
          <div>
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">stdout</span>
            <pre className="mt-0.5 max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] scrollbar-thin" data-testid="code-interpreter-stdout">
              {result.stdout || "(no output)"}
            </pre>
          </div>
          <div>
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">stderr</span>
            <pre className="mt-0.5 max-h-48 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] scrollbar-thin" data-testid="code-interpreter-stderr">
              {result.stderr || "(empty)"}
            </pre>
          </div>
          {files.length > 0 && (
            <div data-testid="code-interpreter-files">
              <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Generated files ({files.length})
              </span>
              <ul className="mt-1 space-y-1">
                {files.map((name) => (
                  <li
                    key={name}
                    className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
                    data-testid={fileTestId(name)}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono">{name}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid={`${fileTestId(name)}-download`}
                      disabled={!result.execution_id || downloading === name}
                      onClick={() => handleDownload(name)}
                    >
                      {downloading === name ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Download className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}
    </div>
  );
};

export default CodeInterpreterPanel;
