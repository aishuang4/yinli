/**
 * MCP 相关类型定义
 *
 * 与 backend/src/server/routes/mcp.py 的 Pydantic 模型保持同步。
 */

export type McpTransport = 'stdio' | 'sse' | 'websocket';

export type McpInstallStatus = 'available' | 'installed' | 'error';

export interface McpTool {
  id: string;
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface McpServer {
  id: string;
  name: string;
  description: string;
  category: string;
  transport: McpTransport;
  command: string;
  url: string;
  homepage: string;
  headers: Record<string, string>;
  tags: string[];
  tools: McpTool[];
  status: McpInstallStatus;
  enabled: boolean;
  installedAt: string | null;
  updatedAt: string;
}

export interface McpRegisterPayload {
  name: string;
  url: string;
  description?: string;
  headers?: Record<string, string>;
}
