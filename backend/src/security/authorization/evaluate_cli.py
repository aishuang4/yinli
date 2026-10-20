"""隔离审核评测 CLI：验证人工数据，保存报告，不执行被审核动作。"""

import argparse
import asyncio
import json
from pathlib import Path

from .evaluation import EvaluationBudget, evaluate_dataset
from .evaluation_data import EvaluationDataset
from .reviewer import freeze_permission_review


def main() -> None:
    """默认仅报告；--record 在所属租户记录实际评测，供新 Run 门禁使用。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-p95-ms", type=float, required=True)
    parser.add_argument("--input-price-per-million", type=float, required=True)
    parser.add_argument("--output-price-per-million", type=float, required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--tenant-id")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    dataset = EvaluationDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    budget = EvaluationBudget(maxP95Ms=args.max_p95_ms, inputPricePerMillion=args.input_price_per_million,
                              outputPricePerMillion=args.output_price_per_million, currency=args.currency)
    from src.tenancy.context import TenantContext, use_tenant

    tenant = TenantContext.hosted(args.tenant_id) if args.tenant_id else TenantContext.local()
    with use_tenant(tenant):
        binding = freeze_permission_review()
        report = asyncio.run(evaluate_dataset(dataset, binding, budget))
        public = {**report.model_dump(mode="json", by_alias=True), "metrics": report.metrics(),
                  "passed": report.passed, "limitation": "有限样本零误放不代表真实错误率为零。"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.record:
            from src.storage.permission_evaluation_records import save_evaluation_report
            save_evaluation_report(report.identity, report.model_dump(mode="json", by_alias=True))
        print(json.dumps({"passed": report.passed, "metrics": report.metrics()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
