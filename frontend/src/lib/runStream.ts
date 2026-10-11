import { API_BASE, csrfHeader } from "./api";

export interface StreamRunOptions {
  signal?: AbortSignal;
  onEvent: (evt: any) => void;
}

/**
 * POST to the agentic streaming endpoint and pump SSE `data:` frames into
 * `onEvent`. Dependency-free, abortable, and tolerant of a final partial
 * buffer. Frames that are not valid JSON are ignored.
 */
export async function streamRun(
  conversationId: string,
  payload: object,
  opts: StreamRunOptions
): Promise<void> {
  const resp = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...csrfHeader(),
    },
    body: JSON.stringify(payload),
    signal: opts.signal,
  });

  if (!resp.ok) {
    throw new Error(`Stream failed (${resp.status})`);
  }
  if (!resp.body) {
    throw new Error("Stream failed (no response body)");
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const drain = (final = false): void => {
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || "";
    for (const part of parts) emitFrame(part, opts.onEvent);
    if (final) {
      const rest = buffer.trim();
      if (rest) emitFrame(rest, opts.onEvent);
      buffer = "";
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    drain(false);
  }
  buffer += decoder.decode();
  drain(true);
}

function emitFrame(frame: string, onEvent: (evt: any) => void): void {
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    // Only `data:` frames carry payloads; `:` comments and other fields are ignored.
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return;
  const raw = dataLines.join("\n").trim();
  if (!raw || raw === "[DONE]") return;
  try {
    onEvent(JSON.parse(raw));
  } catch {
    // Malformed frame — ignore rather than break the stream.
  }
}
