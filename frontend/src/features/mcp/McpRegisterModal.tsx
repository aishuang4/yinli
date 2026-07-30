/**
 * McpRegisterModal — MCP Server 注册表单弹窗。
 *
 * 抽离 MCPPage 内的注册表单，统一使用表单基础组件和 Spark Design 控件。
 */

import { Plus, Trash2 } from 'lucide-react';
import { Button, Modal } from '@/components/design';
import { FormError, FormField, PasswordField, TextAreaField, TextField } from '../../components/form';

export interface McpRegisterFormState {
  name: string;
  url: string;
  description: string;
  headers: Array<{ key: string; value: string }>;
}

interface McpRegisterModalProps {
  open: boolean;
  form: McpRegisterFormState;
  error: string | null;
  registering: boolean;
  onFormChange: (form: McpRegisterFormState) => void;
  onCancel: () => void;
  onRegister: () => void;
}

export function McpRegisterModal({
  open,
  form,
  error,
  registering,
  onFormChange,
  onCancel,
  onRegister,
}: McpRegisterModalProps) {
  return (
    <Modal
      title="注册 MCP Server"
      open={open}
      onCancel={onCancel}
      onOk={onRegister}
      confirmLoading={registering}
      okText="拉取并注册"
      cancelText="取消"
      width={560}
    >
      <div className="space-y-4">
        <FormError>{error}</FormError>

        <TextField
          label="名称"
          required
          value={form.name}
          onChange={(event) => onFormChange({ ...form, name: event.target.value })}
          placeholder="如：Company Docs MCP"
          maxLength={80}
        />

        <TextField
          label="MCP 地址"
          required
          value={form.url}
          onChange={(event) => onFormChange({ ...form, url: event.target.value })}
          placeholder="https://example.com/mcp"
          help={<>支持 Streamable HTTP / JSON-RPC 的 tools/list，也兼容返回 <code>{'{"tools": [...]}'}</code> 的简单接口。</>}
        />

        <TextAreaField
          label="描述"
          value={form.description}
          onChange={(event) => onFormChange({ ...form, description: event.target.value })}
          placeholder="说明这个 MCP Server 提供什么能力"
          rows={3}
          maxLength={300}
        />

        <FormField
          label={(
            <div className="flex items-center justify-between gap-3">
              <span>请求头</span>
              <Button
                size="small"
                onClick={() => onFormChange({
                  ...form,
                  headers: [...form.headers, { key: '', value: '' }],
                })}
              >
                <span className="inline-flex items-center gap-1">
                  <Plus size={12} className="shrink-0" />
                  添加
                </span>
              </Button>
            </div>
          )}
          help="可用于 Authorization、X-API-Key 等认证信息；Content-Type 和 Accept 由系统自动设置。"
        >
          <div className="space-y-2">
            {form.headers.map((header, index) => (
              <div key={index} className="grid grid-cols-[1fr_1fr_32px] gap-2">
                <TextField
                  value={header.key}
                  onChange={(event) => {
                    const next = [...form.headers];
                    next[index] = { ...next[index], key: event.target.value };
                    onFormChange({ ...form, headers: next });
                  }}
                  placeholder="Header 名称，如 Authorization"
                />
                <PasswordField
                  value={header.value}
                  onChange={(event) => {
                    const next = [...form.headers];
                    next[index] = { ...next[index], value: event.target.value };
                    onFormChange({ ...form, headers: next });
                  }}
                  placeholder="Header 值"
                />
                <Button
                  type="text"
                  danger
                  disabled={form.headers.length === 1}
                  onClick={() => onFormChange({
                    ...form,
                    headers: form.headers.filter((_, itemIndex) => itemIndex !== index),
                  })}
                  aria-label="删除请求头"
                >
                  <Trash2 size={14} className="shrink-0" />
                </Button>
              </div>
            ))}
          </div>
        </FormField>
      </div>
    </Modal>
  );
}
