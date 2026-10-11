import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Database, Trash2, RefreshCw, FileText, ChevronDown, ChevronRight, Layers, Search } from "lucide-react";
import { api, apiError, createKnowledgeCollection, deleteKnowledgeCollection, attachKnowledgeDocuments, indexKnowledgeCollection, reindexKnowledgeDocument, detachKnowledgeDocument, searchKnowledge } from "@/lib/api";
import { useKnowledgeCollections, useKnowledgeCollection, useKnowledgeDocuments, useKnowledgeChunks, useProjectFiles } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { EmptyState, LoadingSpinner, ErrorNote } from "@/components/common/Common";
import { KnowledgeCollection } from "@/lib/types";

const ingestionStatus = (document: any): string => String(document?.ingestion_status || document?.status || document?.processing_status || "pending").toUpperCase();

const documentError = (document: any): string | null => {
  const raw = document?.error_message || document?.error;
  const message = raw && typeof raw === "object" ? raw.message || raw.detail || raw.code : raw;
  if (!message && !document?.error_code) return null;
  return [document?.error_code, message].filter(Boolean).join(": ") || null;
};

const statusVariant = (status: string): "secondary" | "outline" | "destructive" =>
  status === "FAILED" || status === "UNAVAILABLE" ? "destructive" : status === "READY" ? "secondary" : "outline";

const configLabel = (value: any): string => {
  if (!value || typeof value !== "object") return "Unknown";
  return [value.name, value.version].filter(Boolean).join(" ") || "Configured";
};

const CollectionDetail: React.FC<{ collection: KnowledgeCollection; projectId: string; onDelete: (id: string) => void; deleting: boolean }> = ({ collection, projectId, onDelete, deleting }) => {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [showChunks, setShowChunks] = useState(false);
  const [chunkDocId, setChunkDocId] = useState<string | null>(null);

  const docsQuery = useKnowledgeDocuments(expanded ? collection.id : null);
  const chunksQuery = useKnowledgeChunks(showChunks ? collection.id : null, chunkDocId);
  const filesQuery = useProjectFiles(attachOpen ? projectId : null);
  const detailQuery = useKnowledgeCollection(expanded ? collection.id : null);
  const detail: any = (detailQuery.data as any) || collection;
  const embeddingConfig = detail?.embedding_config ?? collection.embedding_config ?? null;
  const chunkingConfig = detail?.chunking_config ?? detail?.chunk_config ?? (collection as any).chunking_config ?? null;

  const docs: any[] = Array.isArray(docsQuery.data) ? docsQuery.data : docsQuery.data?.items || docsQuery.data?.documents || [];
  const chunks: any[] = Array.isArray(chunksQuery.data) ? chunksQuery.data : chunksQuery.data?.items || chunksQuery.data?.chunks || [];
  const files: any[] = Array.isArray(filesQuery.data) ? filesQuery.data : filesQuery.data?.items || filesQuery.data?.files || [];
  const indexErrors: any[] = docs.filter((d: any) => ["FAILED", "UNAVAILABLE"].includes(ingestionStatus(d)) && documentError(d));

  const attachMut = useMutation({
    mutationFn: async () => attachKnowledgeDocuments(collection.id, selectedFiles),
    onSuccess: (created: any) => {
      const n = Array.isArray(created) ? created.length : created?.documents?.length || 0;
      toast.success("Attached " + n + " document" + (n === 1 ? "" : "s"));
      setAttachOpen(false);
      setSelectedFiles([]);
      qc.invalidateQueries({ queryKey: ["knowledge-collection", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-documents", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-chunks", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-collections", projectId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const indexMut = useMutation({
    mutationFn: async () => indexKnowledgeCollection(collection.id),
    onSuccess: (summary: any) => {
      toast.message("Index finished: " + Number(summary?.ready ?? 0) + " ready, " + Number(summary?.failed ?? 0) + " failed");
      qc.invalidateQueries({ queryKey: ["knowledge-collection", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-documents", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-chunks", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-collections", projectId] });
      void docsQuery.refetch();
      void detailQuery.refetch();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const lifecycleMut = useMutation({
    mutationFn: async (vars: { documentId: string; action: "reindex" | "detach" }) =>
      vars.action === "detach" ? detachKnowledgeDocument(collection.id, vars.documentId) : reindexKnowledgeDocument(collection.id, vars.documentId),
    onSuccess: (result: any, vars) => {
      if (vars.action === "detach") toast.message("Document detached; searchable chunks removed");
      else toast.message("Reindex finished: " + Number(result?.ready ?? 0) + " ready, " + Number(result?.failed ?? 0) + " failed");
      qc.invalidateQueries({ queryKey: ["knowledge-documents", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-collection", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-chunks", collection.id] });
      qc.invalidateQueries({ queryKey: ["knowledge-collections", projectId] });
      void docsQuery.refetch();
      void detailQuery.refetch();
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const toggleFile = (id: string) =>
    setSelectedFiles((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <Card className="p-3" data-testid={`knowledge-collection-${collection.id}`}>
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid={`knowledge-expand-${collection.id}`}
          onClick={() => setExpanded((v) => !v)}
          className="text-muted-foreground hover:text-foreground"
          title={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{collection.name}</span>
            {collection.status && <Badge variant="outline" className="text-[10px]">{String(collection.status).toUpperCase()}</Badge>}
            {(collection.doc_count ?? collection.document_count) != null && (
              <Badge variant="secondary" className="text-[10px]">{collection.doc_count ?? collection.document_count} docs</Badge>
            )}
            {collection.chunk_count != null && (
              <Badge variant="secondary" className="text-[10px]">{collection.chunk_count} chunks</Badge>
            )}
          </div>
          {collection.description && <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{collection.description}</p>}
        </div>
        <Button size="sm" variant="outline" data-testid={`knowledge-attach-${collection.id}`} onClick={() => setAttachOpen(true)}>
          <FileText className="mr-1 h-3.5 w-3.5" /> Attach
        </Button>
        <Button size="sm" variant="outline" data-testid={`knowledge-index-${collection.id}`} disabled={indexMut.isPending} onClick={() => indexMut.mutate()}>
          {indexMut.isPending ? "Indexing…" : (<><RefreshCw className="mr-1 h-3.5 w-3.5" /> Index</>)}
        </Button>
        <button
          type="button"
          data-testid={`knowledge-delete-${collection.id}`}
          title="Delete collection"
          disabled={deleting}
          className="shrink-0 text-muted-foreground hover:text-destructive disabled:opacity-40"
          onClick={() => onDelete(collection.id)}
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-border pt-3" data-testid={`knowledge-detail-${collection.id}`}>
          {docsQuery.isLoading ? (
            <LoadingSpinner label="Loading documents…" />
          ) : docsQuery.isError ? (
            <ErrorNote message={apiError(docsQuery.error)} />
          ) : docs.length === 0 ? (
            <p className="text-xs text-muted-foreground">No documents. Attach project files, then Index.</p>
          ) : (
            docs.map((d: any) => { const status = ingestionStatus(d); const error = documentError(d); const parser = configLabel(d.parser); const index = d.index?.status || configLabel(d.index); const actionLabel = status === "FAILED" || status === "UNAVAILABLE" ? "Retry" : "Reindex"; return (
              <div key={d.id} className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs" data-testid={`knowledge-doc-${d.id}`}>
                <div className="min-w-0">
                  <span className="block truncate font-medium">{d.display_name || d.project_file_id || d.id}</span>
                  <span className="text-[10px] text-muted-foreground">
                    <Badge data-testid={"knowledge-doc-status-" + d.id} variant={statusVariant(status)} className="text-[9px]">{status}</Badge> <span>{d.chunk_count ?? 0} chunks · parser {parser} · index {index}</span>
                  </span>
                  {error && <span className="block text-[10px] text-destructive" data-testid={"knowledge-doc-error-" + d.id}>{error}</span>}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2 text-[11px]"
                  data-testid={`knowledge-doc-chunks-${d.id}`}
                  onClick={() => { setChunkDocId(d.id); setShowChunks(true); }}
                >
                  Chunks
                </Button>
                <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]" data-testid={"knowledge-doc-reindex-" + d.id} disabled={lifecycleMut.isPending || status === "PROCESSING" || status === "DETACHED"} onClick={() => lifecycleMut.mutate({ documentId: d.id, action: "reindex" })}>{actionLabel}</Button>
                <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px] text-muted-foreground hover:text-destructive" data-testid={"knowledge-doc-detach-" + d.id} disabled={lifecycleMut.isPending || status === "DETACHED"} onClick={() => lifecycleMut.mutate({ documentId: d.id, action: "detach" })}>Remove</Button>
              </div>
            ); })
          )}

          <div className="space-y-1.5 rounded-md bg-muted/40 p-2 text-[11px]" data-testid={`knowledge-config-${collection.id}`}>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Indexing config</p>
            <pre className="whitespace-pre-wrap break-words font-mono text-[10px] text-muted-foreground" data-testid={`knowledge-embedding-config-${collection.id}`}>
              Embedding: {embeddingConfig ? JSON.stringify(embeddingConfig) : "Unknown"}
            </pre>
            <pre className="whitespace-pre-wrap break-words font-mono text-[10px] text-muted-foreground" data-testid={`knowledge-chunking-config-${collection.id}`}>
              Chunking: {chunkingConfig ? JSON.stringify(chunkingConfig) : "Unknown"}
            </pre>
            {indexErrors.length > 0 && (
              <div className="space-y-1" data-testid={`knowledge-indexing-errors-${collection.id}`}>
                {indexErrors.map((d: any) => (
                  <p key={d.id} className="text-[10px] text-destructive">
                    {d.display_name || d.project_file_id || d.id}: {documentError(d)}
                  </p>
                ))}
              </div>
            )}
          </div>

          <div>
            <button
              type="button"
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
              data-testid={`knowledge-chunks-toggle-${collection.id}`}
              onClick={() => setShowChunks((v) => !v)}
            >
              <Layers className="h-3 w-3" /> {showChunks ? "Hide chunks" : "Show chunks"}
            </button>
            {showChunks && (
              <div className="mt-2 space-y-1.5" data-testid={`knowledge-chunks-${collection.id}`}>
                {chunksQuery.isLoading ? (
                  <LoadingSpinner label="Loading chunks…" />
                ) : chunksQuery.isError ? (
                  <ErrorNote message={apiError(chunksQuery.error)} />
                ) : chunks.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground">No chunks yet — run Index first.</p>
                ) : (
                  chunks.slice(0, 20).map((c: any) => (
                    <div key={c.id} className="rounded-md bg-muted/40 p-2 text-[11px]" data-testid={`knowledge-chunk-${c.id}`}>
                      <p className="line-clamp-3 whitespace-pre-wrap">{c.content_preview || c.content || "(empty)"}</p>
                      <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                        {[c.chunk_index != null ? `#${c.chunk_index}` : null, c.token_estimate != null ? `${c.token_estimate} tok` : null].filter(Boolean).join(" · ")}
                      </p>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <Dialog open={attachOpen} onOpenChange={setAttachOpen}>
        <DialogContent data-testid="knowledge-attach-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">Attach project files</DialogTitle>
            <DialogDescription>POST /knowledge/{collection.id}/documents with project_file_ids.</DialogDescription>
          </DialogHeader>
          {filesQuery.isLoading ? (
            <LoadingSpinner label="Loading files…" />
          ) : filesQuery.isError ? (
            <ErrorNote message={apiError(filesQuery.error)} />
          ) : files.length === 0 ? (
            <p className="text-xs text-muted-foreground">No project files to attach. Upload files in the Files tab first.</p>
          ) : (
            <div className="max-h-64 space-y-1 overflow-y-auto scrollbar-thin">
              {files.map((f: any) => (
                <label key={f.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-accent/50" data-testid={`attach-file-${f.id}`}>
                  <Checkbox checked={selectedFiles.includes(f.id)} onCheckedChange={() => toggleFile(f.id)} />
                  <span className="truncate">{f.display_name}</span>
                </label>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAttachOpen(false)}>Cancel</Button>
            <Button data-testid="knowledge-attach-submit" disabled={selectedFiles.length === 0 || attachMut.isPending} onClick={() => attachMut.mutate()}>
              {attachMut.isPending ? "Attaching…" : `Attach ${selectedFiles.length}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
};


const RetrievalResultRow: React.FC<{ result: any; index: number }> = ({ result: r, index }) => {
  const chunk = r.chunk && typeof r.chunk === "object" ? r.chunk : null;
  const score = r.score ?? r.chunk_score ?? chunk?.score ?? null;
  const collectionName = r.collection_name ?? chunk?.collection_name ?? "";
  const collectionId = r.collection_id ?? chunk?.collection_id ?? "";
  const doc = r.document && typeof r.document === "object" ? r.document : null;
  const documentName =
    doc?.display_name || r.document_name || doc?.project_file_id || r.document_id || chunk?.document_id || "";
  const content = r.content ?? chunk?.content ?? chunk?.content_preview ?? "";
  const preview =
    typeof content === "string" && content.length > 500 ? `${content.slice(0, 500)}\u2026` : content || "(empty)";
  const source = r.source && typeof r.source === "object" ? r.source : {};
  const sourceFile = source.display_name || source.project_file_id || source.attachment_id || "";
  const chunkIndex = r.chunk_index ?? chunk?.chunk_index ?? null;
  return (
    <div className="rounded-md border border-border/60 p-2 text-xs" data-testid={`retrieval-result-${index}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="font-mono text-[10px]" data-testid={`retrieval-score-${index}`}>
          {score != null && Number.isFinite(Number(score)) ? Number(score).toFixed(4) : "n/a"}
        </Badge>
        <span className="font-medium">{collectionName || collectionId || "Unknown collection"}</span>
        {documentName && <span className="text-muted-foreground">{documentName}</span>}
        {chunkIndex != null && <span className="font-mono text-[10px] text-muted-foreground">#{String(chunkIndex)}</span>}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-[11px] text-muted-foreground">{preview}</p>
      {sourceFile && <p className="mt-1 font-mono text-[10px] text-muted-foreground">Source file: {sourceFile}</p>}
    </div>
  );
};

const RetrievalConsole: React.FC<{ projectId: string; collections: KnowledgeCollection[] }> = ({ projectId, collections }) => {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState("5");
  const [results, setResults] = useState<any[] | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  const searchMut = useMutation({
    mutationFn: async () => {
      const k = Math.max(1, Math.min(50, parseInt(topK, 10) || 5));
      return searchKnowledge({
        query: query.trim(),
        top_k: k,
        project_id: projectId,
        collection_ids: collections.map((c) => c.id),
      });
    },
    onSuccess: (data: any) => {
      const items = Array.isArray(data?.results) ? data.results : Array.isArray(data?.items) ? data.items : [];
      setResults(items);
      setLatencyMs(data?.latency_ms ?? null);
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <Card className="p-3" data-testid="knowledge-retrieval-console">
      <div className="flex items-center gap-2">
        <Search className="h-4 w-4 text-muted-foreground" />
        <h4 className="text-sm font-medium">Retrieval test</h4>
        <span className="font-mono text-[10px] text-muted-foreground">POST /knowledge/search</span>
        {latencyMs != null && <span className="ml-auto font-mono text-[10px] text-muted-foreground">{latencyMs} ms</span>}
      </div>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <Input
          data-testid="knowledge-search-query"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Test a retrieval query\u2026"
          className="flex-1"
        />
        <div className="flex gap-2">
          <Input
            data-testid="knowledge-search-topk"
            value={topK}
            onChange={(e) => setTopK(e.target.value)}
            inputMode="numeric"
            aria-label="Top K"
            className="w-20"
          />
          <Button
            size="sm"
            data-testid="knowledge-search-run"
            disabled={!query.trim() || collections.length === 0 || searchMut.isPending}
            onClick={() => searchMut.mutate()}
          >
            {searchMut.isPending ? "Searching\u2026" : "Search"}
          </Button>
        </div>
      </div>
      {collections.length === 0 && (
        <p className="mt-2 text-[11px] text-muted-foreground">Create a collection, attach files and index it before testing retrieval.</p>
      )}
      {searchMut.isError && <div className="mt-2"><ErrorNote message={apiError(searchMut.error)} /></div>}
      {searchMut.isPending && <div className="mt-2"><LoadingSpinner label="Searching\u2026" /></div>}
      {results !== null && !searchMut.isPending && (
        <div className="mt-2 space-y-1.5">
          {results.length === 0 ? (
            <p className="text-[11px] text-muted-foreground" data-testid="retrieval-empty">No chunks retrieved for this query.</p>
          ) : (
            results.map((r: any, i: number) => (
              <RetrievalResultRow key={r.chunk_id || r.chunk?.id || `${r.collection_id}-${i}`} result={r} index={i} />
            ))
          )}
        </div>
      )}
    </Card>
  );
};

export const KnowledgeTab: React.FC<{ projectId: string }> = ({ projectId }) => {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const { data, isLoading, isError, error } = useKnowledgeCollections(projectId);
  const collections: KnowledgeCollection[] = Array.isArray(data) ? data : data?.items || data?.collections || [];

  const createMut = useMutation({
    mutationFn: async () => createKnowledgeCollection({ project_id: projectId, name, description }),
    onSuccess: () => {
      toast.success("Collection created");
      setOpen(false);
      setName("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["knowledge-collections", projectId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  const delMut = useMutation({
    mutationFn: async (id: string) => deleteKnowledgeCollection(id),
    onSuccess: () => {
      toast.success("Collection deleted");
      qc.invalidateQueries({ queryKey: ["knowledge-collections", projectId] });
    },
    onError: (e) => toast.error(apiError(e)),
  });

  return (
    <div className="space-y-3" data-testid="project-knowledge">
      <div className="flex justify-end">
        <Button size="sm" data-testid="knowledge-create" onClick={() => setOpen(true)}>
          <Plus className="mr-1.5 h-4 w-4" /> New collection
        </Button>
      </div>
      {isLoading ? (
        <LoadingSpinner label="Loading collections…" />
      ) : isError ? (
        <ErrorNote message={apiError(error)} />
      ) : collections.length === 0 ? (
        <EmptyState
          icon={<Database className="h-6 w-6" />}
          title="No knowledge collections"
          description="Create a collection, attach project files, then index it for retrieval."
          testId="project-knowledge-empty"
        />
      ) : (
        <div className="space-y-2">
          {collections.map((c) => (
            <CollectionDetail
              key={c.id}
              collection={c}
              projectId={projectId}
              onDelete={(id) => delMut.mutate(id)}
              deleting={delMut.isPending}
            />
          ))}
        </div>
      )}

      <RetrievalConsole projectId={projectId} collections={collections} />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="knowledge-create-dialog">
          <DialogHeader>
            <DialogTitle className="font-display">New knowledge collection</DialogTitle>
            <DialogDescription>POST /knowledge with this project_id.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Name</label>
              <Input data-testid="knowledge-name-input" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" placeholder="e.g. Product docs" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Description</label>
              <Textarea data-testid="knowledge-desc-input" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button data-testid="knowledge-create-submit" disabled={!name.trim() || createMut.isPending} onClick={() => createMut.mutate()}>
              {createMut.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};
