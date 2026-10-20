import { describe, expect, it } from "vitest";

import type { UserNotification } from "../../types/notification";
import { notificationClickPlan } from "./notificationViewUtils";

function note(overrides: Partial<UserNotification>): UserNotification {
  return {
    id: "n1",
    jobId: "",
    firingId: "",
    kind: "job_completed",
    title: "完成",
    body: "",
    payload: {},
    readAt: null,
    createdAt: "2026-10-19T00:00:00Z",
    ...overrides,
  } as UserNotification;
}

describe("notification session click", () => {
  it("opens an active or archived owner session and stops on a deleted one", () => {
    expect(notificationClickPlan(note({
      ownerSessionId: "session-a",
      sessionStatus: "active",
    }))).toEqual({ action: "openSession", sessionId: "session-a", status: "active" });
    expect(notificationClickPlan(note({
      ownerSessionId: "session-old",
      sessionStatus: "archived",
    }))).toEqual({ action: "openSession", sessionId: "session-old", status: "archived" });
    expect(notificationClickPlan(note({
      ownerSessionId: "session-gone",
      sessionStatus: "deleted",
    }))).toEqual({ action: "unavailable", reason: "deleted" });
  });
});
