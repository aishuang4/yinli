"""智能审核启用条件：模型页已配置可解析的审核模型。评测报告不阻挡使用。"""

from .evaluation import EvaluationReport, review_identity
from .reviewer import PermissionReviewBinding, freeze_permission_review


def require_review_validation(binding: PermissionReviewBinding) -> EvaluationReport:
    """可选验收：不接受客户端 passed；绑定当前租户和实际连接后重算结论。"""
    from src.storage.permission_evaluation_records import find_evaluation_report
    from src.tenancy.context import current_tenant

    identity = review_identity(binding)
    raw = find_evaluation_report(identity)
    try:
        report = EvaluationReport.model_validate(raw)
        if report.tenant_id != current_tenant().tenant_id or report.identity != identity or not report.passed:
            raise ValueError("评测未通过")
    except (ValueError, TypeError):
        raise ValueError("这份审核配置还没有通过隔离评测。") from None
    return report


def validated_review_binding() -> PermissionReviewBinding:
    """新的智能审核 Run 冻结模型页上的审核连接，不要求事先提交标注集。"""
    return freeze_permission_review()


def permission_review_status() -> dict:
    """前端只看到可用状态和中文原因，不返回 Key、证据或原始报告。"""
    try:
        binding = validated_review_binding()
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "modelConfigId": binding.config.model_config_id,
        "reason": "非只读操作交给这个模型判断；无法判断、超时或返回无效时转人工。",
    }
