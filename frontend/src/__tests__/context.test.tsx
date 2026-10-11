import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContextPopover, DEFAULT_CONTEXT_OPTIONS } from "@/components/workspace/ContextPopover";

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

function renderContext(overrides: Partial<React.ComponentProps<typeof ContextPopover>> = {}) {
  const onChange = overrides.onChange || jest.fn();
  const onStrategyChange = overrides.onStrategyChange || jest.fn();
  render(
    <ContextPopover
      options={DEFAULT_CONTEXT_OPTIONS}
      onChange={onChange}
      strategy="FULL"
      onStrategyChange={onStrategyChange}
      {...overrides}
    />
  );
  return { onChange: onChange as jest.Mock, onStrategyChange: onStrategyChange as jest.Mock };
}

test("toggling each context option reports the updated selection", async () => {
  const user = userEvent.setup({ delay: null });
  const { onChange } = renderContext();

  await user.click(screen.getByTestId("workspace-context-button"));

  // All four supported toggles are present.
  expect(screen.getByText("System Prompt")).toBeInTheDocument();
  expect(screen.getByText("Conversation History")).toBeInTheDocument();
  expect(screen.getByText("Attachments")).toBeInTheDocument();
  expect(screen.getByText("Tool Definitions")).toBeInTheDocument();

  await user.click(screen.getByTestId("context-option-system_prompt"));
  expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_CONTEXT_OPTIONS, system_prompt: false });

  await user.click(screen.getByTestId("context-option-conversation_history"));
  expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_CONTEXT_OPTIONS, conversation_history: false });

  await user.click(screen.getByTestId("context-option-attachments"));
  expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_CONTEXT_OPTIONS, attachments: false });

  await user.click(screen.getByTestId("context-option-tool_definitions"));
  expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_CONTEXT_OPTIONS, tool_definitions: false });
});

test("changing the strategy select reports the chosen strategy", async () => {
  const user = userEvent.setup({ delay: null });
  const { onStrategyChange } = renderContext();

  await user.click(screen.getByTestId("workspace-context-button"));
  await user.click(screen.getByTestId("workspace-context-strategy"));

  await user.click(await screen.findByRole("option", { name: "RECENT_MESSAGES" }));
  expect(onStrategyChange).toHaveBeenCalledWith("RECENT_MESSAGES");

  // The strategy hint reflects the current prop value.
  expect(screen.getByText("Include every selected component.")).toBeInTheDocument();
});

test("context popover stays focused on context; knowledge lives in its own popover", async () => {
  const user = userEvent.setup({ delay: null });
  renderContext();

  await user.click(screen.getByTestId("workspace-context-button"));

  // The Context popover keeps its four context toggles and carries no
  // project-context / retrieved-knowledge options (knowledge selection is
  // owned by the dedicated Knowledge popover in the composer toolbar).
  expect(screen.queryByTestId("context-option-project_context")).not.toBeInTheDocument();
  expect(screen.queryByTestId("context-option-retrieved_knowledge")).not.toBeInTheDocument();
  expect(screen.queryByText(/project context/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/retrieved/i)).not.toBeInTheDocument();
});
