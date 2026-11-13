/** 无框架 UI 组件：表格 / 徽标 / 抽屉 / 空态 / 统计条 / 图表 / toast。 */

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return esc(iso);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function badge(tone, text) {
  return `<span class="badge ${esc(tone)}">${esc(text)}</span>`;
}

export function statStrip(items) {
  return `<div class="stat-strip">${items
    .map(
      (item) => `<div class="stat">
        <span class="k">${esc(item.label)}</span>
        <span class="v">${esc(item.value)}</span>
        <span class="hint">${esc(item.hint || '')}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function emptyState({ title, hint, actionText, actionId }) {
  return `<div class="empty" data-testid="empty-state">
    <div class="mark">${esc(title)}</div>
    <p>${esc(hint || '')}</p>
    ${actionText ? `<button class="btn ghost" id="${esc(actionId || 'empty-action')}">${esc(actionText)}</button>` : ''}
  </div>`;
}

export function errorBox(message, retryId = 'retry-btn') {
  return `<div class="error-box"><span>${esc(message)}</span><button class="btn sm ghost" id="${esc(retryId)}">重试</button></div>`;
}

export function table({ columns, rows, rowId, empty }) {
  if (!rows || rows.length === 0) return emptyState(empty || { title: '暂无记录', hint: '调整筛选条件或先发起一次运行。' });
  const head = columns.map((col) => `<th>${esc(col.label)}</th>`).join('');
  const body = rows
    .map(
      (row) => `<tr data-id="${esc(row.id)}" ${rowId ? `id="${esc(rowId(row))}"` : ''}>
        ${columns.map((col) => `<td class="${esc(col.className || '')}">${col.render ? col.render(row) : esc(row[col.key])}</td>`).join('')}
      </tr>`,
    )
    .join('');
  return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function drawer({ title, subtitle, body, footer }) {
  const backdrop = document.createElement('div');
  backdrop.className = 'drawer-backdrop';
  backdrop.innerHTML = `<aside class="drawer" role="dialog" aria-modal="true">
    <div class="drawer-head">
      <div>
        <h2>${esc(title)}</h2>
        <div class="mono muted">${esc(subtitle || '')}</div>
      </div>
      <button class="btn sm ghost" data-close>关闭</button>
    </div>
    <div class="drawer-body">${body}</div>
    ${footer ? `<div class="drawer-foot">${footer}</div>` : ''}
  </aside>`;
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => {
    if (event.key === 'Escape') close();
  };
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop || event.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(backdrop);
  return { root: backdrop, close };
}

export function kv(items) {
  return `<dl class="kv">${items
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value === undefined || value === null ? '—' : value}</dd>`)
    .join('')}</dl>`;
}

export function barList(items, tone = '') {
  const max = Math.max(1, ...items.map((item) => item.count));
  return `<div class="bars">${items
    .map(
      (item) => `<div class="bar-row">
        <span>${esc(item.label)}</span>
        <div class="bar-track"><div class="bar-fill ${esc(tone)}" style="width:${Math.round((item.count / max) * 100)}%"></div></div>
        <span class="mono">${esc(item.count)}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function trendChart(points) {
  const max = Math.max(1, ...points.map((point) => point.runs));
  return `<div class="trend">${points
    .map(
      (point) => `<div class="col" title="${esc(point.date)} · ${esc(point.runs)} 次">
        <div class="stem" style="height:${Math.max(2, Math.round((point.runs / max) * 68))}px"></div>
        <span class="lbl">${esc(point.date.slice(5))}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function field({ label, name, type = 'text', value = '', placeholder = '', options = null, attrs = '' }) {
  const control = options
    ? `<select name="${esc(name)}" id="${esc(name)}" ${attrs}>${options
        .map((opt) => `<option value="${esc(opt.value)}" ${String(opt.value) === String(value) ? 'selected' : ''}>${esc(opt.label)}</option>`)
        .join('')}</select>`
    : `<input name="${esc(name)}" id="${esc(name)}" type="${esc(type)}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${attrs} />`;
  return `<div class="field"><label for="${esc(name)}">${esc(label)}</label>${control}</div>`;
}

export function toast(message, tone = '') {
  const stack = document.getElementById('toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${tone}`;
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

export function errorMessage(err) {
  if (!err) return '未知错误';
  if (err.code === 'FORBIDDEN') return '当前角色没有这个操作的权限';
  return err.message || '请求失败';
}

/* ---------------- schemagate 专用组件 ---------------- */

/** 半圆仪表：value 0..1，用于破坏性占比。 */
export function gauge({ ratio, label, caption }) {
  const pct = Math.max(0, Math.min(1, Number(ratio) || 0));
  const radius = 70;
  const arc = Math.PI * radius;
  const filled = arc * pct;
  return `<div class="gauge" data-testid="breaking-gauge">
    <svg viewBox="0 0 176 106" role="img" aria-label="${esc(label)} ${(pct * 100).toFixed(1)}%">
      <path d="M18 96 A ${radius} ${radius} 0 0 1 158 96" fill="none" stroke="var(--rule)" stroke-width="12" />
      <path d="M18 96 A ${radius} ${radius} 0 0 1 158 96" fill="none" stroke="var(--annot)" stroke-width="12"
        stroke-dasharray="${filled.toFixed(1)} ${arc.toFixed(1)}" stroke-linecap="butt" />
      <line x1="10" y1="98" x2="166" y2="98" stroke="var(--rule-strong)" stroke-width="1" />
      <text x="88" y="82" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11"
        fill="var(--ink-faint)">${esc(label)}</text>
    </svg>
    <div>
      <div class="gauge-num">${(pct * 100).toFixed(1)}%</div>
      <div class="gauge-cap">${esc(caption || '')}</div>
    </div>
  </div>`;
}

const DIFF_FLAG = {
  remove_column: '删除',
  rename_column: '改名',
  type_change: '类型',
  nullable_tighten: '收紧可空',
  nullable_relax: '放宽可空',
  default_remove: '取消默认',
  default_change: '默认值',
  add_column: '新增',
};

/** diff 左右对照：左侧基线版本字段，右侧目标字段。 */
export function diffColumns({ baseFields, targetFields, diff }) {
  const removed = new Set();
  const added = new Set();
  const changed = new Map();
  (diff || []).forEach((item) => {
    if (item.change_type === 'remove_column') removed.add(item.field);
    if (item.change_type === 'add_column') added.add(item.field);
    if (item.change_type === 'rename_column') {
      removed.add(item.field);
      added.add(item.new_name);
    }
    if (item.change_type === 'type_change') changed.set(item.field, item);
  });
  const rowFor = (field, side) => {
    const isBreaking = (diff || []).some(
      (item) => item.field === field.name && item.breaking && item.change_type !== 'add_column',
    );
    let cls = '';
    let flag = '';
    if (side === 'base' && removed.has(field.name)) {
      cls = isBreaking ? 'removed' : 'changed';
      flag = '移除';
    }
    if (side === 'target' && added.has(field.name)) {
      cls = 'added';
      flag = '新增';
    }
    if (changed.has(field.name)) {
      cls = 'safe';
      flag = '类型';
    }
    return `<div class="diff-row ${cls}">
      <span>${esc(field.name)}</span>
      <span>${esc(field.type)}${field.nullable === false ? ' NOT NULL' : ''}</span>
      <span class="flag">${esc(flag)}</span>
    </div>`;
  };
  return `<div class="diff-grid" data-testid="diff-columns">
    <div class="diff-col">
      <h4><span>基线版本 · BASE</span><span class="mono">${baseFields.length} 字段</span></h4>
      ${baseFields.map((field) => rowFor(field, 'base')).join('') || '<div class="diff-row"><span>（空）</span></div>'}
    </div>
    <div class="diff-col">
      <h4><span>目标版本 · TARGET</span><span class="mono">${targetFields.length} 字段</span></h4>
      ${targetFields.map((field) => rowFor(field, 'target')).join('') || '<div class="diff-row"><span>（空）</span></div>'}
    </div>
  </div>`;
}

/** 变更清单（逐字段判定 + 批注）。 */
export function diffLedger(diff) {
  if (!diff || diff.length === 0) return '<p class="muted">两份字段集完全一致，无变更。</p>';
  return `<div class="ledger">${diff
    .map(
      (item) => `<div class="ledger-row">
        <b>${esc(DIFF_FLAG[item.change_type] || item.change_type)}</b>
        <span>${esc(item.detail || item.field)}</span>
        <span class="mono">${esc(item.field)}</span>
        ${item.breaking ? '<span class="badge danger">破坏</span>' : '<span class="badge ok">安全</span>'}
      </div>`,
    )
    .join('')}</div>`;
}

export function sqlBlock(sql, testId = 'migration-sql') {
  return `<pre class="sql-block" data-testid="${esc(testId)}">${esc(sql || '-- 暂无迁移单')}</pre>`;
}

/**
 * 下游处置清单：待复核（丢列读取 / 缺值写入）+ 安全提示。
 * 每条依赖可展开看原因与所属任务 / 报表；按任务与报表去重由后端保证。
 */
export function reviewBlock(impact = {}) {
  const pending = impact.pending_dependencies || [];
  const advisory = impact.advisory_dependencies || [];
  const queries = impact.queries_to_fix || [];
  const backfills = impact.backfill_tasks || [];
  if (!pending.length && !advisory.length) {
    return '<p class="muted">没有下游依赖命中本次变更列。</p>';
  }
  const row = (dep, tone) => {
    const reasons = (dep.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join('');
    const kindLabel = dep.kind === 'report' ? '报表' : '任务';
    const sideLabel = dep.side === 'write' ? '写入' : '读取';
    return `<div class="review-row ${tone}" data-testid="review-row">
      <button class="review-head" type="button" aria-expanded="false">
        <span class="badge ${tone}">${dep.review_state === 'pending_review' ? '待复核' : '已确认'}</span>
        <b class="mono">${esc(dep.name)}</b>
        <span class="muted">${esc(kindLabel)} · ${esc(sideLabel)} · ${esc(dep.consumer || '—')}</span>
        <span class="review-caret" aria-hidden="true">▸</span>
      </button>
      <ul class="review-reasons" hidden>${reasons}</ul>
    </div>`;
  };
  return `<div class="review-block" data-testid="review-block">
    <div class="review-summary">
      <span class="badge danger">待复核 ${pending.length}</span>
      ${queries.length ? `<span class="annot-note">需改查询：${esc(queries.join('、'))}</span>` : ''}
      ${backfills.length ? `<span class="annot-note">需补值任务：${esc(backfills.join('、'))}</span>` : ''}
    </div>
    ${pending.length ? `<div class="review-list">${pending.map((dep) => row(dep, 'danger')).join('')}</div>` : ''}
    ${advisory.length
      ? `<details class="review-advisory">
          <summary>安全提示（${advisory.length}）：不阻塞，下游确认空值 / 默认值口径即可</summary>
          <div class="review-list">${advisory.map((dep) => row(dep, 'ok')).join('')}</div>
        </details>`
      : ''}
  </div>`;
}

/** 绑定待复核清单的展开 / 收起（事件委托，重渲染后重新绑定即可）。 */
export function bindReviewExpand(container) {
  container?.querySelectorAll('.review-head').forEach((head) => {
    head.addEventListener('click', () => {
      const reasons = head.parentElement.querySelector('.review-reasons');
      const caret = head.querySelector('.review-caret');
      const open = reasons.hasAttribute('hidden');
      if (open) {
        reasons.removeAttribute('hidden');
        head.setAttribute('aria-expanded', 'true');
        if (caret) caret.textContent = '▾';
      } else {
        reasons.setAttribute('hidden', '');
        head.setAttribute('aria-expanded', 'false');
        if (caret) caret.textContent = '▸';
      }
    });
  });
}
