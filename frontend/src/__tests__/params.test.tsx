import React, { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ParamsPopover } from "@/components/workspace/ParamsPopover";

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

type Field = "systemPrompt" | "temperature" | "maxTokens" | "topP" | "seed" | "streaming";

function Harness({
  onField,
  streamSupported = true,
}: {
  onField: (field: Field, value: any) => void;
  streamSupported?: boolean;
}) {
  const [systemPrompt, setSystemPrompt] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [topP, setTopP] = useState(1);
  const [seed, setSeed] = useState("");
  const [streaming, setStreaming] = useState(false);

  return (
    <ParamsPopover
      systemPrompt={systemPrompt}
      onSystemPrompt={(v) => {
        setSystemPrompt(v);
        onField("systemPrompt", v);
      }}
      temperature={temperature}
      onTemperature={(v) => {
        setTemperature(v);
        onField("temperature", v);
      }}
      maxTokens={maxTokens}
      onMaxTokens={(v) => {
        setMaxTokens(v);
        onField("maxTokens", v);
      }}
      topP={topP}
      onTopP={(v) => {
        setTopP(v);
        onField("topP", v);
      }}
      seed={seed}
      onSeed={(v) => {
        setSeed(v);
        onField("seed", v);
      }}
      streaming={streaming}
      onStreaming={(v) => {
        setStreaming(v);
        onField("streaming", v);
      }}
      streamSupported={streamSupported}
    />
  );
}

const lastFieldCall = (spy: jest.Mock, field: Field) =>
  [...spy.mock.calls].reverse().find((c) => c[0] === field);

test("temperature, max tokens, top p, seed and streaming report values", async () => {
  const user = userEvent.setup({ delay: null });
  const onField = jest.fn();
  render(<Harness onField={onField} />);

  await user.click(screen.getByTestId("workspace-params-button"));

  const tempThumb = within(screen.getByTestId("workspace-temperature")).getByRole("slider");
  tempThumb.focus();
  await user.keyboard("{ArrowRight}");
  expect(lastFieldCall(onField, "temperature")![1]).toBeCloseTo(0.75, 5);

  const tokenThumb = within(screen.getByTestId("workspace-max-tokens")).getByRole("slider");
  tokenThumb.focus();
  await user.keyboard("{ArrowRight}");
  expect(lastFieldCall(onField, "maxTokens")![1]).toBe(1152);

  const topPThumb = within(screen.getByTestId("workspace-top-p")).getByRole("slider");
  topPThumb.focus();
  await user.keyboard("{ArrowLeft}");
  expect(lastFieldCall(onField, "topP")![1]).toBeCloseTo(0.95, 5);

  const seedInput = screen.getByTestId("workspace-seed");
  await user.clear(seedInput);
  await user.type(seedInput, "42");
  expect(lastFieldCall(onField, "seed")![1]).toBe("42");

  await user.click(screen.getByTestId("workspace-stream-toggle"));
  expect(lastFieldCall(onField, "streaming")![1]).toBe(true);
});

test("slider bounds expose the clamping range to the DOM", async () => {
  const user = userEvent.setup({ delay: null });
  render(<Harness onField={jest.fn()} />);
  await user.click(screen.getByTestId("workspace-params-button"));

  const temp = within(screen.getByTestId("workspace-temperature")).getByRole("slider");
  expect(temp).toHaveAttribute("aria-valuemin", "0");
  expect(temp).toHaveAttribute("aria-valuemax", "2");

  const tokens = within(screen.getByTestId("workspace-max-tokens")).getByRole("slider");
  expect(tokens).toHaveAttribute("aria-valuemin", "128");
  expect(tokens).toHaveAttribute("aria-valuemax", "8192");

  const topP = within(screen.getByTestId("workspace-top-p")).getByRole("slider");
  expect(topP).toHaveAttribute("aria-valuemin", "0");
  expect(topP).toHaveAttribute("aria-valuemax", "1");
});

test("streaming toggle is user-controllable when supported", async () => {
  const user = userEvent.setup({ delay: null });
  const onField = jest.fn();
  render(<Harness onField={onField} />);
  await user.click(screen.getByTestId("workspace-params-button"));

  const toggle = screen.getByTestId("workspace-stream-toggle");
  expect(toggle).toBeEnabled();
  await user.click(toggle);
  expect(lastFieldCall(onField, "streaming")![1]).toBe(true);
});

test("streaming is disabled with an explanation when unsupported", async () => {
  const user = userEvent.setup({ delay: null });
  render(<Harness onField={jest.fn()} streamSupported={false} />);
  await user.click(screen.getByTestId("workspace-params-button"));

  expect(screen.getByTestId("workspace-stream-toggle")).toBeDisabled();
  expect(screen.getByText(/Streaming is not supported by this model/i)).toBeInTheDocument();
  expect(screen.queryByText(/tools require/i)).not.toBeInTheDocument();
});
