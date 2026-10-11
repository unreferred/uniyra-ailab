import React from "react";
import { Badge } from "@/components/ui/badge";

type ProvenanceProps = {
  metadata?: Record<string, any> | null;
  testId?: string;
};

function numberValue(value: any): number | null {
  return value != null && Number.isFinite(Number(value)) ? Number(value) : null;
}

function sourceLabel(source: any, index: number): string {
  if (typeof source === "string") return source;
  if (!source || typeof source !== "object") return `chunk ${index + 1}`;
  return String(source.display_name || source.collection_name || source.document_id || source.chunk_id || `chunk ${index + 1}`);
}

export const KnowledgeProvenance: React.FC<ProvenanceProps> = ({ metadata, testId = "knowledge-provenance" }) => {
  const raw = metadata && typeof metadata === "object" ? metadata : {};
  const meta = raw.provenance && typeof raw.provenance === "object" ? raw.provenance : raw;
  const sources = Array.isArray(meta.sources) ? meta.sources : [];
  const chunkIds = Array.isArray(meta.chunk_ids) ? meta.chunk_ids : [];
  const scores = Array.isArray(meta.scores) ? meta.scores : [];
  const order = Array.isArray(meta.ordering) ? meta.ordering : [];
  const entries = (sources.length > 0 ? sources : chunkIds).slice(0, 20).map((source: any, index: number) => ({
    marker: order[index] ?? index + 1,
    label: sourceLabel(source, index),
    score: numberValue(typeof source === "object" ? source?.score : null) ?? numberValue(scores[index]),
  }));
  const collectionIds = Array.isArray(meta.collection_ids) ? meta.collection_ids : [];
  const fileIds = Array.isArray(meta.file_ids) ? meta.file_ids : [];
  const count = meta.chunk_count ?? meta.returned_count ?? meta.result_count ?? entries.length;
  const hasMetadata = entries.length > 0 || collectionIds.length > 0 || fileIds.length > 0 || meta.config_source || meta.query_hash;

  if (!hasMetadata) return null;

  return (
    <div className="mt-2 rounded bg-muted/40 p-2" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Knowledge provenance</span>
        <Badge variant="outline" className="px-1 py-0 text-[9px]">{count} chunks</Badge>
        {meta.deduplicated_count != null && <Badge variant="outline" className="px-1 py-0 text-[9px]">{meta.deduplicated_count} unique</Badge>}
        {meta.config_source && <Badge variant="secondary" className="px-1 py-0 text-[9px]">config: {String(meta.config_source)}</Badge>}
      </div>
      {collectionIds.length > 0 && <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">Collections: {collectionIds.join(", ")}</div>}
      {fileIds.length > 0 && <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">Files: {fileIds.join(", ")}</div>}
      {meta.query_hash && <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">Query hash: {String(meta.query_hash)}</div>}
      {entries.length > 0 && (
        <ul className="mt-1 space-y-0.5" data-testid={`${testId}-markers`}>
          {entries.map((entry, index) => (
            <li key={`${entry.label}-${index}`} className="font-mono text-[10px] text-muted-foreground">
              <span className="mr-1 text-foreground">[K{entry.marker}]</span>{" "}{entry.label}
              {entry.score != null && ` · ${entry.score.toFixed(4)}`}
            </li>
          ))}
        </ul>
      )}
      {(Array.isArray(meta.embedding_models) || meta.retrieval_config) && (
        <div className="mt-1 font-mono text-[10px] text-muted-foreground">
          {Array.isArray(meta.embedding_models) && meta.embedding_models.length > 0 && `Embedding: ${meta.embedding_models.join(", ")}`}
          {meta.retrieval_config && ` · Retrieval: ${JSON.stringify(meta.retrieval_config)}`}
        </div>
      )}
    </div>
  );
};
