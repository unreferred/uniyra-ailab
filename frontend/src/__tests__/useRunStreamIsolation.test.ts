import { acceptsRunStreamEvent } from "@/hooks/useRunStream";

describe("run stream execution isolation", () => {
  test("late conversation A events are rejected after B becomes active", () => {
    const activeConversation = "conversation-b";
    const activeGeneration = 2;

    expect(acceptsRunStreamEvent(
      { conversation_id: "conversation-a", delta: "ALPHA_CONTEXT_91" },
      activeConversation,
      1,
      activeGeneration,
    )).toBe(false);

    expect(acceptsRunStreamEvent(
      { conversation_id: "conversation-b", delta: "BETA_CONTEXT_47" },
      activeConversation,
      2,
      activeGeneration,
    )).toBe(true);

    expect(acceptsRunStreamEvent(
      { conversation_id: "conversation-a", delta: "alpha answer" },
      "conversation-a",
      2,
      activeGeneration,
    )).toBe(true);
  });
});