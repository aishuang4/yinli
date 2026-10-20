/** 把 Run snapshot 摘要顯示成可讀的 Agent、Skill、工具與目錄版本。 */

export type SnapshotAudit = {
  agentId?: string;
  skills?: string[];
  tools?: string[];
  directoryGrantVersion?: number;
  resourceGrantVersion?: number;
};

export function formatSnapshotSummary(summary: SnapshotAudit): string {
  const skills = summary.skills?.length ? summary.skills.join(", ") : "none";
  const tools = summary.tools?.length ? summary.tools.join(", ") : "none";
  return [
    `Agent ${summary.agentId || "unknown"}`,
    `Skills ${skills}`,
    `Tools ${tools}`,
    `directory v${summary.directoryGrantVersion ?? 0}`,
    `resources v${summary.resourceGrantVersion ?? 0}`,
  ].join(" · ");
}
