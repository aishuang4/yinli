/** 三种审批选择；能力边界始终独立执行。 */
export type ApprovalMode = 'manual' | 'model_review' | 'full_access';

export const APPROVAL_MODE_LABELS: Record<ApprovalMode, string> = {
  manual: '人工批准', model_review: '智能审核', full_access: '完全开放',
};

export const APPROVAL_MODE_HELP: Record<ApprovalMode, string> = {
  manual: '已确认只读的操作自动执行，其余每次由你批准。',
  model_review: '非只读操作交自动审核模型，无法判断时转人工。',
  full_access: '能力范围内的操作直接执行，仍遵守文件、网络和沙箱限制。',
};
