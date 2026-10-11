import React from "react";
import { render, screen } from "@testing-library/react";
import { AttachmentViewer } from "@/components/workspace/AttachmentViewer";
import { fetchAttachmentBlob, fetchAttachmentText } from "@/lib/api";
import { Attachment } from "@/lib/types";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
  fetchAttachmentBlob: jest.fn(),
  fetchAttachmentText: jest.fn(),
}));

const mockedBlob = fetchAttachmentBlob as jest.Mock;
const mockedText = fetchAttachmentText as jest.Mock;

function att(over: Partial<Attachment> = {}): Attachment {
  return {
    id: "a1",
    owner_id: "u1",
    project_id: "p1",
    filename: "stored.bin",
    original_filename: "photo.png",
    mime_type: "image/png",
    kind: "image",
    size_bytes: 2048,
    sha256: "deadbeef",
    processing_status: "ready",
    extraction_status: "done",
    created_at: new Date("2026-01-01T00:00:00.000Z").toISOString(),
    ...over,
  };
}

beforeEach(() => {
  mockedBlob.mockReset();
  mockedText.mockReset();
});

test("image kind fetches a blob and renders an img", async () => {
  mockedBlob.mockResolvedValue("blob:mock-image");
  render(
    <AttachmentViewer attachment={att({ kind: "image" })} open onOpenChange={() => {}} />
  );

  const img = await screen.findByAltText("photo.png");
  expect(img).toHaveAttribute("src", "blob:mock-image");
  expect(mockedBlob).toHaveBeenCalledWith("a1");
  expect(screen.getByText("image")).toBeInTheDocument();
});

test("text kind fetches extracted text and renders it with metadata", async () => {
  mockedText.mockResolvedValue({
    text: "extracted body text",
    available: true,
    pages: 2,
    char_count: 19,
    extraction_status: "done",
  });
  render(
    <AttachmentViewer
      attachment={att({ kind: "text", original_filename: "notes.txt", mime_type: "text/plain" })}
      open
      onOpenChange={() => {}}
    />
  );

  expect(await screen.findByText("extracted body text")).toBeInTheDocument();
  expect(mockedText).toHaveBeenCalledWith("a1");
  expect(screen.getByText("2 pages")).toBeInTheDocument();
  expect(screen.getByText("19 chars")).toBeInTheDocument();
});

test("pdf text extraction is shown when available", async () => {
  mockedText.mockResolvedValue({
    text: "pdf content",
    available: true,
    pages: 3,
    char_count: 11,
    extraction_status: "done",
  });
  render(
    <AttachmentViewer
      attachment={att({ kind: "pdf", original_filename: "doc.pdf", mime_type: "application/pdf" })}
      open
      onOpenChange={() => {}}
    />
  );

  expect(await screen.findByText("pdf content")).toBeInTheDocument();
  expect(screen.getByText("3 pages")).toBeInTheDocument();
});

test("pdf with unavailable extraction shows a message instead of content", async () => {
  mockedText.mockResolvedValue({ available: false, extraction_status: "skipped" });
  render(
    <AttachmentViewer
      attachment={att({ kind: "pdf", original_filename: "doc.pdf", mime_type: "application/pdf" })}
      open
      onOpenChange={() => {}}
    />
  );

  expect(
    await screen.findByText("Text extraction is not available for this attachment.")
  ).toBeInTheDocument();
});

test("unsupported kind shows a preview message and image metadata without fetching", async () => {
  render(
    <AttachmentViewer
      attachment={att({
        kind: "unsupported",
        original_filename: "archive.zip",
        mime_type: "application/zip",
        image_metadata: { width: 100, height: 50 },
      })}
      open
      onOpenChange={() => {}}
    />
  );

  expect(await screen.findByText("Preview is not supported for this file type.")).toBeInTheDocument();
  expect(screen.getByText("Image metadata")).toBeInTheDocument();
  expect(screen.getByText(/"width": 100/)).toBeInTheDocument();
  expect(mockedBlob).not.toHaveBeenCalled();
  expect(mockedText).not.toHaveBeenCalled();
});
