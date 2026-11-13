"""下游消费方服务：登记 / 列表 / 按表聚合法。

登记按读写用途分开（read_* / write_*）；历史数据只有 columns/tasks 时按读取侧兜底，
保证旧数据与新算法 B 的读写分离推演兼容。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.consumer import Consumer
from src.models.dataset import Dataset
from src.utils.errors import AppError, not_found
from src.utils.timeutil import iso_utc


def _legacy_or_new(row_value, legacy_value) -> list:
    """新字段为空时回落到旧登记（旧登记一律按读取用途解释）。"""
    return list(row_value or []) or list(legacy_value or [])


def serialize(row: Consumer, dataset: Dataset | None = None) -> dict:
    read_columns = _legacy_or_new(row.read_columns, row.columns)
    write_columns = list(row.write_columns or [])
    read_tasks = _legacy_or_new(row.read_tasks, row.tasks)
    write_tasks = list(row.write_tasks or [])
    all_tasks = [*read_tasks, *[task for task in write_tasks if task not in read_tasks]]
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "dataset_name": dataset.full_name if dataset else None,
        "name": row.name,
        "team": row.team,
        "owner": row.owner,
        "columns": row.columns or [],
        "tasks": row.tasks or [],
        "reports": row.reports or [],
        "read_columns": read_columns,
        "write_columns": write_columns,
        "read_tasks": read_tasks,
        "write_tasks": write_tasks,
        "task_count": len(all_tasks),
        "read_task_count": len(read_tasks),
        "write_task_count": len(write_tasks),
        "report_count": len(row.reports or []),
        "refresh_cron": row.refresh_cron,
        "created_at": iso_utc(row.created_at),
    }


def list_consumers(
    db: Session,
    *,
    dataset_id: int | None = None,
    team: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Consumer)
    count_stmt = select(func.count()).select_from(Consumer)
    filters = []
    if dataset_id:
        filters.append(Consumer.dataset_id == dataset_id)
    if team:
        filters.append(Consumer.team == team)
    if q:
        like = f"%{q.strip()}%"
        filters.append(Consumer.name.ilike(like) | Consumer.owner.ilike(like) | Consumer.team.ilike(like))
    for clause in filters:
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(Consumer.dataset_id, Consumer.name).limit(limit).offset(offset)).all()
    datasets = {row.id: row for row in db.scalars(select(Dataset)).all()}
    return {
        "items": [serialize(row, datasets.get(row.dataset_id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def teams(db: Session) -> list[str]:
    return [row for row in db.scalars(select(Consumer.team).distinct().order_by(Consumer.team)).all()]


def create_consumer(db: Session, payload: dict) -> Consumer:
    dataset = db.get(Dataset, payload["dataset_id"])
    if dataset is None:
        raise not_found("dataset", payload["dataset_id"])
    exists = db.scalar(
        select(Consumer).where(Consumer.dataset_id == dataset.id, Consumer.name == payload["name"])
    )
    if exists is not None:
        raise AppError("CONFLICT", 409, {"name": payload["name"], "dataset_id": dataset.id})
    read_columns = payload.get("read_columns") or payload.get("columns") or []
    write_columns = payload.get("write_columns") or []
    read_tasks = payload.get("read_tasks") or payload.get("tasks") or []
    write_tasks = payload.get("write_tasks") or []
    reports = payload.get("reports") or []
    # 兼容旧契约：columns/tasks 同步保留合并值，旧页面与旧导出不受影响
    legacy_columns = [*read_columns, *[column for column in write_columns if column not in read_columns]]
    legacy_tasks = [*read_tasks, *[task for task in write_tasks if task not in read_tasks]]
    row = Consumer(
        dataset_id=dataset.id,
        name=payload["name"],
        team=payload.get("team") or "未分组",
        owner=payload.get("owner") or "",
        columns=legacy_columns,
        tasks=legacy_tasks,
        reports=reports,
        read_columns=read_columns,
        write_columns=write_columns,
        read_tasks=read_tasks,
        write_tasks=write_tasks,
        refresh_cron=payload.get("refresh_cron") or "0 3 * * *",
    )
    db.add(row)
    db.flush()
    return row
