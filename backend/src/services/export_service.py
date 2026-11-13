"""导出服务：兼容检测清单 CSV（字段判定 + 待复核依赖）+ 迁移单 SQL。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CHANGE_TYPE_LABELS,
    COMPAT_LABELS,
    GATE_LABELS,
    USAGE_LABELS,
)
from src.models.change_request import ChangeRequest
from src.models.compat_check import CompatCheck
from src.models.dataset import Dataset
from src.utils.csv_export import to_csv
from src.utils.errors import not_found


def checks_csv(db: Session, request_id: int | None = None) -> bytes:
    stmt = select(CompatCheck)
    if request_id:
        stmt = stmt.where(CompatCheck.request_id == request_id)
    checks = db.scalars(stmt.order_by(CompatCheck.request_id, CompatCheck.id)).all()
    requests = {row.id: row for row in db.scalars(select(ChangeRequest)).all()}
    datasets = {row.id: row for row in db.scalars(select(Dataset)).all()}
    headers = [
        "变更单号", "表", "字段", "变更类型", "是否破坏性", "兼容判定", "门禁", "说明", "检测时间",
    ]
    rows = []
    for item in checks:
        request = requests.get(item.request_id)
        dataset = datasets.get(request.dataset_id) if request else None
        rows.append(
            [
                request.code if request else "",
                dataset.full_name if dataset else "",
                item.field,
                CHANGE_TYPE_LABELS.get(item.change_type, item.change_type),
                "是" if item.breaking else "否",
                COMPAT_LABELS.get(request.compat, request.compat) if request else "",
                GATE_LABELS.get(request.gate, request.gate) if request else "",
                item.detail,
                item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
            ]
        )

    # 追加「待复核依赖」段：丢列读取 / 缺值写入的任务与报表，按任务与报表去重
    if request_id:
        target_requests = [requests[request_id]] if request_id in requests else []
    else:
        target_requests = [requests[check.request_id] for check in checks]
        target_requests = list({request.id: request for request in target_requests}.values())
    for request in target_requests:
        impact = request.impact or {}
        for dependency in impact.get("pending_dependencies", []):
            rows.append(
                [
                    request.code,
                    (datasets.get(request.dataset_id).full_name if datasets.get(request.dataset_id) else ""),
                    dependency.get("name", ""),
                    f"待复核依赖·{USAGE_LABELS.get(dependency.get('side'), dependency.get('side'))}"
                    f"·{'任务' if dependency.get('kind') == 'task' else '报表'}",
                    "是",
                    COMPAT_LABELS.get(request.compat, request.compat),
                    GATE_LABELS.get(request.gate, request.gate),
                    "；".join(dependency.get("reasons") or []),
                    request.checked_at.strftime("%Y-%m-%d %H:%M") if request.checked_at else "",
                ]
            )
    return to_csv(headers, rows)


def migration_sql(db: Session, request_id: int) -> tuple[str, str]:
    request = db.get(ChangeRequest, request_id)
    if request is None:
        raise not_found("change_request", request_id)
    dataset = db.get(Dataset, request.dataset_id)
    filename = f"{request.code}_{dataset.table_name if dataset else 'table'}_migration.sql"
    return request.migration_sql or "-- 该变更尚未生成迁移单\n", filename
