"""审核评测报告按租户落库；模型配置源仍只有 models.json。"""

from src.storage.database import get_json_record, upsert_json_record

NAMESPACE = "permission_review_evaluations"


def save_evaluation_report(identity: str, report: dict) -> None:
    """仅由隔离评测程序写入，没有前端自行声明通过的 API。"""
    upsert_json_record(NAMESPACE, identity, report)


def find_evaluation_report(identity: str) -> dict | None:
    """同一身份只读取当前租户的验收事实。"""
    return get_json_record(NAMESPACE, identity)
