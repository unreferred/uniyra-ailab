import React, { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Upload, Pencil, Trash2, Eye } from "lucide-react";
import {
  api, apiError, fetchAttachmentBlob,
  listProjectFiles, uploadProjectFiles, updateProjectFile, deleteProjectFile, getProjectFile,
} from "@/lib/api";
import { useProjectFiles } from "@/lib/hooks";
import { ProjectFile } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { EmptyState, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { AttachmentViewer } from "@/components/workspace/AttachmentViewer";
import { Attachment } from "@/lib/types";
import { fmtBytes } from "@/components/workspace/AttachmentsBar";

export const FilesTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [tag, setTag] = useState("");
  const [debounced, setDebounced] = useState("");
  const [viewer, setViewer] = useState<Attachment | null>(null);
  const [editing, setEditing] = useState<ProjectFile | null>(null);
  const [editName, setEditName] = useState("");
  const [editTags, setEditTags] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 400);
    return () => clearTimeout(t);
  }, [search]);

  const { data, isLoading, isError, error } = useProjectFiles(projectId, {
    ...(debounced ? { search: debounced } : {}),
    ...(kind ? { kind } : {}),
    ...(tag ? { tag } : {}),
  });
  const files: ProjectFile[] = Array.isArray(data) ? data : data?.items || data?.files || [];

  const invalidate = () => qc.invalidateQueries({ queryKey: ["project-files", projectId] });

  const uploadMut = useMutation({
    mutationFn: async (list: File[]) => uploadProjectFiles(projectId, list),
    onSuccess: (created: any[]) => {
      toast.success(`Uploaded ${created.length} file${created.length === 1 ? "" : "s"}`);
      invalidate();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const saveEdit = useMutation({
    mutationFn: async () =>
      updateProjectFile(projectId, editing!.id, {
        display_name: editName,
        tags: editTags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: () => { toast.success("File updated"); setEditing(null); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const delMut = useMutation({
    mutationFn: async (id: string) => deleteProjectFile(projectId, id),
    onSuccess: () => { toast.success("File deleted"); invalidate(); },
    onError: (e) => toast.error(apiError(e)),
  });

  const openPreview = async (f: ProjectFile) => {
    try {
      const detail = await getProjectFile(projectId, f.id);
      const attachmentId = detail?.attachment_id || f.attachment_id;
      if (!attachmentId) {
        // No attachment backing: show references only.
        toast.message(
          `References: ${(detail?.references?.knowledge_documents || []).length} knowledge doc(s), ${detail?.references?.run_count || 0} run(s)`
        );
        return;
      }
      const url = await fetchAttachmentBlob(attachmentId);
      void url;
      // Reuse AttachmentViewer by synthesizing a minimal Attachment record.
      setViewer({
        id: attachmentId,
        owner_id: f.owner_id,
        project_id: projectId,
        filename: f.display_name,
        original_filename: f.display_name,
        mime_type: f.mime_type || "application/octet-stream",
        kind: (f.kind as any) || "text",
        size_bytes: f.size_bytes || 0,
        sha256: f.sha256 || "",
        processing_status: f.processing_status || "ready",
        extraction_status: "done",
        created_at: f.created_at,
      });
    } catch (e) {
      toast.error(apiError(e));
    }
  };

  const onDropFiles = (list: File[]) => {
    if (list.length) void uploadMut.mutateAsync(list);
  };

  return (
    <div className="space-y-3" data-testid="project-files">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search files…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 w-48"
          data-testid="project-files-search"
        />
        <Input
          placeholder="kind filter (e.g. text)"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="h-8 w-40"
          data-testid="project-files-kind"
        />
        <Input
          placeholder="tag filter"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          className="h-8 w-40"
          data-testid="project-files-tag"
        />
        <div className="ml-auto flex gap-2">
          <Button size="sm" data-testid="project-files-upload" disabled={uploadMut.isPending} onClick={() => fileRef.current?.click()}>
            <Upload className="mr-1.5 h-4 w-4" /> Upload
          </Button>
          <input ref={fileRef} type="file" multiple hidden data-testid="project-files-input" onChange={(e) => { onDropFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
          <Button
            size="sm"
            variant="outline"
            data-testid="project-files-refresh"
            onClick={async () => {
              try {
                await listProjectFiles(projectId);
                invalidate();
              } catch (e) {
                toast.error(apiError(e));
              }
            }}
          >
            Refresh
          </Button>
        </div>
      </div>

      <div
        className={`rounded-lg border p-1 ${dragging ? "border-dashed border-primary bg-accent/30" : "border-transparent"}`}
        onDragEnter={(e) => { e.preventDefault(); setDragging(true); }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
        onDrop={(e) => { e.preventDefault(); setDragging(false); onDropFiles(Array.from(e.dataTransfer?.files || [])); }}
        data-testid="project-files-dropzone"
      >
        {uploadMut.isPending && <p className="px-2 py-2 text-xs text-muted-foreground">Uploading…</p>}
        {isLoading ? (
          <LoadingSpinner label="Loading files…" />
        ) : isError ? (
          <ErrorNote message={apiError(error)} />
        ) : files.length === 0 ? (
          <EmptyState
            icon={<Upload className="h-6 w-6" />}
            title="No files"
            description="Upload project files via the button or drag & drop. Files back knowledge collections."
            testId="project-files-empty"
          />
        ) : (
          <div className="space-y-2">
            {files.map((f) => (
              <Card key={f.id} className="flex items-center justify-between gap-3 p-3" data-testid={`project-file-${f.id}`}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{f.display_name}</span>
                    {f.kind && <Badge variant="outline" className="text-[10px]">{f.kind}</Badge>}
                    {f.size_bytes != null && <span className="text-[10px] text-muted-foreground">{fmtBytes(f.size_bytes)}</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(f.tags || []).map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button size="sm" variant="ghost" data-testid={`preview-file-${f.id}`} onClick={() => openPreview(f)} title="Preview">
                    <Eye className="h-4 w-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`rename-file-${f.id}`}
                    onClick={() => { setEditing(f); setEditName(f.display_name); setEditTags((f.tags || []).join(", ")); }}
                    title="Rename / retag"
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button size="sm" variant="ghost" className="text-destructive" data-testid={`delete-file-${f.id}`} onClick={() => delMut.mutate(f.id)} title="Delete">
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={!!editing} onOpenChange={(o) => { if (!o) setEditing(null); }}>
        <DialogContent data-testid="project-file-edit-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Rename file</DialogTitle>
            <DialogDescription>Updates display name and tags (PATCH file endpoint).</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Display name</label>
              <Input data-testid="project-file-edit-name" value={editName} onChange={(e) => setEditName(e.target.value)} className="mt-1" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Tags (comma-separated)</label>
              <Input data-testid="project-file-edit-tags" value={editTags} onChange={(e) => setEditTags(e.target.value)} className="mt-1" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            <Button data-testid="project-file-edit-save" disabled={!editName.trim() || saveEdit.isPending} onClick={() => saveEdit.mutate()}>
              {saveEdit.isPending ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AttachmentViewer attachment={viewer} open={!!viewer} onOpenChange={(o) => { if (!o) setViewer(null); }} />
    </div>
  );
};
