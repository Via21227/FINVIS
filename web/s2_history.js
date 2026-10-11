/* S2 User History: rendering only. The server verifies every read-only prefix. */
import {language} from './i18n.js';
import {node} from './shared.js';
import {lifecycleText} from './plan_reality.js';
import {renderHistoryControls, historyText} from './history_actions.js';

const COPY={
  en:{history:'Version History',current:'Current World revision',historical:'HISTORICAL VERSION · As recorded then',currentVersion:'CURRENT VERSION',baseline:'History started',updated:'Updated Financial World',replacement:'Reviewed replacement',correction:'Corrected records',occurrence:'Actual occurrence',changes:'{n} record changes',view:'View details',back:'Back to History',returnCurrent:'Return to current World',retry:'Retry History read',empty:'No authoritative User changes have been recorded yet.',loading:'Reading verified History…',error:'History is unavailable. No partial or Demo history was substituted.',local:'History is retained only while this local app process runs.',durable:'History is saved on this Mac and survives FINVIS restarts.',sourceSaved:'Reviewed source evidence',sourceHistory:'The reason and exact historical source are retained with this operation.',notRecorded:'Not recorded',removed:'Removed from current view',add:'Add',change:'Change',retire:'Remove',newRecord:'New record',changedRecord:'Record updated',retiredRecord:'Record retired',correctedFrom:'Corrected from the previous version',retiredIn:'Retired by reviewed replacement',included:'Already included in the reported balance',sourceUnavailable:'Original source wording is unavailable in this History view; receipt and record evidence remain available.',snapshot:'View Financial World at this revision',assets:'Current assets',earmarks:'Purpose allocations · within current assets',inflows:'Expected inflows · not current cash',expenses:'Planned expenses · not paid',decisions:'Decisions',accounts:'Accounts',actuals:'Actual occurrences',plan:'Plan record',noAssets:'No current asset observation at this revision.',unknown:'Amount unknown',system:'World/Plan versions and record references',recorded:'Recorded',observed:'Observed',revision:'Revision',account:'Account',position:'Current funds',earmark:'Purpose allocation',future:'Future inflow',expense:'Planned expense',decision:'Decision',monthly:'Reported monthly essential outflow',context:'Context',actual:'Actual occurrence',effect:'Balance adjustment'},
  'zh-CN':{history:'版本历史',current:'当前财务世界修订号',historical:'历史版本 · 当时记录',currentVersion:'当前版本',baseline:'开始记录',updated:'更新财务世界',replacement:'经审阅的替换',correction:'更正记录',occurrence:'实际发生的收支',changes:'{n} 项记录变更',view:'查看详情',back:'返回历史',returnCurrent:'返回当前财务世界',retry:'重试读取历史',empty:'尚无权威用户财务变更记录。',loading:'正在读取已验证的历史…',error:'无法读取历史；未显示不完整历史或演示记录。',local:'历史仅在本地应用进程运行期间保留。',durable:'历史保存在这台 Mac 上，重启 FINVIS 后仍可查看。',sourceSaved:'已审阅的原文依据',sourceHistory:'此操作的原因和精确历史来源已随收据保留。',notRecorded:'尚未记录',removed:'已从当前记录移除',add:'新增',change:'更改',retire:'移除',newRecord:'新增记录',changedRecord:'记录已更新',retiredRecord:'记录已退役',correctedFrom:'由上一版本更正而来',retiredIn:'在经审阅的替换中退役',included:'已包含在报告余额中',sourceUnavailable:'此历史视图无法提供原文措辞；收据及记录依据仍可查看。',snapshot:'查看该修订号的财务世界',assets:'当前资产',earmarks:'用途分配 · 包含在当前资产内',inflows:'预期收入 · 不属于当前现金',expenses:'计划支出 · 尚未支付',decisions:'决策',accounts:'账户',actuals:'实际收支',plan:'计划记录',noAssets:'该修订号尚无当前资产记录。',unknown:'金额未知',system:'财务世界／计划版本与记录引用',recorded:'记录于',observed:'观察于',revision:'修订号',account:'账户',position:'当前资金',earmark:'用途分配',future:'未来收入',expense:'计划支出',decision:'决策',monthly:'用户报告的每月必要支出',context:'背景',actual:'实际收支',effect:'余额调整'}
};
const L=(key,vars={})=>((COPY[language]||COPY.en)[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(vars[name]??''));
const kind=key=>({monthly_essential_outflow:'monthly',account:'account',asset_position:'position',earmark:'earmark',future_inflow:'future',planned_expense:'expense',world_decision:'decision',decision_context:'context',actual_occurrence:'actual',balance_effect:'effect'})[key]||'context';
const STATE={en:{expense:'Expense',inflow:'Inflow',draft:'Draft',adopted:'Adopted',active:'Active',pending:'Pending evaluation',current:'Evaluated',stale:'Stale'},'zh-CN':{expense:'支出',inflow:'收入',draft:'草稿',adopted:'已采纳',active:'有效',pending:'待评估',current:'已评估',stale:'已过期'}};
const state=value=>(STATE[language]||STATE.en)[value]||L('unknown');
const amount=value=>{
  if(!value)return L('notRecorded');
  const currency=value.amount?.currency||value.lower_bound?.currency||value.upper_bound?.currency||value.unknown_currency||'';
  const digits=minor=>{const n=BigInt(minor),a=n<0n?-n:n;return `${n<0n?'-':''}${new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG').format(a/100n)}.${String(a%100n).padStart(2,'0')}`;};
  if(value.precision==='unknown')return `${L('unknown')}${currency?` (${currency})`:''}`;
  if(value.precision==='range')return value.lower_bound?.minor_units==null||value.upper_bound?.minor_units==null?L('unknown'):`${currency} ${digits(value.lower_bound.minor_units)}–${digits(value.upper_bound.minor_units)}`;
  return value.amount?.minor_units==null?L('unknown'):`${value.precision==='approximate'?'≈':''}${currency} ${digits(value.amount.minor_units)}`;
};
const time=value=>value?new Intl.DateTimeFormat(language==='zh-CN'?'zh-CN':'en-GB',{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC',timeZoneName:'short'}).format(new Date(value)):null;
const asOf=value=>value?.kind==='known_date'?value.date_value:value?.kind==='date_window'?`${value.window_start}–${value.window_end}`:value?.kind==='relative'?value.source_text:null;
const line=(label,value)=>{const p=node('p',undefined,'s2-line');p.append(node('span',label),node('strong',value));return p;};
const refText=value=>value?`${value.ref.id} · v${value.ref.version}`:'—';
const button=(label,callback,cls='secondary-button')=>{const b=node('button',label,cls);b.type='button';b.addEventListener('click',callback);return b;};
const note=(text,cls='')=>node('p',text,`s2-note ${cls}`);

function renderSnapshot(root,p){
  const disclosure=node('details',undefined,'s2-snapshot');disclosure.append(node('summary',L('snapshot')));
  const wrap=node('div',undefined,'s2-snapshot-body');
  if(!p.currencies.length)wrap.append(note(L('noAssets')));
  for(const bucket of p.currencies)wrap.append(line(`${L('assets')} · ${bucket.currency}`,amount(bucket.current_assets.value)));
  const groups=[['monthly',p.monthly_essential_observations||[],r=>amount(r.amount)],['accounts',p.accounts,r=>r.label],['position',p.positions,r=>`${p.accounts.find(a=>a.ref.ref.id===r.account_ref.ref.id)?.label||L('position')} · ${amount(r.balance)}`],['earmarks',p.earmarks,r=>`${r.purpose} · ${amount(r.amount)}`],['inflows',p.future_inflows,r=>`${r.label} · ${amount(r.amount)}`],['expenses',p.planned_expenses,r=>`${r.label} · ${amount(r.amount)}`],['decisions',p.decisions,r=>`${r.subject}${r.amount?' · '+amount(r.amount):''}`],['actuals',p.occurrences,r=>`${state(r.kind)} · ${amount(r.amount)}`]];
  for(const [heading,rows,format] of groups){if(!rows.length)continue;const group=node('section');group.append(node('h4',L(heading)));for(const row of rows)group.append(note(format(row)));wrap.append(group);}
  if(p.plan)wrap.append(line(L('plan'),`${state(p.plan.status)} · ${state(p.plan.evaluation_status)}`));
  disclosure.append(wrap);root.append(disclosure);
}

export function renderHistory(root,history,{select,back,retry,act,resume},loading=false,error=false){
  const section=node('section',undefined,'s2-history');section.append(node('h3',L('history')));
  if(error){const message=note(L('error'),'s2-error');message.setAttribute('role','alert');section.append(message);if(retry)section.append(button(L('retry'),retry));root.append(section);return;}
  if(loading||!history){const message=note(L('loading'));message.setAttribute('role','status');message.setAttribute('aria-busy','true');section.append(message);root.append(section);return;}
  const retention=node('details',undefined,'vh-history-meta');retention.append(node('summary',language==='zh-CN'?'历史保存与版本':'History storage and versions'),note(`${L('current')}: ${history.current_revision??'—'}`),note(L(history.retention==='durable_local'?'durable':'local'),'s2-local'));section.append(retention);
  if(resume&&sessionStorage.getItem('finvis-history-pending'))section.append(button(historyText('resume'),resume));
  if(!history.entries.length){const message=note(L('empty'),'s2-empty');message.setAttribute('role','status');section.append(message);root.append(section);return;}
  const detail=history.detail;
  if(detail){
    section.append(button(L('back'),back));
    const head=node('div',undefined,'s2-detail-head');head.append(node('strong',detail.is_current?L('currentVersion'):L('historical')),node('span',`${L('revision')} ${detail.revision}`));section.append(head);
    if(detail.recorded_at)section.append(note(`${L('recorded')}: ${time(detail.recorded_at)}`));
    renderHistoryControls(section,detail,act);
    if(detail.future_origin){const origin=node('details');origin.append(node('summary',language==='zh-CN'?'计划来自哪些探索':'Earlier exploration behind this plan'));for(const r of detail.future_origin)origin.append(note(r.event+' · '+r.label+' · '+(r.adopted?(language==='zh-CN'?'已选择':'Chosen'):(language==='zh-CN'?'背景假设':'Context only'))));section.append(origin);}
    if(detail.destructive)section.append(note(L('replacement'),'s2-destructive'));
    for(const row of detail.rows){
      const action=row.retired?'retire':row.before_ref?'change':'add';const item=node('div',undefined,`s2-change s2-${action}`);
      const title=node('div',undefined,'s2-change-title');title.append(node('strong',row.kind==='monthly_essential_outflow'?L('monthly'):row.kind==='actual_occurrence'?(row.event_semantics?lifecycleText(row.event_semantics):state(row.subject)):row.subject||L(kind(row.kind))),node('span',L(action)));item.append(title);
      const values=node('p',undefined,'s2-values');
      if(row.kind==='balance_effect')values.textContent=row.effect_direction===0?L('included'):`${row.effect_direction<0?'−':'+'}${amount(row.after)}`;
      else if(row.before||row.after)values.textContent=`${row.before?amount(row.before):L('notRecorded')} → ${row.retired?L('removed'):amount(row.after)}`;
      else values.textContent=L(row.retired?'retiredRecord':row.before_ref?'changedRecord':'newRecord');
      item.append(values);
      for(const text of row.text_changes||[])item.append(note(`${text.before} → ${text.after}`,'s2-lineage'));
      if(row.before_status&&row.before_status!==row.after_status){
        const labels=language==='zh-CN'?{cancelled:'已取消',planned:'计划中',expected:'预期'}:{cancelled:'Cancelled',planned:'Planned',expected:'Expected'};
        item.append(note(`${labels[row.before_status]||state(row.before_status)} → ${labels[row.after_status]||state(row.after_status)}`,'s2-lineage'));
      }
      if(row.corrected)item.append(note(L('correctedFrom'),'s2-lineage'));
      if(row.retired&&detail.destructive)item.append(note(L('retiredIn'),'s2-lineage'));
      section.append(item);
    }
    if(detail.source_detail_available){
      const sources=node('details',undefined,'s2-snapshot');sources.append(node('summary',L('sourceSaved')));
      const bindings=detail.committed_evidence?.review?.value?.bindings||[];
      for(const binding of bindings){const evidence=binding.evidence;sources.append(note(evidence?.decision_relevant_excerpt||L('sourceUnavailable')));}
      section.append(sources);
    }else section.append(note(L(detail.history_action?'sourceHistory':'sourceUnavailable'),'s2-local'));
    renderSnapshot(section,detail.projection);
    const audit=node('details',undefined,'s2-audit');audit.append(node('summary',L('system')));
    audit.append(line('World root',`${detail.root_ref.id} · v${detail.root_ref.version}`));
    if(detail.receipt_ref)audit.append(line('Receipt',detail.receipt_ref.id));
    if(detail.source_refs?.length)audit.append(line('Source refs',detail.source_refs.map(ref=>`${ref.id} · v${ref.version}`).join(' / ')));
    for(const row of [...detail.rows,...detail.system_changes]){
      const label=row.subject||L(kind(row.kind));audit.append(line(label,`${refText(row.before_ref)} → ${refText(row.after_ref)}`));
      if(row.observed_at)audit.append(note(`${L('observed')}: ${time(row.observed_at)}`));
      if(asOf(row.as_of))audit.append(note(`${language==='zh-CN'?'截至':'As of'}: ${asOf(row.as_of)}`));
    }
    section.append(audit);
    if(!detail.is_current)section.append(button(L('returnCurrent'),()=>select(history.current_revision)));
  }else{
    const list=node('div',undefined,'s2-list');
    for(const entry of history.entries){const item=node('article',undefined,'s2-entry');
      const title=entry.intended_choice?(language==='zh-CN'?'选择／调整计划':'Chose / changed intended plan'):entry.history_action?historyText('event_'+entry.history_action):entry.revision===0?L('baseline'):entry.destructive?L('replacement'):entry.has_occurrence?L('occurrence'):entry.has_correction?L('correction'):L('updated');
      item.append(node('strong',title),note(entry.recorded_at?time(entry.recorded_at):L('baseline')),note(entry.revision===history.current_revision?L('currentVersion'):'','vh-caption'));
      const meta=node('details',undefined,'vh-history-meta');meta.append(node('summary',language==='zh-CN'?'版本与记录':'Version and records'),note(`${L('revision')} ${entry.revision}`));if(entry.revision)meta.append(note(L('changes',{n:entry.change_count})));item.append(meta);
      item.append(button(L('view'),()=>select(entry.revision)));list.append(item);
    }
    section.append(list);
  }
  root.append(section);
}
