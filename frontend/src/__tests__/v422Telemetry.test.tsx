import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReasoningPanel } from "@/components/workspace/ReasoningPanel";
import { TelemetryRow } from "@/components/common/Common";
import { Telemetry } from "@/lib/types";

describe("V4.2.2 Telemetry & Reasoning UI", () => {
  describe("ReasoningPanel", () => {
    it("renders nothing when no reasoning telemetry is available (never fabricates)", () => {
      const { container } = render(
        <ReasoningPanel reasoningTelemetry={{ available: false }} />
      );
      expect(container.firstChild).toBeNull();
      expect(screen.queryByTestId("reasoning-panel")).toBeNull();
      expect(screen.queryByText(/thinking/i)).toBeNull();
    });

    it("renders collapsible panel with reasoning tokens count", () => {
      render(
        <ReasoningPanel
          reasoningTelemetry={{
            available: true,
            tokens: 420,
            content: "Step 1: Analyze problem\nStep 2: Solve",
            streamed: true,
          }}
        />
      );

      expect(screen.getByTestId("reasoning-panel")).toBeInTheDocument();
      expect(screen.getByText("420 tokens")).toBeInTheDocument();
      expect(screen.getByText("streamed")).toBeInTheDocument();

      // Initially collapsed by default
      expect(screen.queryByTestId("reasoning-panel-content")).toBeNull();

      // Click to expand
      fireEvent.click(screen.getByTestId("reasoning-panel-toggle"));
      expect(screen.getByTestId("reasoning-panel-content")).toBeInTheDocument();
      expect(screen.getByText(/Step 1: Analyze problem/)).toBeInTheDocument();
    });

    it("handles tokens reported without text content without fabricating text", () => {
      render(
        <ReasoningPanel
          reasoningTelemetry={{
            available: true,
            tokens: 150,
            content: null,
          }}
          defaultExpanded={true}
        />
      );

      expect(screen.getByTestId("reasoning-panel")).toBeInTheDocument();
      expect(screen.getByText("150 tokens")).toBeInTheDocument();
      expect(
        screen.getByText(/without textual chain-of-thought content/i)
      ).toBeInTheDocument();
    });

    it("copies reasoning content to clipboard on copy button click", async () => {
      const writeTextMock = jest.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: {
          writeText: writeTextMock,
        },
      });

      render(
        <ReasoningPanel
          reasoningTelemetry={{
            available: true,
            content: "Detailed mathematical proof step 1...",
          }}
          defaultExpanded={true}
        />
      );

      const copyBtn = screen.getByTestId("reasoning-copy-button");
      fireEvent.click(copyBtn);

      expect(writeTextMock).toHaveBeenCalledWith(
        "Detailed mathematical proof step 1..."
      );
      await waitFor(() => {
        expect(screen.getByText("Copied")).toBeInTheDocument();
      });
    });
  });

  describe("TelemetryRow Extended Metrics", () => {
    it("renders baseline metrics when extended telemetry is absent", () => {
      const t: Telemetry = {
        latency_ms: 450,
        input_tokens: 100,
        output_tokens: 200,
        estimated_cost: 0.0015,
        cost_available: true,
      };
      render(<TelemetryRow t={t} />);

      expect(screen.getByTestId("telemetry-row")).toBeInTheDocument();
      expect(screen.getByText("450 ms")).toBeInTheDocument();
      expect(screen.getByText("100")).toBeInTheDocument();
      expect(screen.getByText("200")).toBeInTheDocument();
      expect(screen.getByText("$0.00150")).toBeInTheDocument();
      expect(screen.queryByTestId("telemetry-reasoning")).toBeNull();
      expect(screen.queryByTestId("telemetry-cache")).toBeNull();
    });

    it("renders reasoning tokens and cache metrics when present", () => {
      const t: Telemetry = {
        latency_ms: 1200,
        input_tokens: 1000,
        output_tokens: 500,
        reasoning_tokens: 250,
        cache_read_tokens: 800,
        cache_telemetry: {
          cache_read_tokens: 800,
          cache_hit_rate: 0.8,
        },
        estimated_cost: 0.0042,
        cost_available: true,
      };
      render(<TelemetryRow t={t} />);

      expect(screen.getByTestId("telemetry-reasoning")).toBeInTheDocument();
      expect(screen.getByText("250")).toBeInTheDocument();

      expect(screen.getByTestId("telemetry-cache")).toBeInTheDocument();
      expect(screen.getByText("800 (80%)")).toBeInTheDocument();
    });
  });
});
