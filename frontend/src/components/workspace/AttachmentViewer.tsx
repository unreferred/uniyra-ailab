import React, { useEffect, useState } from "react";
import { Loader2, ImageOff } from "lucide-react";
import { Attachment } from "@/lib/types";
import { fetchAttachmentBlob, fetchAttachmentText, apiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { ErrorNote } from "@/components/common/Common";
import { fmtBytes } from "@/components/workspace/AttachmentsBar";

export const AttachmentViewer: React.FC<{
  attachment: Attachment | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}> = ({ attachment, open, onOpenChange }) => {
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [meta, setMeta] = useState<{
    pages?: number | null;
    extraction_status?: string;
    char_count?: number | null;
    available?: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !attachment) {
      setUrl(null);
      setText(null);
      setMeta(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setLoading(true);
    setError(null);
    setUrl(null);
    setText(null);
    setMeta(null);

    const load = async () => {
      try {
        if (attachment.kind === "image") {
          objectUrl = await fetchAttachmentBlob(attachment.id);
          if (cancelled) {
            URL.revokeObjectURL(objectUrl);
            return;
          }
          setUrl(objectUrl);
        } else if (attachment.kind === "text" || attachment.kind === "pdf") {
          const res = await fetchAttachmentText(attachment.id);
          if (cancelled) return;
          setMeta({
            pages: res.pages,
            extraction_status: res.extraction_status,
            char_count: res.char_count,
            available: res.available,
          });
          if (res.available === false) setError("Text extraction is not available for this attachment.");
          else setText(res.text || "");
        }
      } catch (e) {
        if (!cancelled) setError(apiError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [open, attachment?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const filename = attachment ? attachment.original_filename || attachment.filename : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto scrollbar-thin sm:max-w-3xl" data-testid="attachment-viewer">
        <DialogHeader>
          <DialogTitle className="break-all font-display">{filename || "Attachment"}</DialogTitle>
          <DialogDescription className="break-all">{attachment?.mime_type || ""}</DialogDescription>
        </DialogHeader>

        {attachment && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{attachment.kind}</Badge>
              <Badge variant="outline">{fmtBytes(attachment.size_bytes)}</Badge>
              {attachment.extraction_status && <Badge variant="secondary">{attachment.extraction_status}</Badge>}
              {meta?.pages != null && <Badge variant="outline">{meta.pages} pages</Badge>}
              {meta?.char_count != null && <Badge variant="outline">{meta.char_count} chars</Badge>}
            </div>

            {loading && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading attachment…
              </div>
            )}
            {error && <ErrorNote message={error} />}

            {url && attachment.kind === "image" && (
              <div className="flex justify-center rounded-md border border-border bg-muted/30 p-2">
                <img src={url} alt={filename} className="max-h-[60vh] max-w-full object-contain" />
              </div>
            )}

            {text != null && (
              <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-card p-3 text-xs scrollbar-thin">
                {text || "(empty extracted text)"}
              </pre>
            )}

            {!loading && !error && !url && text == null && attachment.kind === "unsupported" && (
              <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
                <ImageOff className="h-5 w-5" />
                Preview is not supported for this file type.
              </div>
            )}

            {attachment.image_metadata && Object.keys(attachment.image_metadata).length > 0 && (
              <div>
                <h4 className="mb-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Image metadata</h4>
                <pre className="max-h-40 overflow-auto rounded bg-muted/50 p-2 text-[10px] scrollbar-thin">
                  {JSON.stringify(attachment.image_metadata, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
