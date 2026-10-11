import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { ParamsPopover } from "../components/workspace/ParamsPopover";

describe("ParamsPopover - Output tokens budget", () => {
  const defaultProps = {
    systemPrompt: "test prompt",
    onSystemPrompt: jest.fn(),
    temperature: 0.7,
    onTemperature: jest.fn(),
    maxTokens: 2048,
    onMaxTokens: jest.fn(),
    topP: 0.9,
    onTopP: jest.fn(),
    seed: "",
    onSeed: jest.fn(),
    streaming: true,
    onStreaming: jest.fn(),
    streamSupported: true,
  };

  it("renders parameters button and opens popover", () => {
    render(<ParamsPopover {...defaultProps} />);
    const btn = screen.getByTestId("workspace-params-button");
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);

    expect(screen.getByTestId("workspace-params")).toBeInTheDocument();
    expect(screen.getByText("Output tokens")).toBeInTheDocument();
    expect(screen.getByText("2048 tokens")).toBeInTheDocument();
  });

  it("displays model max output capability when model is known", () => {
    const model = {
      id: "m1",
      owner_id: "o1",
      provider_id: "p1",
      display_name: "Ling 3.0 Flash",
      model_identifier: "inclusionai/ling-3.0-flash-fin:free",
      family: "openai",
      enabled: true,
      context_window: 262144,
      max_output_tokens: 8192,
      input_cost: null,
      output_cost: null,
      capabilities: {},
      tags: [],
      notes: "",
      created_at: "",
      updated_at: "",
    };

    render(<ParamsPopover {...defaultProps} maxTokens={10000} selectedModel={model} />);
    const btn = screen.getByTestId("workspace-params-button");
    fireEvent.click(btn);

    expect(screen.getByTestId("params-model-max")).toHaveTextContent("Model max: 8192");
    expect(screen.getByTestId("params-clamped")).toHaveTextContent("Clamped to 8192");
  });

  it("handles auto / model default when maxTokens is 0", () => {
    render(<ParamsPopover {...defaultProps} maxTokens={0} />);
    fireEvent.click(screen.getByTestId("workspace-params-button"));

    expect(screen.getByText("Auto / Model default")).toBeInTheDocument();
    expect(screen.getByText("Model max: Unknown")).toBeInTheDocument();
  });
});
