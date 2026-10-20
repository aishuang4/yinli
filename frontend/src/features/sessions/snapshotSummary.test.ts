import { describe, expect, it } from "vitest";

import { formatSnapshotSummary } from "./snapshotSummary";

describe("snapshot summary", () => {
  it("shows agent, skills, tools and versions without secrets", () => {
    const text = formatSnapshotSummary({
      agentId: "default",
      skills: ["report"],
      tools: ["remote"],
      directoryGrantVersion: 2,
      resourceGrantVersion: 4,
    });
    expect(text).toContain("Agent default");
    expect(text).toContain("Skills report");
    expect(text).toContain("Tools remote");
    expect(text).toContain("directory v2");
    expect(text).toContain("resources v4");
    expect(text).not.toContain("sk-");
    expect(text).not.toContain("secret");
  });
});
