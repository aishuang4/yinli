"""智能审核启用条件：模型页已配置可解析的审核连接。"""

from .reviewer import PermissionReviewBinding, freeze_permission_review


def validated_review_binding() -> PermissionReviewBinding:
    """新的智能审核 Run 冻结模型页上的审核连接。"""

    return freeze_permission_review()


def permission_review_status() -> dict:
    """只检查 SmartGuard 配置、探测、已审定 prompt 与连接，不读评测记录。"""

    from .reviewer import review_connection_available

    try:
        binding = validated_review_binding()
    except ValueError as exc:
        return {"available": False, "promptVersion": "", "reason": str(exc)}
    prompt_version = binding.prompt.version
    if binding.config.probe_status != "passed":
        return {"available": False, "promptVersion": prompt_version, "reason": "审核模型尚未通过保存探测"}
    if not review_connection_available(binding):
        return {"available": False, "promptVersion": prompt_version, "reason": "审核模型连接不可用"}
    return {
        "available": True,
        "modelConfigId": binding.config.model_config_id,
        "promptVersion": prompt_version,
        "reason": "危险或不确定的操作交给这个模型判断",
    }
