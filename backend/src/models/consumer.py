"""附加实体 1：Consumer（下游消费方：任务 + 报表引用了哪些列，按读写用途登记）。

自然键 = (dataset_id, name)。

读写分离登记（字段契约变更时各自处置）：
  read_columns / read_tasks   读取侧：查询、聚合任务、报表的数据来源
  write_columns / write_tasks 写入侧：向该表 INSERT/UPDATE 的调度任务
  reports                     报表恒为读取用途
旧登记只有 columns/tasks：服务层按读取侧兜底（见 consumer_service.serialize 与 compat_engine.normalize_consumer）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class Consumer(Base):
    __tablename__ = "consumers"
    __table_args__ = (UniqueConstraint("dataset_id", "name", name="uq_consumers_dataset_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    team: Mapped[str] = mapped_column(String(80), index=True)
    owner: Mapped[str] = mapped_column(String(120), default="")
    # 旧登记：引用列名 + 名下任务（不区分读写，仅为兼容历史数据保留）
    columns: Mapped[list] = mapped_column(JSON, default=list)
    tasks: Mapped[list] = mapped_column(JSON, default=list)
    reports: Mapped[list] = mapped_column(JSON, default=list)
    # 读写分离登记（算法 B 的影响传播按用途分别推演处置）
    read_columns: Mapped[list] = mapped_column(JSON, default=list)
    write_columns: Mapped[list] = mapped_column(JSON, default=list)
    read_tasks: Mapped[list] = mapped_column(JSON, default=list)
    write_tasks: Mapped[list] = mapped_column(JSON, default=list)
    refresh_cron: Mapped[str] = mapped_column(String(40), default="0 3 * * *")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
