"""算法 B：兼容性判定 + 影响传播 + 迁移单生成（纯函数，无 DB / 无网络，可单测）。

判定口径（README「核心算法与口径」同步）：

兼容级别 compat
  breaking : diff 中存在任一 breaking 条目（删列 / 重命名 / 非安全类型变更 /
             新增非空无默认 / 收紧可空 / 非空列取消默认值）
  backward : 只有新增（含安全提升、放宽可空、默认值变更），旧读者读新表不受影响
  forward  : 只有删除 / 重命名（没有新增），新读者读旧表不受影响
  full     : 既有新增又有删除，但全部为安全变更

下游登记（Consumer）按读写用途分开：
  read_columns / read_tasks   —— 读取该表的查询与调度任务（SELECT 侧）
  write_columns / write_tasks —— 向该表写入的调度任务（INSERT/UPDATE 侧）
  reports                     —— 报表恒为读取用途
  旧登记只有 columns/tasks：读取侧兜底（tasks 默认读、reports 默认读），保证幂等兼容。

影响传播 impact（按任务名 / 报表名跨消费方并集去重）：
  读侧破坏列 = remove_column / rename_column / 非安全 type_change 的字段
      → 引用它的读取任务与报表「丢列读取」，停在待复核，需改查询。
  写侧破坏列 = remove_column / rename_column / 非安全 type_change /
               nullable_tighten / 非空列 default_remove / 新增非空无默认
      → 写入任务「缺值写入」，停在待复核，需补值或改写入语句。
  安全契约变更（放宽可空、默认值变更、可空列取消默认、安全类型提升）只提示、不阻塞。

风险 risk
  breaking 且受影响任务 >= 3  → critical（batch-plan 验收口径）
  breaking 且受影响任务  < 3  → high
  非 breaking：任务 >= 8 → critical；>= 3 → high；>= 1 → medium；否则 low

门禁 gate
  compat == breaking → block，其余 pass
"""
from __future__ import annotations

from src.constants.enums import (
    CRITICAL_TASK_THRESHOLD,
    ChangeType,
    Compat,
    Gate,
)

IMPACT_CHANGE_TYPES = {
    ChangeType.REMOVE_COLUMN.value,
    ChangeType.RENAME_COLUMN.value,
    ChangeType.TYPE_CHANGE.value,
}

# 读取侧会「丢列 / 读不了」的破坏类型
READ_BREAKING_TYPES = {
    ChangeType.REMOVE_COLUMN.value,
    ChangeType.RENAME_COLUMN.value,
    ChangeType.TYPE_CHANGE.value,
}

# 写入侧会「缺值 / 写不进」的破坏类型（读侧破坏写侧同样成立，另加契约收紧）
WRITE_BREAKING_TYPES = READ_BREAKING_TYPES | {
    ChangeType.NULLABLE_TIGHTEN.value,
    ChangeType.DEFAULT_REMOVE.value,
    ChangeType.ADD_COLUMN.value,
}

REVIEW_PENDING = "pending_review"
REVIEW_CONFIRMED = "confirmed"

READ_SIDE = "read"
WRITE_SIDE = "write"


def _is_read_breaking(item: dict) -> bool:
    return item.get("breaking") and item.get("change_type") in READ_BREAKING_TYPES


def _is_write_breaking(item: dict) -> bool:
    if not item.get("breaking"):
        return False
    change_type = item.get("change_type")
    if change_type not in WRITE_BREAKING_TYPES:
        return False
    # add_column 只有「非空且无默认值」才是写侧破坏，_add_is_breaking 已据此打标
    return True


def _breaking_map(diff: list[dict], predicate) -> dict[str, dict]:
    """字段名 → 首个命中的破坏条目（同字段多条契约变更时保留破坏性最强的第一条）。"""
    mapping: dict[str, dict] = {}
    for item in diff or []:
        if predicate(item) and item["field"] not in mapping:
            mapping[item["field"]] = item
    return mapping


def affected_field_names(diff: list[dict]) -> list[str]:
    """全部破坏性条目涉及的列（读侧 + 写侧），按 diff 顺序去重。"""
    names: list[str] = []
    for item in diff or []:
        if item.get("breaking") and item["field"] not in names:
            names.append(item["field"])
    return names


def resolve_compat(diff: list[dict]) -> str:
    if not diff:
        return Compat.FULL.value
    breaking = any(item.get("breaking") for item in diff)
    if breaking:
        return Compat.BREAKING.value
    has_add = any(item.get("change_type") == ChangeType.ADD_COLUMN.value for item in diff)
    has_drop = any(
        item.get("change_type") in (ChangeType.REMOVE_COLUMN.value, ChangeType.RENAME_COLUMN.value)
        for item in diff
    )
    if has_add and has_drop:
        return Compat.FULL.value
    if has_drop:
        return Compat.FORWARD.value
    return Compat.BACKWARD.value


def normalize_consumer(consumer: dict) -> dict:
    """把下游登记归一为读写分离结构；旧登记（只有 columns/tasks）按读取侧兜底。"""
    legacy_columns = [str(column) for column in (consumer.get("columns") or [])]
    legacy_tasks = [str(task) for task in (consumer.get("tasks") or [])]
    reports = [str(report) for report in (consumer.get("reports") or [])]
    read_columns = [str(column) for column in (consumer.get("read_columns") or [])] or legacy_columns
    write_columns = [str(column) for column in (consumer.get("write_columns") or [])]
    read_tasks = [str(task) for task in (consumer.get("read_tasks") or [])] or legacy_tasks
    write_tasks = [str(task) for task in (consumer.get("write_tasks") or [])]
    return {
        "id": consumer.get("id"),
        "name": consumer.get("name"),
        "team": consumer.get("team"),
        "owner": consumer.get("owner"),
        "read_columns": read_columns,
        "write_columns": write_columns,
        "read_tasks": read_tasks,
        "write_tasks": write_tasks,
        "reports": reports,
    }


def _rename_targets(diff: list[dict]) -> dict[str, str]:
    return {
        item["field"]: item.get("new_name")
        for item in diff or []
        if item.get("change_type") == ChangeType.RENAME_COLUMN.value and item.get("new_name")
    }


def _reason_text(side: str, item: dict, renames: dict[str, str]) -> str:
    change_type = item.get("change_type")
    field = item["field"]
    if side == READ_SIDE:
        if change_type == ChangeType.REMOVE_COLUMN.value:
            return f"列 {field} 已删除，读取查询会报列不存在（丢列读取），需改查询"
        if change_type == ChangeType.RENAME_COLUMN.value:
            return f"列 {field} 已重命名为 {renames.get(field, item.get('new_name'))}，旧列名读取会丢列，需改查询"
        return (
            f"列 {field} 类型 {item.get('from_type')} → {item.get('to_type')} 为非安全变更，"
            "历史分区读取会失败或丢精度，需改查询"
        )
    if change_type == ChangeType.REMOVE_COLUMN.value:
        return f"列 {field} 已删除，写入语句仍带该列会失败，需改写入并补值"
    if change_type == ChangeType.RENAME_COLUMN.value:
        return f"列 {field} 已重命名为 {renames.get(field, item.get('new_name'))}，旧列名写入会失败，需改写入语句"
    if change_type == ChangeType.TYPE_CHANGE.value:
        return (
            f"列 {field} 类型 {item.get('from_type')} → {item.get('to_type')} 为非安全变更，"
            "写入值需按新类型转换补值"
        )
    if change_type == ChangeType.NULLABLE_TIGHTEN.value:
        return f"列 {field} 收紧为必填且写入侧无兜底，缺值写入会失败，需在写入前补值"
    if change_type == ChangeType.DEFAULT_REMOVE.value:
        return f"列 {field} 已取消默认值且为必填，未显式赋值的写入会失败，需补值"
    if change_type == ChangeType.ADD_COLUMN.value:
        return f"新增必填列 {field} 且无默认值，存量写入语句未给值会失败，需补值"
    return f"列 {field} 存在破坏性契约变更，写入侧需补值"


def _dedup_append(bucket: list, value) -> None:
    if value not in bucket:
        bucket.append(value)


def propagate_impact(diff: list[dict], consumers: list[dict]) -> dict:
    """按 Consumer 的读写引用列推演影响面，给出每个任务 / 报表的处置与待复核原因。"""
    read_breaking = _breaking_map(diff, _is_read_breaking)
    write_breaking = _breaking_map(diff, _is_write_breaking)
    renames = _rename_targets(diff)
    all_breaking_fields = set(affected_field_names(diff))
    # 新增的必填无默认列：任何向该表写入的任务（即便登记列里还没有这列）都需要补值
    new_required_columns = [
        item["field"]
        for item in diff or []
        if item.get("change_type") == ChangeType.ADD_COLUMN.value and item.get("breaking")
    ]

    affected: list[dict] = []
    read_tasks: list[str] = []
    write_tasks: list[str] = []
    reports: list[str] = []
    queries_to_fix: list[str] = []       # 需改查询：读侧破坏命中的读取任务（去重）
    backfill_tasks: list[str] = []       # 需补值：写侧破坏命中的写入任务（去重）
    pending_dependencies: list[dict] = []  # 停在待复核的任务 / 报表（去重）
    advisory_dependencies: list[dict] = []  # 仅提示（安全契约变更）的任务 / 报表（去重）
    seen_pending: set[tuple[str, str]] = set()
    seen_advisory: set[tuple[str, str]] = set()

    def queue_dependency(kind: str, name: str, side: str, reasons: list[str], consumer_name: str) -> None:
        key = (kind, name)
        payload = {
            "kind": kind,           # task / report
            "name": name,
            "side": side,           # read / write
            "review_state": REVIEW_PENDING,
            "reasons": reasons,
            "consumer": consumer_name,
        }
        if key not in seen_pending:
            seen_pending.add(key)
            pending_dependencies.append(payload)

    def queue_advisory(kind: str, name: str, side: str, reasons: list[str], consumer_name: str) -> None:
        key = (kind, name)
        if key in seen_pending or key in seen_advisory:
            return
        seen_advisory.add(key)
        advisory_dependencies.append(
            {
                "kind": kind,
                "name": name,
                "side": side,
                "review_state": REVIEW_CONFIRMED,
                "reasons": reasons,
                "consumer": consumer_name,
            }
        )

    for raw in consumers or []:
        consumer = normalize_consumer(raw)
        read_hits = [column for column in consumer["read_columns"] if column in read_breaking]
        write_hits = [column for column in consumer["write_columns"] if column in write_breaking]
        # 新增必填无默认列对所有写入任务生效，即使该任务登记的引用列里还没有新列
        implicit_new_hits = [
            column for column in new_required_columns if column not in consumer["write_columns"]
        ]
        write_break_hits = [*write_hits, *implicit_new_hits]
        # 安全契约变更命中（不阻塞，仅提示下游知悉）
        read_safe_hits = [
            column
            for column in consumer["read_columns"]
            if column in all_breaking_fields and column not in read_breaking
        ]
        write_safe_hits = [
            column
            for column in consumer["write_columns"]
            if column in all_breaking_fields and column not in write_breaking
        ]
        if not (read_hits or write_break_hits or read_safe_hits or write_safe_hits):
            continue

        read_reasons = {
            column: _reason_text(READ_SIDE, read_breaking[column], renames) for column in read_hits
        }
        write_reasons = {
            column: _reason_text(WRITE_SIDE, write_breaking[column], renames) for column in write_break_hits
        }

        def _advisory_text(column: str, side: str) -> str:
            item = next((entry for entry in (diff or []) if entry["field"] == column), None)
            change_type = (item or {}).get("change_type")
            if change_type == ChangeType.NULLABLE_TIGHTEN.value:
                return (
                    f"列 {column} 收紧为必填：{side}侧不阻塞（读取不报错），"
                    "但存量行仍可能为 NULL，查询与报表需保留空值处理"
                )
            if change_type == ChangeType.DEFAULT_REMOVE.value:
                return f"列 {column} 取消默认值：{side}侧不阻塞，仅写入侧失去兜底，读取语义不变"
            return f"列 {column} 为安全契约变更（放宽可空 / 默认值调整 / 安全类型提升），按提示确认即可"

        safe_reasons = [
            _advisory_text(column, "读")
            for column in dict.fromkeys([*read_safe_hits, *write_safe_hits])
        ]

        consumer_read_tasks: list[dict] = []
        consumer_write_tasks: list[dict] = []
        consumer_reports: list[dict] = []

        for task in consumer["read_tasks"]:
            _dedup_append(read_tasks, task)
            if read_hits:
                reasons = [read_reasons[column] for column in read_hits]
                consumer_read_tasks.append(
                    {"name": task, "side": READ_SIDE, "review_state": REVIEW_PENDING, "reasons": reasons}
                )
                _dedup_append(queries_to_fix, task)
                queue_dependency("task", task, READ_SIDE, reasons, consumer["name"])
            else:
                consumer_read_tasks.append(
                    {"name": task, "side": READ_SIDE, "review_state": REVIEW_CONFIRMED, "reasons": safe_reasons}
                )
                if safe_reasons:
                    queue_advisory("task", task, READ_SIDE, safe_reasons, consumer["name"])

        for task in consumer["write_tasks"]:
            _dedup_append(write_tasks, task)
            if write_break_hits:
                reasons = [write_reasons[column] for column in write_break_hits]
                consumer_write_tasks.append(
                    {"name": task, "side": WRITE_SIDE, "review_state": REVIEW_PENDING, "reasons": reasons}
                )
                _dedup_append(backfill_tasks, task)
                queue_dependency("task", task, WRITE_SIDE, reasons, consumer["name"])
            else:
                consumer_write_tasks.append(
                    {"name": task, "side": WRITE_SIDE, "review_state": REVIEW_CONFIRMED, "reasons": safe_reasons}
                )
                if safe_reasons:
                    queue_advisory("task", task, WRITE_SIDE, safe_reasons, consumer["name"])

        for report in consumer["reports"]:
            _dedup_append(reports, report)
            if read_hits:
                reasons = [read_reasons[column] for column in read_hits]
                consumer_reports.append(
                    {"name": report, "side": READ_SIDE, "review_state": REVIEW_PENDING, "reasons": reasons}
                )
                queue_dependency("report", report, READ_SIDE, reasons, consumer["name"])
            else:
                consumer_reports.append(
                    {"name": report, "side": READ_SIDE, "review_state": REVIEW_CONFIRMED, "reasons": safe_reasons}
                )
                if safe_reasons:
                    queue_advisory("report", report, READ_SIDE, safe_reasons, consumer["name"])

        affected.append(
            {
                "id": consumer["id"],
                "name": consumer["name"],
                "team": consumer["team"],
                "owner": consumer["owner"],
                # 命中列含阻塞列（含新增必填列的隐式命中）与仅提示列
                "hit_columns": sorted(
                    dict.fromkeys([*read_hits, *write_break_hits, *read_safe_hits, *write_safe_hits])
                ),
                "read_hit_columns": read_hits,
                "write_hit_columns": write_break_hits,
                "implicit_new_columns": implicit_new_hits,
                "advisory_columns": sorted(dict.fromkeys([*read_safe_hits, *write_safe_hits])),
                "read_tasks": consumer["read_tasks"],
                "write_tasks": consumer["write_tasks"],
                "tasks": [*consumer["read_tasks"], *consumer["write_tasks"]],
                "reports": consumer["reports"],
                "task_dispositions": [*consumer_read_tasks, *consumer_write_tasks],
                "report_dispositions": consumer_reports,
                "review_state": REVIEW_PENDING if (read_hits or write_break_hits) else REVIEW_CONFIRMED,
            }
        )

    affected.sort(key=lambda item: (item["team"] or "", item["name"] or ""))
    all_tasks = [*read_tasks, *[task for task in write_tasks if task not in read_tasks]]
    return {
        "affected_fields": sorted(all_breaking_fields),
        "read_breaking_fields": sorted(read_breaking),
        "write_breaking_fields": sorted(write_breaking),
        "affected_consumers": affected,
        "consumer_count": len(affected),
        # 任务 / 报表按名称跨消费方并集去重
        "tasks": all_tasks,
        "reports": reports,
        "read_tasks": read_tasks,
        "write_tasks": write_tasks,
        "task_count": len(all_tasks),
        "report_count": len(reports),
        # 按读写用途列出的处置清单
        "queries_to_fix": queries_to_fix,
        "backfill_tasks": backfill_tasks,
        "pending_count": len(pending_dependencies),
        "pending_dependencies": pending_dependencies,
        "advisory_dependencies": advisory_dependencies,
    }


def resolve_risk(compat: str, impact: dict) -> str:
    task_count = int(impact.get("task_count") or 0)
    if compat == Compat.BREAKING.value:
        return "critical" if task_count >= CRITICAL_TASK_THRESHOLD else "high"
    if task_count >= 8:
        return "critical"
    if task_count >= CRITICAL_TASK_THRESHOLD:
        return "high"
    if task_count >= 1:
        return "medium"
    return "low"


def resolve_gate(compat: str) -> str:
    return Gate.BLOCK.value if compat == Compat.BREAKING.value else Gate.PASS.value


def _table(db_name: str, table_name: str) -> str:
    return f"{db_name}.{table_name}"


def _backfill_literal(field_name: str, field_type: str | None, previous_default) -> str:
    """收紧必填前的存量 NULL 回填值：优先用原默认值，否则按类型给安全兜底。"""
    if previous_default not in (None, ""):
        return str(previous_default)
    kind = (field_type or "").lower()
    if kind.startswith(("decimal", "bigint", "int", "tinyint", "smallint", "double", "float")):
        return "0"
    if kind == "boolean":
        return "false"
    if kind in ("timestamp", "date"):
        return "CURRENT_TIMESTAMP" if kind == "timestamp" else "CURRENT_DATE"
    return "''"  # string 及其它类型兜底为空串


def build_migration_sql(
    *,
    code: str,
    dataset: dict,
    base_version: int,
    diff: list[dict],
    compat: str,
    gate: str,
    impact: dict,
) -> str:
    """生成迁移单 SQL：前置校验 / 正向迁移 / 兼容视图 / 阻塞项 / 回滚脚本。"""
    db_name = dataset.get("db_name") or "dwd"
    table_name = dataset.get("table_name") or "unknown_table"
    table = _table(db_name, table_name)
    lines: list[str] = []
    add = [item for item in diff if item["change_type"] == ChangeType.ADD_COLUMN.value]
    drop = [item for item in diff if item["change_type"] == ChangeType.REMOVE_COLUMN.value]
    rename = [item for item in diff if item["change_type"] == ChangeType.RENAME_COLUMN.value]
    retype = [item for item in diff if item["change_type"] == ChangeType.TYPE_CHANGE.value]
    tighten = [item for item in diff if item["change_type"] == ChangeType.NULLABLE_TIGHTEN.value]
    relax = [item for item in diff if item["change_type"] == ChangeType.NULLABLE_RELAX.value]
    default_remove = [item for item in diff if item["change_type"] == ChangeType.DEFAULT_REMOVE.value]
    default_change = [item for item in diff if item["change_type"] == ChangeType.DEFAULT_CHANGE.value]

    lines.append(f"-- 迁移单 {code} · {table}（{dataset.get('format_label') or dataset.get('format', 'iceberg')} 表）")
    lines.append(f"-- 基线版本 v{base_version}；兼容判定 {compat}；门禁 {gate}")
    lines.append(f"-- 影响面：{impact.get('consumer_count', 0)} 个下游 / {impact.get('task_count', 0)} 个任务 / "
                 f"{impact.get('report_count', 0)} 张报表；待复核 {impact.get('pending_count', 0)} 项")
    lines.append("-- 执行顺序：先建兼容视图 → 再改表 → 下游切换完成后回收旧列")
    lines.append("")
    lines.append("-- 1) 前置校验（有数据则先与下游确认）")
    for item in drop + [entry for entry in retype if entry["breaking"]]:
        lines.append(f"SELECT count(*) AS non_null_{item['field']} FROM {table} WHERE {item['field']} IS NOT NULL;")
    for item in tighten:
        lines.append(
            f"SELECT count(*) AS null_{item['field']} FROM {table} WHERE {item['field']} IS NULL;  "
            "-- 收紧为必填前先按下行回填值确认口径"
        )
    if not (drop or [entry for entry in retype if entry["breaking"]] or tighten):
        lines.append(f"SELECT count(*) AS rows_total FROM {table};")
    lines.append("")

    lines.append("-- 2) 正向迁移")
    if add:
        for item in add:
            suffix = "" if item.get("to_nullable", True) else " NOT NULL"
            lines.append(f"ALTER TABLE {table} ADD COLUMN {item['field']} {item['to_type']}{suffix};")
    for item in rename:
        lines.append(f"ALTER TABLE {table} RENAME COLUMN {item['field']} TO {item['new_name']};")
    for item in retype:
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} TYPE {item['to_type']};")
    for item in tighten:
        fill = _backfill_literal(item["field"], item.get("to_type"), item.get("from_default"))
        lines.append(f"UPDATE {table} SET {item['field']} = {fill} WHERE {item['field']} IS NULL;  -- 回填后再收紧")
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} SET NOT NULL;")
    for item in relax:
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} DROP NOT NULL;")
    for item in default_remove:
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} DROP DEFAULT;")
    for item in default_change:
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} SET DEFAULT {item['to_default']};")
    for item in drop:
        lines.append(f"-- ALTER TABLE {table} DROP COLUMN {item['field']};  -- 待下游切换完成后单独执行")
    if not diff:
        lines.append(f"-- 无字段变化，仅登记版本：{table}")
    lines.append("")

    lines.append("-- 3) 兼容视图（给未切换的下游读旧列名）")
    projections: list[str] = []
    for item in rename:
        projections.append(f"CAST({item['new_name']} AS {item['to_type']}) AS {item['field']}")
    for item in drop:
        projections.append(f"CAST(NULL AS {item['from_type']}) AS {item['field']}")
    if projections:
        lines.append(f"CREATE OR REPLACE VIEW {db_name}.v_{table_name}_compat AS")
        lines.append("SELECT")
        lines.append("    *,")
        lines.append("    " + ",\n    ".join(projections))
        lines.append(f"FROM {table};")
        lines.append(f"-- 下游临时改读 {db_name}.v_{table_name}_compat")
    else:
        lines.append(f"-- 本次无需兼容视图（{table}）")
    lines.append("")

    lines.append("-- 4) 阻塞项（待复核下游处置完成后方可放行）")
    blockers = [item for item in diff if item["breaking"]]
    if blockers:
        for item in blockers:
            lines.append(f"-- [{item['change_type']}] {item['field']}：{item['detail']}")
        for dependency in impact.get("pending_dependencies", []):
            lines.append(
                f"-- 待复核 · {dependency['kind']} {dependency['name']}（{dependency['side']}）："
                + "；".join(dependency["reasons"])
            )
    else:
        lines.append("-- 无阻塞项，门禁可直接放行")
    lines.append("")

    lines.append("-- 5) 回滚脚本（与正向迁移同一事务；按逆序执行）")
    for item in reversed(default_change):
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} SET DEFAULT {item['from_default']};")
    for item in reversed(default_remove):
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} SET DEFAULT {item['from_default']};")
    for item in reversed(relax):
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} SET NOT NULL;")
    for item in reversed(tighten):
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} DROP NOT NULL;")
    for item in reversed(retype):
        lines.append(f"ALTER TABLE {table} ALTER COLUMN {item['field']} TYPE {item['from_type']};")
    for item in reversed(rename):
        lines.append(f"ALTER TABLE {table} RENAME COLUMN {item['new_name']} TO {item['field']};")
    for item in reversed(drop):
        lines.append(f"ALTER TABLE {table} ADD COLUMN {item['field']} {item['from_type']};")
    for item in reversed(add):
        lines.append(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {item['field']};")
    if projections:
        lines.append(f"DROP VIEW IF EXISTS {db_name}.v_{table_name}_compat;")
    return "\n".join(lines) + "\n"


def evaluate(
    *,
    code: str,
    dataset: dict,
    base_version: int,
    diff: list[dict],
    consumers: list[dict],
) -> dict:
    """算法 B 总入口：返回 compat / risk / gate / impact / migration_sql。"""
    compat = resolve_compat(diff)
    impact = propagate_impact(diff, consumers)
    risk = resolve_risk(compat, impact)
    gate = resolve_gate(compat)
    sql = build_migration_sql(
        code=code,
        dataset=dataset,
        base_version=base_version,
        diff=diff,
        compat=compat,
        gate=gate,
        impact=impact,
    )
    return {"compat": compat, "risk": risk, "gate": gate, "impact": impact, "migration_sql": sql}
