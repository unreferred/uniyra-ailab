import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CodeInterpreterPanel } from "@/components/workspace/CodeInterpreterPanel";
import {
  codeInterpreterStatus,
  downloadExecutionFile,
  executeCode,
} from "@/lib/api";

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), message: jest.fn() },
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  apiError: (e: any) => (e && e.message) || "error",
  codeInterpreterStatus: jest.fn(),
  executeCode: jest.fn(),
  downloadExecutionFile: jest.fn(),
}));

const statusAvailable = {
  available: true,
  reason: null,
  image: "omnilab-code-interpreter:latest",
  runtime: "docker",
  limits: { memory_mb: 512, timeout_s: 30 },
  policy: { network: "off", packages: ["python", "pandas", "numpy", "matplotlib"], tool: "code_interpreter" },
};

function renderPanel(props: any = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CodeInterpreterPanel projectId="p1" {...props} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  (downloadExecutionFile as jest.Mock).mockResolvedValue("blob:mock");
});

test("unavailable status disables run with docker image guidance", async () => {
  (codeInterpreterStatus as jest.Mock).mockResolvedValueOnce({
    available: false,
    reason: "sandbox image is not available",
    image: "omnilab-code-interpreter:latest",
    limits: { memory_mb: 512 },
    policy: { network: "off", packages: ["python"] },
  });
  renderPanel();
  const run = await screen.findByTestId("code-interpreter-run");
  expect(await screen.findByTestId("code-interpreter-unavailable")).toBeInTheDocument();
  expect(run).toBeDisabled();
  expect(screen.getByTestId("code-interpreter-unavailable-hint")).toHaveTextContent("docker image inspect");
  expect(executeCode).not.toHaveBeenCalled();
});

test("success renders stdout and generated files with downloads", async () => {
  const user = userEvent.setup({ delay: null });
  (codeInterpreterStatus as jest.Mock).mockResolvedValueOnce(statusAvailable);
  (executeCode as jest.Mock).mockResolvedValueOnce({
    status: "success",
    stdout: "hello from sandbox\n",
    stderr: "",
    exit_code: 0,
    duration_ms: 42,
    files: [{ name: "plot.png" }, "out.csv"],
    execution_id: "exec-123",
  });
  renderPanel({ initialCode: 'print("hi")' });
  await waitFor(() =>
    expect(screen.getByTestId("code-interpreter-run")).not.toBeDisabled()
  );
  await user.click(screen.getByTestId("code-interpreter-run"));
  expect(executeCode).toHaveBeenCalledWith({
    project_id: "p1",
    code: expect.stringContaining('print("hi")'),
    input_file_ids: [],
  });
  expect(await screen.findByTestId("code-interpreter-stdout")).toHaveTextContent("hello from sandbox");
  expect(screen.getByTestId("code-interpreter-files")).toHaveTextContent("plot.png");
  expect(screen.getByTestId("code-interpreter-file-plot.png-download")).toBeInTheDocument();
  await user.click(screen.getByTestId("code-interpreter-file-plot.png-download"));
  expect(downloadExecutionFile).toHaveBeenCalledWith("exec-123", "plot.png");
});

test("timeout result shows the time-limit note; secret never in DOM", async () => {
  const user = userEvent.setup({ delay: null });
  (codeInterpreterStatus as jest.Mock).mockResolvedValueOnce(statusAvailable);
  (executeCode as jest.Mock).mockResolvedValueOnce({
    status: "timeout",
    stdout: "",
    stderr: "",
    exit_code: null,
    duration_ms: 30000,
    files: [],
    execution_id: "exec-999",
    error: { category: "CODE_TIMEOUT", message: "Execution exceeded the 30s time limit" },
  });
  renderPanel({ initialCode: "while True: pass" });
  await waitFor(() =>
    expect(screen.getByTestId("code-interpreter-run")).not.toBeDisabled()
  );
  await user.click(screen.getByTestId("code-interpreter-run"));
  expect(await screen.findByTestId("code-interpreter-timeout-note")).toHaveTextContent("time limit");
  expect(screen.getByTestId("code-interpreter-error")).toHaveTextContent("CODE_TIMEOUT");
  expect(document.body.textContent || "").not.toMatch(/sk-|supersecret|hunter2/i);
});

test("input file checkboxes are staged via input_file_ids", async () => {
  const user = userEvent.setup({ delay: null });
  (codeInterpreterStatus as jest.Mock).mockResolvedValueOnce(statusAvailable);
  (executeCode as jest.Mock).mockResolvedValueOnce({
    status: "success",
    stdout: "ok",
    stderr: "",
    exit_code: 0,
    duration_ms: 10,
    files: [],
    execution_id: "exec-1",
  });
  renderPanel({
    attachments: [{ id: "a1", filename: "data.csv" }],
    initialCode: "print(open('/workspace/input/000_data.csv').read())",
  });
  await user.click(screen.getByTestId("code-interpreter-run"));
  await screen.findByTestId("code-interpreter-run");
  await user.click(screen.getByTestId("code-interpreter-input-a1").querySelector('input') as HTMLElement);
  await user.click(screen.getByTestId("code-interpreter-run"));
  await screen.findByTestId("code-interpreter-stdout");
  expect(executeCode).toHaveBeenLastCalledWith({
    project_id: "p1",
    code: expect.any(String),
    input_file_ids: ["a1"],
  });
});
