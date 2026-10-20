import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { grantFromDialogSelection } from "../../../../electron/directoryGrant";
import { requestHasHiddenScope } from "./catalogRequests";

describe("desktop directory grant", () => {
  it("keeps the dialog realpath and ignores a frontend path", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "yinli-grant-"));
    const selected = path.join(root, "selected");
    const injected = path.join(root, "injected");
    fs.mkdirSync(selected);
    fs.mkdirSync(injected);
    const before = fs.readdirSync(selected);

    const grant = grantFromDialogSelection(selected, injected);

    expect(grant?.path).toBe(fs.realpathSync(selected));
    expect(grant?.accessMode).toBe("read_write");
    expect(grant?.path).not.toBe(fs.realpathSync(injected));
    expect(fs.readdirSync(selected)).toEqual(before);
    expect(grantFromDialogSelection(null, injected)).toBeNull();
  });
});

describe("section 10 frontend scope", () => {
  const src = path.resolve(process.cwd(), "src");

  function source(relativePath: string): string {
    return fs.readFileSync(path.join(src, relativePath), "utf8");
  }

  it("does not boot workspace or project selectors", () => {
    expect(fs.existsSync(path.join(src, "hooks/useWorkspaceProjects.ts"))).toBe(false);
    for (const relativePath of ["stores/appStore.ts", "layouts/TitleBar.tsx", "pages/SettingsPage.tsx"]) {
      const text = source(relativePath);
      expect(text).not.toContain("activeWorkspace");
      expect(text).not.toContain("activeProject");
      expect(text).not.toContain("/api/workspaces");
    }
  });

  it("keeps catalog requests free of hidden scope queries", () => {
    for (const relativePath of [
      "hooks/useSkills.ts",
      "hooks/useMcp.ts",
      "hooks/useKnowledgeTools.ts",
      "stores/agentsStore.ts",
    ]) {
      expect(requestHasHiddenScope(source(relativePath))).toBe(false);
    }
  });
});
