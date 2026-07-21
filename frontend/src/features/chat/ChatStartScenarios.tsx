/** ChatStartScenarios — 新会话空状态的常用办公任务入口。 */

import { BarChart3, FilePenLine, SearchCheck, Sheet } from 'lucide-react';

const START_SCENARIOS = [
  { label: '整理文档', prompt: '请帮我梳理这份文档的结构、重点和待办事项。', icon: FilePenLine },
  { label: '分析表格', prompt: '请分析我上传的表格，提炼关键数据、趋势和异常项。', icon: BarChart3 },
  { label: '撰写材料', prompt: '请根据我提供的资料，起草一份结构清晰的办公材料。', icon: Sheet },
  { label: '调研总结', prompt: '请围绕这个主题开展调研，并输出结构化的结论与建议。', icon: SearchCheck },
];

export function ChatStartScenarios({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <div className="mt-4 grid w-full max-w-[520px] grid-cols-2 gap-2 text-left">
      {START_SCENARIOS.map((scenario) => {
        const Icon = scenario.icon;
        return (
          <button
            key={scenario.label}
            type="button"
            className="flex min-h-16 items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 text-left transition hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-hover)]"
            onClick={() => onSelect(scenario.prompt)}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--color-accent-bg)] text-[var(--color-accent-text)]">
              <Icon size={16} />
            </span>
            <span className="text-sm font-medium text-[var(--color-text-primary)]">{scenario.label}</span>
          </button>
        );
      })}
    </div>
  );
}
