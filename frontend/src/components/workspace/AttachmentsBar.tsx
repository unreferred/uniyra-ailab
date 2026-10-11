import React from "react";
import { Image as ImageIcon, FileText, FileX, Loader2, X, AlertTriangle } from "lucide-react";
import { Attachment } from "@/lib/types";
import { cn } from "@/lib/utils";

export function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export const AttachmentKindIcon: React.FC<{ kind?: string | null; className?: string }> = ({ kind, className }) => {
  if (kind === "image") return <ImageIcon className={className} />;
  if (kind === "pdf" || kind === "text") return <FileText className={className} />;
  return <FileX className={className} />;
};

export function attachmentError(a: Attachment): string | null {
  if (a.processing_status === "error" || a.processing_status === "failed" || a.extraction_status === "failed") {
    const meta = a.metadata || {};
    return typeof meta.error === "string" ? meta.error : "Processing failed";
  }
  return null;
}

export const AttachmentsBar: React.FC<{
  attachments: Attachment[];
  onRemove?: (id: string) => void;
  onOpen?: (a: Attachment) => void;
  uploading?: boolean;
  className?: string;
}> = ({ attachments, onRemove, onOpen, uploading, className }) => {
  if (attachments.length === 0 && !uploading) return null;
  return (
    <div className={cn("flex flex-wrap gap-2", className)} data-testid="attachments-bar">
      {attachments.map((a) => {
        const err = attachmentError(a);
        const processing = !!a.processing_status && a.processing_status !== "ready" && !err;
        return (
          <div
            key={a.id}
            role="button"
            tabIndex={0}
            data-testid={`attachment-chip-${a.id}`}
            onClick={() => onOpen?.(a)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen?.(a);
              }
            }}
            className={cn(
              "group flex max-w-[260px] items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-xs",
              err ? "border-destructive/50" : "border-border",
              onOpen && "cursor-pointer hover:bg-accent/50"
            )}
            title={a.original_filename || a.filename}
          >
            <AttachmentKindIcon kind={a.kind} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{a.original_filename || a.filename}</span>
              <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span>{fmtBytes(a.size_bytes)}</span>
                {processing && (
                  <>
                    <Loader2 className="h-2.5 w-2.5 animate-spin" />
                    <span>{a.processing_status}</span>
                  </>
                )}
                {err && (
                  <span className="flex items-center gap-0.5 text-destructive">
                    <AlertTriangle className="h-2.5 w-2.5" />
                    {err}
                  </span>
                )}
              </span>
            </span>
            {onRemove && (
              <button
                type="button"
                title="Remove"
                data-testid={`attachment-remove-${a.id}`}
                className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(a.id);
                }}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        );
      })}
      {uploading && (
        <div className="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Uploading…
        </div>
      )}
    </div>
  );
};
