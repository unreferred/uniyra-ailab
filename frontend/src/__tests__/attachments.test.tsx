import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AttachmentsBar } from "@/components/workspace/AttachmentsBar";
import { Attachment } from "@/lib/types";

function att(over: Partial<Attachment> = {}): Attachment {
  return {
    id: "a1",
    owner_id: "u1",
    project_id: "p1",
    filename: "stored.png",
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

test("renders a chip with filename, formatted size and kind-specific icon", () => {
  render(
    <AttachmentsBar
      attachments={[
        att({ id: "img", kind: "image" }),
        att({ id: "txt", kind: "text", original_filename: "notes.txt", size_bytes: 512 }),
        att({ id: "pdf", kind: "pdf", original_filename: "doc.pdf", size_bytes: 1024 * 1024 * 3 }),
        att({ id: "bin", kind: "unsupported", original_filename: "blob.bin" }),
      ]}
    />
  );

  // A chip's preferred label is the original filename and uses fmtBytes.
  const imgChip = screen.getByTestId("attachment-chip-img");
  expect(imgChip).toHaveTextContent("photo.png");
  expect(imgChip).toHaveTextContent("2.0 KB");
  expect(imgChip.querySelector(".lucide-image")).toBeTruthy();

  expect(screen.getByTestId("attachment-chip-txt")).toHaveTextContent("512 B");
  expect(screen.getByTestId("attachment-chip-txt").querySelector(".lucide-file-text")).toBeTruthy();

  expect(screen.getByTestId("attachment-chip-pdf")).toHaveTextContent("3.0 MB");
  expect(screen.getByTestId("attachment-chip-pdf").querySelector(".lucide-file-text")).toBeTruthy();

  expect(screen.getByTestId("attachment-chip-bin").querySelector(".lucide-file-x")).toBeTruthy();
});

test("remove button calls onRemove with the attachment id", async () => {
  const user = userEvent.setup();
  const onRemove = jest.fn();
  render(<AttachmentsBar attachments={[att()]} onRemove={onRemove} />);

  await user.click(screen.getByTestId("attachment-remove-a1"));
  expect(onRemove).toHaveBeenCalledTimes(1);
  expect(onRemove).toHaveBeenCalledWith("a1");
});

test("processing status and extraction errors are surfaced on the chip", () => {
  render(
    <AttachmentsBar
      attachments={[
        att({ id: "proc", processing_status: "processing" }),
        att({ id: "bad", processing_status: "error", metadata: { error: "Unsupported encoding" } }),
      ]}
    />
  );

  expect(screen.getByTestId("attachment-chip-proc")).toHaveTextContent("processing");
  expect(screen.getByTestId("attachment-chip-bad")).toHaveTextContent("Unsupported encoding");

  // The default failure message is used when metadata carries no error.
  render(<AttachmentsBar attachments={[att({ id: "bad2", extraction_status: "failed" })]} />);
  expect(screen.getByTestId("attachment-chip-bad2")).toHaveTextContent("Processing failed");
});

test("uploading state renders a spinner row and empty idle bar renders nothing", () => {
  const { container, rerender } = render(<AttachmentsBar attachments={[]} />);
  expect(container).toBeEmptyDOMElement();

  rerender(<AttachmentsBar attachments={[]} uploading />);
  expect(screen.getByTestId("attachments-bar")).toHaveTextContent("Uploading…");
});
