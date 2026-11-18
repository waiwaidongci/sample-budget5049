import {store} from '../../stores/research.js';
import {table} from './table.js';
import {status} from './status.js';
import {esc} from '../../utils/format.js';
import {METHOD_LABELS} from '../../types/enums.js';
export function runList(){return table(['编号 / 运行','状态','分析口径','源数据快照','复核'],store.runs.map(r=>[{html:`<a href="#results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},{html:`<span class="status">${esc(METHOD_LABELS[r.method]||r.method||'—')}</span>`},r.snapshot.length+'条 · '+r.checksum.slice(0,12),r.review?'已复核':'待负责人复核']));}
