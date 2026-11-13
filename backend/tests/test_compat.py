"""算法 B（兼容判定 + 影响传播 + 迁移单）单测。"""
from __future__ import annotations

from src.services import compat_engine, schema_diff

DATASET = {"db_name": "dwd", "table_name": "dwd_order_detail", "format": "iceberg", "format_label": "Iceberg"}
BASE = [
    {"name": "order_id", "type": "bigint", "nullable": False},
    {"name": "pay_channel", "type": "string", "nullable": True},
    {"name": "amount", "type": "decimal(12,2)", "nullable": True},
]
CONSUMERS = [
    {"id": 1, "name": "经营日报", "team": "经营分析", "owner": "李蔓", "columns": ["amount", "pay_channel"], "tasks": ["t_daily_gmv"], "reports": ["r_gmv_daily"]},
    {"id": 2, "name": "渠道对账", "team": "财务对账", "owner": "王戈", "columns": ["pay_channel"], "tasks": ["t_channel_recon"], "reports": ["r_channel_diff"]},
    {"id": 3, "name": "商家结算", "team": "商家侧", "owner": "赵晗", "columns": ["amount"], "tasks": ["t_settlement_calc"], "reports": []},
    {"id": 4, "name": "订单导出", "team": "BI 报表", "owner": "陈昊", "columns": ["order_id"], "tasks": ["t_order_export"], "reports": ["r_dump"]},
]


def run(target_fields, consumers=None):
    diff = schema_diff.diff_fields(BASE, target_fields)
    return compat_engine.evaluate(
        code="CR-TEST-0001",
        dataset=DATASET,
        base_version=3,
        diff=diff,
        consumers=CONSUMERS if consumers is None else consumers,
    )


def test_only_nullable_additions_is_backward_and_pass():
    result = run(BASE + [{"name": "ext_json", "type": "string", "nullable": True}])
    assert result["compat"] == "backward"
    assert result["gate"] == "pass"
    assert result["impact"]["task_count"] == 0
    assert result["risk"] == "low"


def test_drop_and_type_change_is_breaking_and_block():
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "bigint", "nullable": True},
    ]
    result = run(target)
    assert result["compat"] == "breaking"
    assert result["gate"] == "block"
    # pay_channel + amount 都被下游引用：3 个任务 / 2 张报表（order_id 那条不算影响）
    assert result["impact"]["task_count"] == 3
    assert result["impact"]["report_count"] == 2
    assert result["impact"]["consumer_count"] == 3
    assert result["risk"] == "critical"
    assert result["impact"]["affected_fields"] == ["amount", "pay_channel"]


def test_three_tasks_threshold_and_below():
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "pay_channel", "type": "string", "nullable": True},
    ]
    # 只有 经营日报 + 渠道对账 引用 pay_channel → 2 个任务，breaking 但未达 ≥3 的 critical 阈值
    result = run(target)
    assert result["compat"] == "breaking"
    assert result["impact"]["task_count"] == 2
    assert result["risk"] == "high"


def test_safe_widening_only_is_backward_without_impact():
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "pay_channel", "type": "string", "nullable": True},
        {"name": "amount", "type": "decimal(18,2)", "nullable": True},
    ]
    result = run(target)
    assert result["compat"] == "backward"
    assert result["gate"] == "pass"
    assert result["impact"]["task_count"] == 0


def test_migration_sql_contains_add_column_and_rollback():
    target = BASE + [
        {"name": "discount_rate", "type": "decimal(6,4)", "nullable": True},
        {"name": "currency", "type": "string", "nullable": False, "default": "CNY"},
    ]
    result = run(target)
    sql = result["migration_sql"]
    assert "ADD COLUMN discount_rate decimal(6,4)" in sql
    assert "ADD COLUMN currency string" in sql
    assert "-- 5) 回滚脚本" in sql
    assert "DROP COLUMN IF EXISTS discount_rate" in sql
    assert "CREATE OR REPLACE VIEW dwd.v_dwd_order_detail_compat" not in sql  # 无删列/改名时不需要兼容视图


def test_migration_sql_rollback_restores_dropped_column():
    target = [{"name": "order_id", "type": "bigint", "nullable": False},
              {"name": "amount", "type": "bigint", "nullable": True}]
    result = run(target)
    sql = result["migration_sql"]
    assert "ALTER TABLE dwd.dwd_order_detail ADD COLUMN pay_channel string;" in sql
    assert "ALTER COLUMN amount TYPE decimal(12,2)" in sql
    assert "CREATE OR REPLACE VIEW dwd.v_dwd_order_detail_compat" in sql
    assert "[remove_column] pay_channel" in sql


def test_impact_is_empty_when_no_consumers_registered():
    target = [{"name": "order_id", "type": "bigint", "nullable": False}]
    result = run(target, consumers=[])
    assert result["compat"] == "breaking"
    assert result["impact"]["task_count"] == 0
    assert result["risk"] == "high"  # breaking 但无下游任务
    assert result["gate"] == "block"


def test_evaluation_is_deterministic():
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "bigint", "nullable": True},
    ]
    assert run(target) == run(target)


# ---------------------------------------------------------------- 读写分离 + 待复核处置
INCIDENT_BASE = [
    {"name": "order_id", "type": "bigint", "nullable": False},
    {"name": "amount", "type": "decimal(12,2)", "nullable": True, "default": "0"},
]


def _run_on(base, target, consumers):
    diff = schema_diff.diff_fields(base, target)
    return compat_engine.evaluate(
        code="CR-TEST-0001",
        dataset=DATASET,
        base_version=3,
        diff=diff,
        consumers=consumers,
    )


def _amount_tighten_target():
    return [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "decimal(12,2)", "nullable": False, "default": None},
    ]


def test_amount_tighten_blocks_write_tasks_and_lists_backfill():
    consumers = [
        {"id": 10, "name": "订单宽表入湖", "team": "实时数仓", "owner": "张岩",
         "read_columns": [], "write_columns": ["order_id", "amount"],
         "read_tasks": [], "write_tasks": ["t_order_wide_ingest"], "reports": []},
        {"id": 11, "name": "订单金额回补", "team": "数据平台", "owner": "林可",
         "read_columns": [], "write_columns": ["amount"],
         "read_tasks": [], "write_tasks": ["t_amount_backfill"], "reports": []},
    ]
    result = _run_on(INCIDENT_BASE, _amount_tighten_target(), consumers)
    assert result["compat"] == "breaking"
    assert result["gate"] == "block"
    impact = result["impact"]
    # 写侧破坏列才进 write_breaking_fields；读侧无破坏
    assert impact["write_breaking_fields"] == ["amount"]
    assert impact["read_breaking_fields"] == []
    # 需补值任务按名称去重列出
    assert impact["backfill_tasks"] == ["t_order_wide_ingest", "t_amount_backfill"]
    assert impact["queries_to_fix"] == []
    # 两个写入任务都停在待复核，且带原因
    pending = {(d["name"], d["kind"], d["side"]) for d in impact["pending_dependencies"]}
    assert pending == {
        ("t_order_wide_ingest", "task", "write"),
        ("t_amount_backfill", "task", "write"),
    }
    assert all(d["review_state"] == "pending_review" and d["reasons"] for d in impact["pending_dependencies"])


def test_read_consumers_of_tightened_field_are_advisory_not_pending():
    # 经营日报读 amount：收紧可空不阻塞读取，但要进入影响面并给出空值处理提示
    consumers = [
        {"id": 12, "name": "经营日报 · GMV", "team": "经营分析", "owner": "李蔓",
         "read_columns": ["amount"], "write_columns": [],
         "read_tasks": ["t_daily_gmv"], "write_tasks": [], "reports": ["r_gmv_daily"]},
    ]
    result = _run_on(INCIDENT_BASE, _amount_tighten_target(), consumers)
    impact = result["impact"]
    assert impact["pending_count"] == 0
    assert impact["report_count"] == 1 and impact["reports"] == ["r_gmv_daily"]
    advisory_names = {d["name"] for d in impact["advisory_dependencies"]}
    assert {"t_daily_gmv", "r_gmv_daily"} <= advisory_names
    assert all("必填" in d["reasons"][0] for d in impact["advisory_dependencies"])


def test_read_breaking_column_marks_queries_and_reports_pending():
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "bigint", "nullable": True},
    ]
    result = run(target)  # 用模块内 CONSUMERS：含 pay_channel 删除与 amount 非安全改型
    impact = result["impact"]
    # 丢列读取 / 非安全类型 → 需改查询
    assert "t_daily_gmv" in impact["queries_to_fix"]
    assert "t_channel_recon" in impact["queries_to_fix"]
    pending_names = {d["name"] for d in impact["pending_dependencies"]}
    # 报表 r_gmv_daily 引用了被影响列，必须进入待复核
    assert "r_gmv_daily" in pending_names
    assert all(d["review_state"] == "pending_review" for d in impact["pending_dependencies"])


def test_tasks_and_reports_dedup_across_consumers():
    consumers = [
        {"id": 20, "name": "甲", "team": "A", "owner": "x",
         "read_columns": ["amount"], "write_columns": [],
         "read_tasks": ["t_shared"], "write_tasks": [], "reports": ["r_shared"]},
        {"id": 21, "name": "乙", "team": "B", "owner": "y",
         "read_columns": ["amount"], "write_columns": ["amount"],
         "read_tasks": ["t_shared"], "write_tasks": ["t_shared"], "reports": ["r_shared"]},
    ]
    result = _run_on(INCIDENT_BASE, _amount_tighten_target(), consumers)
    impact = result["impact"]
    # 同名任务 / 报表跨消费方只计一次
    assert impact["task_count"] == 1
    assert impact["report_count"] == 1
    assert impact["tasks"] == ["t_shared"]
    # 同一任务只在待复核清单出现一次（写侧命中优先）
    assert len([d for d in impact["pending_dependencies"] if d["name"] == "t_shared"]) == 1


def test_legacy_consumer_registration_falls_back_to_read_side():
    # 旧登记只有 columns/tasks：按读取侧解释，行为与显式 read_* 一致
    legacy = [
        {"id": 30, "name": "旧消费方", "team": "经营分析", "owner": "李蔓",
         "columns": ["pay_channel"], "tasks": ["t_legacy_read"], "reports": ["r_legacy"]},
    ]
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "decimal(12,2)", "nullable": True, "default": "0"},
    ]
    result = run(target, consumers=legacy)  # pay_channel 被删
    impact = result["impact"]
    assert impact["queries_to_fix"] == ["t_legacy_read"]
    assert impact["backfill_tasks"] == []
    assert {d["name"] for d in impact["pending_dependencies"]} == {"t_legacy_read", "r_legacy"}


def test_new_required_column_marks_all_write_tasks_for_backfill():
    # 新增必填无默认列：即使写入任务登记的引用列里没有该新列，也要补值
    base = [{"name": "order_id", "type": "bigint", "nullable": False}]
    target = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "decimal(12,2)", "nullable": False, "default": None},
    ]
    consumers = [
        {"id": 50, "name": "入湖", "team": "实时数仓", "owner": "张岩",
         "read_columns": ["order_id"], "write_columns": ["order_id"],
         "read_tasks": ["t_read"], "write_tasks": ["t_ingest"], "reports": []},
    ]
    diff = schema_diff.diff_fields(base, target)
    result = compat_engine.evaluate(
        code="CR-TEST-0001", dataset=DATASET, base_version=3, diff=diff, consumers=consumers
    )
    impact = result["impact"]
    assert impact["backfill_tasks"] == ["t_ingest"]
    assert "amount" in impact["write_breaking_fields"]
    pending = {d["name"]: d for d in impact["pending_dependencies"]}
    assert "t_ingest" in pending and pending["t_ingest"]["side"] == "write"
    # 读取任务不被新增列阻塞
    assert "t_read" not in pending
    assert impact["queries_to_fix"] == []


def test_safe_contract_only_change_is_backward_without_pending():
    # 仅放宽可空 + 调整默认值：backward / pass，无待复核
    target = [
        {"name": "order_id", "type": "bigint", "nullable": True, "default": None},
        {"name": "pay_channel", "type": "string", "nullable": True, "default": "UNKNOWN"},
        {"name": "amount", "type": "decimal(12,2)", "nullable": True, "default": "0"},
    ]
    result = run(target)
    assert result["compat"] == "backward"
    assert result["gate"] == "pass"
    assert result["impact"]["pending_count"] == 0


def test_migration_sql_covers_contract_clauses_and_pending_list():
    result = _run_on(INCIDENT_BASE, _amount_tighten_target(), [
        {"id": 40, "name": "订单宽表入湖", "team": "实时数仓", "owner": "张岩",
         "read_columns": [], "write_columns": ["amount"],
         "read_tasks": [], "write_tasks": ["t_order_wide_ingest"], "reports": []},
    ])
    sql = result["migration_sql"]
    assert "SET NOT NULL" in sql
    assert "DROP DEFAULT" in sql  # amount 原默认 0 被取消
    assert "DROP NOT NULL" in sql  # 回滚脚本还原可空
    assert "SET DEFAULT 0" in sql  # 回滚脚本还原默认值
    assert "待复核" in sql and "t_order_wide_ingest" in sql
    # amount 是数值列，回填字面量为 0
    assert "UPDATE dwd.dwd_order_detail SET amount = 0 WHERE amount IS NULL" in sql


def test_backfill_literal_is_type_aware():
    assert compat_engine._backfill_literal("amount", "decimal(12,2)", "0") == "0"
    assert compat_engine._backfill_literal("amount", "decimal(12,2)", None) == "0"
    assert compat_engine._backfill_literal("name", "string", None) == "''"
    assert compat_engine._backfill_literal("flag", "boolean", None) == "false"
    assert compat_engine._backfill_literal("created_at", "timestamp", None) == "CURRENT_TIMESTAMP"
    assert compat_engine._backfill_literal("dt", "date", None) == "CURRENT_DATE"
