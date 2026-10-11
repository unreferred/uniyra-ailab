import React from "react";
import { render, screen } from "@testing-library/react";
import { KnowledgeProvenance } from "@/components/workspace/KnowledgeProvenance";

test("renders V4 knowledge markers and metadata without private content", () => {
  render(
    <KnowledgeProvenance
      metadata={{
        chunk_count: 2,
        chunk_ids: ["chunk-a", "chunk-b"],
        collection_ids: ["collection-7"],
        file_ids: ["file-9"],
        scores: [0.98765, 0.87654],
        ordering: [1, 2],
        query_hash: "qh-123",
        config_source: "conversation",
        content: "private document text that must not be rendered",
      }}
    />
  );

  expect(screen.getByTestId("knowledge-provenance")).toHaveTextContent("Knowledge provenance");
  expect(screen.getByTestId("knowledge-provenance-markers")).toHaveTextContent("[K1] chunk-a");
  expect(screen.getByTestId("knowledge-provenance-markers")).toHaveTextContent("[K2] chunk-b");
  expect(screen.getByTestId("knowledge-provenance")).toHaveTextContent("collection-7");
  expect(screen.getByTestId("knowledge-provenance")).toHaveTextContent("qh-123");
  expect(screen.getByTestId("knowledge-provenance")).not.toHaveTextContent("private document text");
});
