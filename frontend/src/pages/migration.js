import {methods as methodApi,migrations as migApi} from '../api/research.js';
import {table} from '../components/common/table.js';
import {esc,fmt} from '../utils/format.js';
import {METHOD_LABELS,MIGRATION_BATCH_LABELS,MIGRATION_RUN_LABELS} from '../types/enums.js';

const role=()=>localStorage.getItem('research-role');
const badge=(code,active)=>`<span class="status ${active?'completed':''}">${esc(METHOD_LABELS[code]||code)}${active?' · 生效':''}</span>`;

async function loadVersions(){return methodApi.list();}
async function loadBatches(){return migApi.list();}

function diffTable(diff){
  return table(['核对项','旧版口径值','新版口径值','是否变化'],
    diff.map(d=>[d.metric,fmt(d.old),fmt(d.new),{html:d.changed?'<span class="status failed">变化</span>':'<span class="status completed">一致</span>'}]));
}

export async function render(id){
  const isAdmin=role()==='admin';
  const [versions,batches]=await Promise.all([loadVersions(),loadBatches()]);
  const active=versions.find(v=>v.active);
  let detail='';
  if(id){
    const b=await migApi.get(id);
    detail=`<h2 data-live="${b.status==='shadow'?'1':'0'}">批次 #${b.id} 迁移差异 <small>${esc(MIGRATION_BATCH_LABELS[b.status]||b.status)} · 目标 ${esc(METHOD_LABELS[b.target_method]||b.target_method)}</small></h2>
    <p class="method-note">共 ${b.total} 条 · 影子完成 ${b.shadow_done} · 已切换 ${b.switched_count} · 失败 ${b.failed_count}${b.last_error?' · 原因：'+esc(b.last_error):''}${b.note?' · '+esc(b.note):''}</p>
    <div class="toolbar">
      ${b.status==='shadow'&&b.failed_count===0&&b.shadow_done===b.total&&isAdmin?`<button data-action="switch" data-id="${b.id}">一次切换可见结果 →</button>`:''}
      ${b.status==='shadow'&&b.failed_count>0&&isAdmin?`<button class="secondary" data-action="abandon" data-id="${b.id}">放弃失败批次（整批保留旧版）</button>`:''}
      ${b.status==='switched'&&isAdmin?`<button class="secondary" data-action="rollback" data-id="${b.id}">回退到旧口径</button>`:''}
    </div>
    ${b.runs.map(mr=>`<details ${mr.status==='failed'?'open':''}><summary>#${mr.run_id} ${esc(mr.run_name)} · ${esc(MIGRATION_RUN_LABELS[mr.status]||mr.status)}${mr.reviewed?' · 已获批沿用原口径':''}${mr.error?' · '+esc(mr.error):''}</summary>
      ${mr.diff?diffTable(mr.diff):'<p>影子重算尚未完成，暂无差异。</p>'}
      ${mr.old_result&&mr.new_result?`<p class="checksum">旧版SHA留档 ${esc((mr.old_result.input_sha256||'').slice(0,24))}… · 新版结果校验 ${esc((mr.new_checksum||'').slice(0,24))}…</p>`:''}
    </details>`).join('')}`;
  }
  return `<div class="eyebrow">06 / METHOD & MIGRATION WORKBENCH</div>
  <h1>分析口径与存量迁移</h1>
  <p class="lead">低点击率情景改用双侧 Fisher 精确检验。新研究排队即绑定当时生效口径并永久留档；未获批存量研究先影子重算、核对样本量/预算功效/留档，再一次切换，任一项失败整批保留旧版。</p>
  <h2>生效口径 <small>切换仅影响此后新排队的研究</small></h2>
  ${table(['口径','说明','存量运行数','状态','操作'],versions.map(v=>[
    METHOD_LABELS[v.code]||v.code,
    v.description,
    v.run_count,
    {html:badge(v.code,v.active)},
    {html:isAdmin&&!v.active?`<button class="text" data-method="${v.code}">切换为生效口径</button>`:'—'}
  ]))}
  <h2 data-live="${batches.some(b=>b.status==='shadow')?'1':'0'}">迁移批次 <small>获批研究及其下载永不迁移，沿用原口径</small></h2>
  ${isAdmin?`<form id="mig-form" class="worksheet"><label>目标口径<select name="target_method"><option value="v2-fisher2">双侧 Fisher 精确检验（v2）</option></select></label><button type="submit">发起未获批存量整批迁移（影子重算）→</button><p>迁移期间仍可并发排队；在途研究按排队时口径完成，迟归计算不混入另一版。</p></form>`:'<p class="method-note">仅负责人可发起/切换/回退迁移；观察员与分析师可查看生效口径和迁移差异。</p>'}
  ${batches.length?table(['批次','目标口径','状态','进度','创建人','时间','详情'],batches.map(b=>[
    '#'+b.id,
    METHOD_LABELS[b.target_method]||b.target_method,
    {html:`<span class="status ${b.status==='failed'?'failed':b.status==='switched'||b.status==='rolled_back'?'completed':''}">${MIGRATION_BATCH_LABELS[b.status]||b.status}</span>`},
    `${b.shadow_done}/${b.total} 影子 · ${b.switched_count} 切换 · ${b.failed_count} 失败`,
    b.created_by,b.created_at,
    {html:`<a href="#migration/${b.id}">查看差异 / 操作 →</a>`}
  ])):'<p class="method-note">尚无迁移批次。</p>'}
  ${detail}`;
}

export function bind(refresh){
  const roleNow=role();
  document.querySelectorAll('[data-method]').forEach(btn=>btn.onclick=async()=>{
    try{await methodApi.switch(btn.dataset.method);await refresh();}catch(e){document.getElementById('notice').textContent=e.message;}
  });
  document.querySelectorAll('[data-action]').forEach(btn=>btn.onclick=async()=>{
    const {action,id}=btn.dataset;
    const verb={switch:migApi.switch,rollback:migApi.rollback,abandon:migApi.abandon}[action];
    if(roleNow!=='admin')return;
    if(action==='switch'&&!confirm('一次切换将整批覆盖可见结果为新版口径，确认继续？'))return;
    if(action==='rollback'&&!confirm('回退后未获批运行恢复旧口径；已按新版冻结/获批的研究仍按新版完成。确认？'))return;
    try{await verb(id);await refresh();}catch(e){document.getElementById('notice').textContent=e.message;}
  });
  const form=document.getElementById('mig-form');
  if(form)form.onsubmit=async e=>{
    e.preventDefault();const button=form.querySelector('button');button.disabled=true;
    try{const b=await migApi.create(new FormData(form).get('target_method'));location.hash='migration/'+b.id;await refresh();}
    catch(err){document.getElementById('notice').textContent=err.message;button.disabled=false;}
  };
}
