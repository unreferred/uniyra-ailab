import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RunStreamPanel } from "@/components/workspace/RunStreamPanel";
import type { RunStreamState } from "@/hooks/useRunStream";

// Mock MarkdownView since react-markdown is ESM
jest.mock("@/components/common/Markdown", () => ({
  MarkdownView: ({ content }: { content: string }) => <div data-testid="markdown-view">{content}</div>,
}));

function makeState(overrides: Partial<RunStreamState> = {}): RunStreamState {
  return {
    status: "idle",
    phase: "idle",
    activities: [],
    answerText: "",
    sawDelta: false,
    ...overrides,
  };
}

describe("RunStreamPanel & Failed Turn Recovery UX", () => {
  test("failed run without partial text shows Retry but not Continue", () => {
    const state = makeState({
      status: "error",
      error: { message: "Provider timeout" },
      answerText: "",
    });
    const onRetry = jest.fn();
    const onContinue = jest.fn();

    render(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
      />
    );

    expect(screen.getByText("Provider timeout")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-retry")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-continue")).toBeNull();

    fireEvent.click(screen.getByTestId("workspace-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onContinue).not.toHaveBeenCalled();
  });

  test("interrupted run with partial answer shows both Continue and Retry and preserves text", () => {
    const state = makeState({
      status: "error",
      error: { message: "Transport stream broken" },
      answerText: "Here is step 1 and step 2",
    });
    const onRetry = jest.fn();
    const onContinue = jest.fn();

    render(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
      />
    );

    // Partial answer is preserved and displayed
    expect(screen.getByTestId("run-answer")).toHaveTextContent("Here is step 1 and step 2");
    expect(screen.getByText("Transport stream broken")).toBeInTheDocument();

    // Both Continue and Retry should be available
    expect(screen.getByTestId("workspace-continue")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-retry")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("workspace-continue"));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  test("output_limit reached shows Continue action only", () => {
    const state = makeState({
      status: "output_limit",
      outputLimit: true,
      outputLimitReason: "max_tokens",
      answerText: "Partial response cut off by token limit",
    });
    const onRetry = jest.fn();
    const onContinue = jest.fn();

    render(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
      />
    );

    expect(screen.getAllByText(/Output limit reached/)[0]).toBeInTheDocument();
    expect(screen.getByTestId("run-answer")).toHaveTextContent("Partial response cut off by token limit");
    expect(screen.getByTestId("workspace-continue")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-retry")).toBeNull();
  });

  test("cancelled run with partial text shows Continue and Retry", () => {
    const state = makeState({
      status: "cancelled",
      answerText: "Partial draft before cancel",
    });
    const onRetry = jest.fn();
    const onContinue = jest.fn();

    render(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
      />
    );

    expect(screen.getByText("Run cancelled.")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-continue")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-retry")).toBeInTheDocument();
  });

  test("buttons show loading state and disable click when isRetrying or isContinuing", () => {
    const state = makeState({
      status: "error",
      error: { message: "Server error" },
      answerText: "Partial text",
    });
    const onRetry = jest.fn();
    const onContinue = jest.fn();

    const { rerender } = render(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
        isRetrying={true}
      />
    );

    expect(screen.getByTestId("workspace-retry")).toHaveTextContent("Retrying…");
    expect(screen.getByTestId("workspace-retry")).toBeDisabled();
    expect(screen.getByTestId("workspace-continue")).toBeDisabled();

    rerender(
      <RunStreamPanel
        state={state}
        onRetry={onRetry}
        onContinue={onContinue}
        isContinuing={true}
      />
    );

    expect(screen.getByTestId("workspace-continue")).toHaveTextContent("Continuing…");
    expect(screen.getByTestId("workspace-continue")).toBeDisabled();
    expect(screen.getByTestId("workspace-retry")).toBeDisabled();
  });
});
