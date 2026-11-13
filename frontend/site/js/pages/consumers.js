import { api } from '../api.js';
import { ENUMS, can, compatTone, gateTone, riskTone } from '../config.js';
import {
  bindReviewExpand,
  drawer,
  emptyState,
  errorBox,
  errorMessage,
  esc,
  field,
  fmtTime,
  kv,
  reviewBlock,
  table,
  toast,
} from '../ui.js';

const state = { team: '', q: '', requestId: '' };

async function openRegister(view, ctx) {
  const datasets = await api.get('/api/datasets', { limit: 200 });
  const { root, close } = drawer({
    title: '登记下游消费方',
    subtitle: '按读写用途登记引用列：读取任务看「需改查询」，写入任务看「需补值」',
    body: `<form id="consumer-form" style="display:grid;gap:10px">
      ${field({
        label: '所属表',
        name: 'dataset_id',
        options: datasets.items.map((item) => ({ value: item.id, label: item.full_name })),
      })}
      ${field({ label: '消费方名称', name: 'name', placeholder: '经营日报 · GMV' })}
      ${field({ label: '团队', name: 'team', placeholder: '经营分析' })}
      ${field({ label: '负责人', name: 'owner', placeholder: '李蔓' })}
      <div class="filter-bar" style="display:grid;gap:8px">
        <div class="eyebrow">读取侧（SELECT / 聚合 / 报表数据来源）</div>
        ${field({ label: '读取引用列（逗号分隔）', name: 'read_columns', placeholder: 'amount, pay_channel' })}
        ${field({ label: '读取任务（逗号分隔）', name: 'read_tasks', placeholder: 't_daily_gmv' })}
      </div>
      <div class="filter-bar" style="display:grid;gap:8px">
        <div class="eyebrow">写入侧（INSERT / UPDATE 调度，契约收紧时需补值）</div>
        ${field({ label: '写入引用列（逗号分隔）', name: 'write_columns', placeholder: 'order_id, amount' })}
        ${field({ label: '写入任务（逗号分隔）', name: 'write_tasks', placeholder: 't_order_wide_ingest' })}
      </div>
      ${field({ label: '报表（逗号分隔，恒为读取用途）', name: 'reports', placeholder: 'r_gmv_daily' })}
      <div class="form-error" id="consumer-error"></div>
      <button class="btn accent" type="submit">保存</button>
    </form>`,
  });
  const split = (text) => text.split(',').map((item) => item.trim()).filter(Boolean);
  root.querySelector('#consumer-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api.post('/api/consumers', {
        dataset_id: Number(form.dataset_id.value),
        name: form.name.value.trim(),
        team: form.team.value.trim(),
        owner: form.owner.value.trim() || null,
        read_columns: split(form.read_columns.value),
        write_columns: split(form.write_columns.value),
        read_tasks: split(form.read_tasks.value),
        write_tasks: split(form.write_tasks.value),
        reports: split(form.reports.value),
      });
      toast('消费方已登记，读写影响面会立即生效');
      close();
      await consumersPage.render(view, ctx);
    } catch (err) {
      root.querySelector('#consumer-error').textContent =
        err.code === 'CONFLICT' ? '该表下已有同名消费方' : errorMessage(err);
    }
  });
}

async function renderImpact(container, requestId) {
  if (!requestId) {
    container.innerHTML = emptyState({ title: '选一个变更申请', hint: '影响面按该变更的 diff 与下游引用列现算。' });
    return;
  }
  try {
    const detail = await api.get(`/api/change-requests/${requestId}`);
    const impact = detail.impact || {};
    container.innerHTML = `
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
        <span class="badge mono">${esc(detail.code)}</span>
        <span class="badge ${compatTone(detail.compat)}">${esc(detail.compat_label)}</span>
        <span class="badge ${gateTone(detail.gate)}">门禁 ${esc(detail.gate_label)}</span>
        <span class="badge ${riskTone(detail.risk)}">风险 ${esc(detail.risk_label)}</span>
      </div>
      ${kv([
        ['受影响列', `<span class="mono">${esc((impact.affected_fields || []).join('、') || '无')}</span>`],
        ['消费方', `${impact.consumer_count} 个`],
        ['影响任务', `<b>${impact.task_count}</b> 个（读 ${impact.read_tasks?.length || 0} / 写 ${impact.write_tasks?.length || 0}，并集去重）`],
        ['影响报表', `${impact.report_count} 张`],
        ['待复核', impact.pending_count ? `<b class="badge danger">${impact.pending_count} 项</b>` : '0 项']],
      ])}
      ${
        (impact.affected_consumers || []).length
          ? `<div class="ledger" style="margin-top:8px">${impact.affected_consumers
              .map(
                (item) => `<div class="ledger-row" style="grid-template-columns:1.1fr 1fr 52px 52px 60px">
                  <b>${esc(item.name)}<div class="t">${esc(item.team)} · ${esc(item.owner || '—')}</div></b>
                  <span class="mono">${esc((item.hit_columns || []).join(', '))}</span>
                  <span class="mono">读 ${(item.read_tasks || []).length}</span>
                  <span class="mono">写 ${(item.write_tasks || []).length}</span>
                  ${item.review_state === 'pending_review' ? '<span class="badge danger">待复核</span>' : '<span class="badge ok">已确认</span>'}
                </div>`,
              )
              .join('')}</div>`
          : '<p class="muted">该变更没有命中任何下游引用列。</p>'
      }
      <h3 style="margin:12px 0 4px;font-size:0.9rem">下游处置（展开看原因与任务）</h3>
      <div id="impact-review-mount">${reviewBlock(impact)}</div>
      ${
        (detail.checks || []).length
          ? `<h3 style="margin:12px 0 4px;font-size:0.9rem">字段级检测（${detail.checks.length}）</h3>
             <div class="ledger">${detail.checks
               .map(
                 (item) => `<div class="ledger-row" style="grid-template-columns:1.2fr 84px 1fr 56px">
                   <b class="mono">${esc(item.field)}</b>
                   <span>${esc(item.change_type_label)}</span>
                   <span class="t">${esc(item.detail)}</span>
                   ${item.breaking ? '<span class="badge danger">破坏</span>' : '<span class="badge ok">安全</span>'}
                 </div>`,
               )
               .join('')}</div>`
          : '<p class="muted" style="margin-top:8px">该变更尚未执行字段级检测（可到「变更申请」点开单据重跑）。</p>'
      }`;
    bindReviewExpand(container);
  } catch (err) {
    container.innerHTML = errorBox(errorMessage(err));
  }
}

export const consumersPage = {
  async render(view, ctx) {
    let data;
    let requests;
    try {
      [data, requests] = await Promise.all([
        api.get('/api/consumers', { team: state.team, q: state.q, limit: 60 }),
        api.get('/api/change-requests', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => consumersPage.render(view, ctx));
      return;
    }
    const teams = data.teams || [];
    if (!state.requestId && requests.items.length) {
      // 默认选影响面最大的那条变更，进页面就能看到真实推演结果
      const top = [...requests.items].sort((a, b) => b.affected_tasks - a.affected_tasks)[0];
      state.requestId = top.id;
    }
    view.innerHTML = `
      <section class="sheet">
        <div class="sheet-head">
          <b>下游影响</b>
          <span>${data.total} 个消费方 · 影响任务按引用列推演，不是人工登记的数字</span>
        </div>
        <div class="split-2">
          <section class="panel">
            <div class="panel-head"><h2>消费方登记</h2><span class="meta">consumers</span></div>
            <div class="panel-body">
              <div class="filter-bar" id="consumer-filters" style="margin-bottom:10px">
                ${field({ label: '搜索', name: 'q', value: state.q, placeholder: '消费方 / 负责人 / 团队' })}
                ${field({ label: '团队', name: 'team', value: state.team, options: [{ value: '', label: '全部' }, ...teams.map((team) => ({ value: team, label: team }))] })}
                <div class="spacer"></div>
                ${can(ctx.user.role, 'consumer:write') ? '<button class="btn sm annot" id="new-consumer">登记消费方</button>' : ''}
              </div>
              ${table({
                rows: data.items.slice(0, 22),
                columns: [
                  { key: 'name', label: '消费方', render: (row) => `<b>${esc(row.name)}</b><div class="mono muted">${esc(row.dataset_name)}</div>` },
                  { key: 'team', label: '团队' },
                  {
                    key: 'read_columns',
                    label: '读引用列',
                    render: (row) => `<span class="mono muted">${esc((row.read_columns || []).join(', '))}</span>`,
                  },
                  {
                    key: 'write_columns',
                    label: '写引用列',
                    render: (row) => `<span class="mono muted">${esc((row.write_columns || []).join(', ') || '—')}</span>`,
                  },
                  {
                    key: 'task_count',
                    label: '任务(读/写)',
                    className: 'num',
                    render: (row) => `${row.task_count}<span class="muted">（${row.read_task_count || 0}/${row.write_task_count || 0}）</span>`,
                  },
                  { key: 'report_count', label: '报表', className: 'num' },
                ],
                empty: { title: '没有匹配的消费方', hint: '换个团队或清空搜索。' },
              })}
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>影响面推演</h2><span class="meta">impact</span></div>
            <div class="panel-body">
              <div class="field" style="margin-bottom:10px">
                <label for="impact-request">选择变更申请</label>
                <select id="impact-request">
                  ${requests.items
                    .map(
                      (item) =>
                        `<option value="${item.id}" ${item.id === state.requestId ? 'selected' : ''}>${esc(item.code)} · ${esc(item.dataset_name)} · ${esc(item.compat_label)}</option>`,
                    )
                    .join('')}
                </select>
              </div>
              <div id="impact-body"><div class="empty"><div class="mark">推演中…</div></div></div>
            </div>
          </section>
        </div>
        <div class="titleblock">
          <div><b>视图</b>下游影响</div>
          <div><b>口径</b>引用列命中 + 并集去重</div>
          <div><b>消费方</b>${data.items.length} / ${data.total}</div>
          <div><b>来源</b>/api/consumers · /api/change-requests</div>
        </div>
      </section>`;

    const filters = view.querySelector('#consumer-filters');
    filters?.addEventListener('change', () => {
      state.q = filters.querySelector('[name=q]').value.trim();
      state.team = filters.querySelector('[name=team]').value;
      consumersPage.render(view, ctx);
    });
    view.querySelector('#new-consumer')?.addEventListener('click', () => openRegister(view, ctx));
    const impactBody = view.querySelector('#impact-body');
    await renderImpact(impactBody, state.requestId);
    view.querySelector('#impact-request')?.addEventListener('change', async (event) => {
      state.requestId = Number(event.target.value);
      await renderImpact(impactBody, state.requestId);
    });
  },
};

export default consumersPage;
