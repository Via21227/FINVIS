/* Meaning-first read-only review stages. Exact D preview still binds consent. */
import {language} from './i18n.js';
import {node} from './shared.js';
import {formatUserAmount} from './i4_home.js';
const COPY={en:{facts:'Financial changes',decisions:'What you’re considering',understood:'FINVIS understood',continue:'Looks right · Continue',wrong:'Something is wrong',edit:'Change wording, amount or precision',evidence:'Review evidence / Advanced',ready:'Review and authorize',unapplied:'Nothing has been applied.',tentative:'Proposed meaning · any matching or replacement questions come next.',scope:'Existing records need a replacement choice next.',advice:'Purchase advice is unavailable. Your reported facts can still be recorded.',details:'Details and evidence',allocations:'Reserved within the account balance',context:'Context',unknown:'Unknown',existing:'Match pending',new:'Not recorded',changes:'Review changes',back:'Back to financial changes'},'zh-CN':{facts:'财务更改',decisions:'你正在考虑的事',understood:'FINVIS 的理解',continue:'没问题，继续',wrong:'有些不对',edit:'修改原文、金额或精度',evidence:'审阅依据／高级',ready:'核对并授权',unapplied:'尚未应用任何更改。',tentative:'拟议内容 · 需要匹配或替换的部分将在下一步确认。',scope:'下一步需确认现有记录的替换范围。',advice:'目前无法给出购买建议；你报告的事实仍可记录。',details:'详情与依据',allocations:'预留款，已包含在账户余额内',context:'背景',unknown:'未知',existing:'待匹配',new:'尚未记录',changes:'查看更改',back:'返回财务更改'}};
export const userFirstText=key=>(COPY[language]||COPY.en)[key]||key;
export const hasDecision=state=>state.candidate?.interpretation.candidate_facts.some(f=>['contemplated_decision','context'].includes(f.type));
export const hasFacts=state=>state.candidate?.interpretation.candidate_facts.some(f=>!['contemplated_decision','context'].includes(f.type));
export function meaningStage(state,stage,exactRenderer){
 const root=node('section',undefined,'uf-review');root.dataset.reviewStage=stage;
 if(state.preview){
  const view=exactRenderer(state);
  const decisions=new Set(['world_decision','decision_context']);
  // Filter DOM groups by explicit kind metadata; do not alter bound preview.
  for(const group of view.querySelectorAll('[data-change-kind]'))if(decisions.has(group.dataset.changeKind)!==(stage==='decisions'))group.remove();
  view.querySelector('.i3-s1-summary')?.remove();const unknowns=view.querySelector('.i3-s1-unknowns');if(unknowns)unknowns.closest('details')?.remove();
  root.append(view);
 }else{
  root.append(node('p',userFirstText('tentative'),'uf-caption'));
  const rows=state.interaction?.evidence?.fact_preview||[];
  for(const row of rows){
   if(['contemplated_decision','context'].includes(row.kind)!==(stage==='decisions'))continue;
   const item=node('div',undefined,'uf-change');item.dataset.factKind=row.kind;
   item.append(node('strong',row.label,'uf-item-name'));
   if(row.amount){const values=node('div',undefined,'uf-values');values.append(node('span',state.current_records?.length?userFirstText('existing'):userFirstText('new')),node('span','→'),node('strong',formatUserAmount(row.amount)));item.append(values);}
   if(row.kind==='earmarked_current_asset')item.append(node('small',userFirstText('allocations')));
   root.append(item);
  }
  if(state.interaction?.questions.some(q=>q.kind==='scope'))root.append(node('p',userFirstText('scope'),'uf-caption'));
 }
 if(stage==='decisions'&&(state.interaction?.evidence?.advice_notice||state.interaction?.evidence?.unsupported?.length))root.append(node('p',userFirstText('advice'),'i3-advice-note'));
 root.append(node('p',userFirstText('unapplied'),'uf-caption'));
 return root;
}
