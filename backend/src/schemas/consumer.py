from __future__ import annotations

from pydantic import BaseModel, Field


class ConsumerIn(BaseModel):
    dataset_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=120)
    team: str = Field(min_length=1, max_length=80)
    owner: str | None = Field(default=None, max_length=120)
    # 读写分离登记：读取侧引用列 / 写入侧引用列
    read_columns: list[str] = Field(default_factory=list, max_length=200)
    write_columns: list[str] = Field(default_factory=list, max_length=200)
    # 读取侧调度任务（查询 / 聚合）与写入侧调度任务（INSERT/UPDATE）
    read_tasks: list[str] = Field(default_factory=list, max_length=100)
    write_tasks: list[str] = Field(default_factory=list, max_length=100)
    # 报表恒为读取用途
    reports: list[str] = Field(default_factory=list, max_length=100)
    refresh_cron: str | None = Field(default=None, max_length=40)

    # 旧契约兼容：只有 columns/tasks 的请求按读取侧解释
    columns: list[str] = Field(default_factory=list, max_length=200)
    tasks: list[str] = Field(default_factory=list, max_length=100)
