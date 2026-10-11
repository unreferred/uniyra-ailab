import React, { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToolsPopover } from "@/components/workspace/ToolsPopover";
import { api } from "@/lib/api";
import { ToolRecord } from "@/lib/types";

jest.mock("@floating-ui/dom", () => {
  const actual = jest.requireActual("@floating-ui/dom");
  return {
    ...actual,
    // The real computePosition loops under jsdom (zero-size rects); positioning
    // is not under test, so return a stable placement.
    computePosition: () =>
      Promise.resolve({ x: 0, y: 0, strategy: "fixed", placement: "bottom", middlewareData: {} }),
  };
});

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  apiError: (e: any) => (e && e.message) || "error",
}));

const mockedGet = api.get as jest.Mock;

const tools: ToolRecord[] = [
  {
    id: "t1",
    name: "web_search",
    display_name: "Web Search",
    description: "Search the public web",
    version: "1",
    tool_type: "builtin",
    input_schema: {},
    enabled: true,
    risk_level: "low",
    permissions: [],
    available: true,
  },
  {
    id: "t2",
    name: "shell",
    display_name: "Shell",
    description: "Execute shell commands",
    version: "1",
    tool_type: "builtin",
    input_schema: {},
    enabled: true,
    risk_level: "high",
    permissions: [],
    available: false,
    availability_reason: "Requires a sandbox",
  },
  {
    id: "t3",
    name: "legacy",
    display_name: "Legacy Tool",
    description: "Disabled by policy",
    version: "1",
    tool_type: "builtin",
    input_schema: {},
    enabled: false,
    risk_level: "medium",
    permissions: [],
  },
];

beforeEach(() => {
  mockedGet.mockReset();
  mockedGet.mockImplementation((url: string) => {
    if (url === "/tools") return Promise.resolve({ data: { items: tools, total: tools.length } });
    if (url === "/models") return Promise.resolve({ data: [] });
    if (url === "/code-interpreter/status")
      return Promise.resolve({ data: { available: true, reason: null } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

function renderTools(props: { projectId?: string | null; onChange?: (ids: string[]) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToolsPopover projectId={props.projectId === undefined ? "p1" : props.projectId} value={[]} onChange={props.onChange || (() => {})} />
    </QueryClientProvider>
  );
}

function Harness({ onChange }: { onChange: (ids: string[]) => void }) {
  const [ids, setIds] = useState<string[]>([]);
  return (
    <ToolsPopover
      projectId="p1"
      value={ids}
      onChange={(next) => {
        setIds(next);
        onChange(next);
      }}
    />
  );
}

test("lists tools from GET /tools with name, description and risk level", async () => {
  const user = userEvent.setup({ delay: null });
  renderTools();

  expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 0");
  await user.click(screen.getByTestId("workspace-tools-button"));

  expect(await screen.findByText("Web Search")).toBeInTheDocument();
  expect(screen.getByText("Search the public web")).toBeInTheDocument();
  expect(screen.getByText("low")).toBeInTheDocument();

  expect(screen.getByText("Shell")).toBeInTheDocument();
  expect(screen.getByText("Execute shell commands")).toBeInTheDocument();
  expect(screen.getByText("high")).toBeInTheDocument();

  // The GET is scoped to the active project.
  expect(mockedGet).toHaveBeenCalledWith("/tools", { params: { project_id: "p1" } });
});

test("unavailable tools cannot be selected and explain why", async () => {
  const user = userEvent.setup({ delay: null });
  const onChange = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness onChange={onChange} />
    </QueryClientProvider>
  );

  await user.click(screen.getByTestId("workspace-tools-button"));
  await screen.findByText("Web Search");

  const unavailableCheckbox = within(screen.getByTestId("tool-t2")).getByRole("checkbox");
  expect(unavailableCheckbox).toBeDisabled();
  expect(screen.getByText("Requires a sandbox")).toBeInTheDocument();

  const policyDisabled = within(screen.getByTestId("tool-t3")).getByRole("checkbox");
  expect(policyDisabled).toBeDisabled();
  // Disabled tools always carry an explicit gate (never a bare grey checkbox).
  expect(screen.getByTestId("tool-t3")).toHaveAttribute("data-gate", "UNAVAILABLE");
  expect(screen.getByTestId("tool-reason-t3")).toHaveTextContent("Tool is disabled");

  // Clicking a disabled checkbox is a no-op.
  await user.click(unavailableCheckbox);
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 0");
});

test("toggling an available checkbox updates the selected ids via onChange", async () => {
  const user = userEvent.setup({ delay: null });
  const onChange = jest.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness onChange={onChange} />
    </QueryClientProvider>
  );

  await user.click(screen.getByTestId("workspace-tools-button"));
  await screen.findByText("Web Search");

  await user.click(within(screen.getByTestId("tool-t1")).getByRole("checkbox"));

  expect(onChange).toHaveBeenCalledWith(["t1"]);
  expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 1");

  await user.click(within(screen.getByTestId("tool-t1")).getByRole("checkbox"));
  expect(onChange).toHaveBeenLastCalledWith([]);
  expect(screen.getByTestId("workspace-tools-button")).toHaveTextContent("Tools: 0");
});

test("trigger is disabled without an active project", () => {
  renderTools({ projectId: null });
  expect(screen.getByTestId("workspace-tools-button")).toBeDisabled();
});
