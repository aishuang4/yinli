/** 桌面目錄選擇契約。授權只來自主進程對話框返回的真實路徑。 */

import fs from "node:fs";
import path from "node:path";

export type DirectoryAccessMode = "read" | "read_write";

export type DirectoryGrant = {
  path: string;
  accessMode: DirectoryAccessMode;
};

export function grantFromDialogSelection(
  dialogPath: string | null | undefined,
  clientPath?: string | null,
): DirectoryGrant | null {
  if (!dialogPath || typeof dialogPath !== "string") {
    return null;
  }
  let realPath: string;
  try {
    realPath = fs.realpathSync(dialogPath);
    if (!fs.statSync(realPath).isDirectory()) {
      return null;
    }
  } catch {
    return null;
  }
  if (clientPath && path.resolve(clientPath) !== realPath) {
    try {
      if (fs.realpathSync(clientPath) !== realPath) {
        return {
          path: realPath,
          accessMode: "read_write",
        };
      }
    } catch {
      return {
        path: realPath,
        accessMode: "read_write",
      };
    }
  }
  return { path: realPath, accessMode: "read_write" };
}
