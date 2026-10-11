/* D3 presentation: existing exact History Apply; explicit derived refresh only. */
import {language} from './i18n.js';
import {node} from './shared.js';
import {deliverPreparedIf} from './if_lines.js';
import {showPlanning} from './simple_planning.js';
const COPY={en:{refresh:'Refresh evaluation',working:'Re-evaluating…',done:'Evaluation refreshed. Financial Reality was not changed.',error:'Could not refresh. Read the latest state and try again.',world_inputs_changed:'Your financial reality changed.',dependency_changed:'This reported input changed',inflow_cancelled:'This expected inflow was cancelled',dependency_removed:'This input is no longer current',context_review_required:'The meaning or planning context changed. Review the baseline or this assumption before continuing.',hypothesis_already_realized:'This hypothetical removal has happened in Reality. Keep it as evidence; it is no longer a future alternative.',required_inflow_unavailable:'An inflow this branch requires is no longer expected.',evidence_stale:'A quote has expired. Supply current evidence before re-evaluating.',baseline_not_current:'The baseline or evidence still needs review.',cost_no_longer_planned:'A cost is no longer planned. Review the cost scope.',authority_changed:'The source session changed. Establish new planning evidence.',baseline_changed:'The baseline changed. Refresh this page.',baseline_superseded:'A newer baseline exists.',world_evidence_stale:'The reported financial evidence is stale.',history:'Previous evaluation evidence',historical:'Historical result · not a current evaluation',version:'Evaluation version',context:'Relevant reported context',unknown:'Unknown',expected:'Expected · not current cash',cancelled:'Cancelled',realized:'Realized',hypothetical:'Hypothetical exploration',hypoNote:'This is an “if”. Choose a Decision to review the hypothetical; no financial record will change.',reality:'Reality update · cancel an expected inflow',realNote:'Choose the exact existing expected inflow. Only its cancellation will be prepared. Review the changes, authorize and Apply before Reality changes.',clarification:'Clarify what actually happened',cancellation_not_confirmed:'This does not clearly confirm a cancellation. Edit the report, or use an explicit “if” to explore it.',choose:'Choose…',continue:'Continue to review',empty:'No matching record is available. Record the relevant Decision or expected inflow first.',close:'Close',source:'Your original report',prepare:'Prepare exact cancellation preview',target:'Expected inflow to cancel',decision:'Decision to explore',noWrite:'Not applied · Reality is unchanged.'},'zh-CN':{refresh:'重新评估',working:'正在重新评估…',done:'评估已刷新，没有修改真实财务状态。',error:'暂时无法重新评估，请读取最新状态后重试。',world_inputs_changed:'你的真实财务信息已变化。',dependency_changed:'这项报告的输入已变化',inflow_cancelled:'这笔预期收入已取消',dependency_removed:'这项输入已不再有效',context_review_required:'含义或规划背景已变化，请先核对基线或这项假设。',hypothesis_already_realized:'这项假设中的移除已经成为现实。原假设保留为证据，不再是未来的备选情景。',required_inflow_unavailable:'此分支依赖的一笔收入已不再预期到账。',evidence_stale:'报价已过期，请补充当前证据后重新评估。',baseline_not_current:'基线或证据仍需要核对。',cost_no_longer_planned:'一项费用已不再处于计划中，请核对成本范围。',authority_changed:'来源会话已变化，请重新建立规划证据。',baseline_changed:'基线已变化，请刷新此页面。',baseline_superseded:'已有更新的基线。',world_evidence_stale:'报告的财务证据已过期。',history:'先前评估证据',historical:'历史结果 · 不是当前评估',version:'评估版本',context:'相关的报告背景',unknown:'未知',expected:'预期到账 · 不是当前现金',cancelled:'已取消',realized:'已实现',hypothetical:'假设探索',hypoNote:'这是“如果”。请选择要探索的决策，再核对假设；不会修改财务记录。',reality:'现实更新 · 取消预期收入',realNote:'请选择确切的现有预期收入。本次只准备该收入的取消；核对变更、授权并应用后，真实财务状态才会变化。',clarification:'请明确实际发生的情况',cancellation_not_confirmed:'这段话尚未明确确认取消。请修改报告，或用明确的“如果”探索假设。',choose:'请选择…',continue:'继续核对',empty:'尚无相应记录，请先记录相关决策或预期收入。',close:'关闭',source:'你的原始报告',prepare:'准备取消的精确预览',target:'要取消的预期收入',decision:'要探索的决策',noWrite:'尚未应用 · 真实财务状态未变化。'}};
Object.assign(COPY.en,{hypothesis_materialized:'This If became Reality. Its original evaluation remains evidence.',lifecycle_uncertain:'Clarify the one event that actually happened; uncertain or mixed reports cannot be applied.',decision_context_changed:'The Decision context changed. Review its planning evidence.',binding_cannot_be_verified:'The source could not be verified. No current result is available.'});
Object.assign(COPY['zh-CN'],{hypothesis_materialized:'这项假设已成为现实，原始评估保留为证据。',lifecycle_uncertain:'请明确实际发生的一件事，尚不确定或混合含义的报告不能直接应用。',decision_context_changed:'决策背景已变化，请核对规划证据。',binding_cannot_be_verified:'暂时无法核验来源，当前结果不可用。'});
Object.assign(COPY.en,{context_review_required:'The meaning or planning context changed. Review the current estimate or this assumption.',baseline_not_current:'The current estimate or its evidence needs review.',baseline_changed:'The current estimate changed. Refresh this page.',baseline_superseded:'A newer estimate is available.'});
Object.assign(COPY['zh-CN'],{context_review_required:'含义或规划背景已变化，请核对当前估算或这项假设。',baseline_not_current:'当前估算或相关依据需要核对。',baseline_changed:'当前估算已变化，请刷新此页面。',baseline_superseded:'已有更新的估算。'});
export const d3Text=k=>(COPY[language]||COPY.en)[k]||COPY[language==='zh-CN'?'zh-CN':'en'].error;
const note=t=>node('p',t,'d1-note');
const button=(key,fn)=>{const b=node('button',d3Text(key),'secondary-button');b.type='button';b.addEventListener('click',fn);return b;};
export async function d3Post(path,payload){const seed=await fetch('/api/state',{cache:'no-store'});if(!seed.ok)throw Error('error');const token=(await seed.json()).token;const r=await fetch('/api/user/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify(payload)});const data=await r.json();if(!r.ok)throw Error(data.code||'error');return data;}
export function staleDetails(parent,result){
 const specific=(result.dependency_details||[]).filter(r=>r.changed);
 for(const r of specific)parent.append(note(`${r.label||d3Text('context')}: ${d3Text(r.reason)}`));
 for(const reason of result.stale_reasons||[])if(reason!=='world_inputs_changed'||!specific.length)parent.append(note(d3Text(reason)));
}
export function evaluationHistory(parent,result,renderEffects){
 if(!result.evaluation_history?.length)return;
 const details=node('details',undefined,'d3-history');details.append(node('summary',d3Text('history')));
 for(const entry of result.evaluation_history){const row=node('div',undefined,'d1-component');row.append(node('strong',`${d3Text('version')} ${entry.version}`),note(d3Text('historical')),note(entry.evaluated_at));renderEffects(row,entry.result.effects);details.append(row);}
 parent.append(details);
}
export function refreshButton(parent,baseline,refresh){
 const status=note('');status.setAttribute('role','status');
 const b=button('refresh',async()=>{b.disabled=true;status.textContent=d3Text('working');try{await d3Post('evaluation/refresh',{decision_id:baseline.decision_ref.ref.id,baseline_ref:baseline.ref});await refresh();}catch(e){status.textContent=d3Text(e.message);b.disabled=false;}});b.classList.add('d3-refresh');parent.append(b,status);
}
export function showRealityRoute(route,{onRealityPrepared,onHypothetical,showFeedback}){
 if(route.kind==='hypothetical'&&route.decisions?.length===1){onHypothetical(route.decisions[0].ref.ref.id,route.source_text);return;}
 let dialog=document.getElementById('d3-route');if(!dialog){dialog=node('dialog',undefined,'i3-dialog d3-route');dialog.id='d3-route';document.body.append(dialog);}dialog.replaceChildren();
 const hypothetical=route.kind==='hypothetical',real=route.kind==='inflow_cancellation';
 dialog.append(node('h2',d3Text(hypothetical?'hypothetical':real?'reality':'clarification')),note(d3Text(hypothetical?'hypoNote':real?'realNote':route.reason)),note(d3Text('noWrite')));
 const source=node('details');source.append(node('summary',d3Text('source')),note(route.source_text));dialog.append(source);
 const options=route.targets||route.decisions||[];
 if(real||hypothetical){if(!options.length)dialog.append(note(d3Text('empty')));else{
  const label=node('label',undefined,'d1-field'),select=node('select'),empty=node('option',d3Text('choose'));empty.value='';select.append(empty);options.forEach((o,i)=>{const option=node('option',o.label);option.value=String(i);select.append(option);});label.append(node('span',d3Text(real?'target':'decision')),select);dialog.append(label);
  if(options.length===1){select.value='0';label.hidden=true;dialog.append(node('strong',options[0].label));}
  const error=note('');error.setAttribute('role','alert');const next=button(real?'prepare':'continue',async()=>{if(select.value==='')return;next.disabled=true;try{const selected=options[Number(select.value)];if(real){await d3Post('reality/prepare',{proposal_id:route.id,target:selected.ref});dialog.close();await onRealityPrepared();}else{dialog.close();await onHypothetical(selected.ref.ref.id,route.source_text);}}catch(e){error.textContent=d3Text(e.message);next.disabled=false;}});dialog.append(error,next);
 }}
 dialog.append(button('close',()=>dialog.close()));dialog.showModal();
}
export async function focusIfDraft(decisionId,source,open,showFeedback){
 let dialog=document.getElementById('if-continuity');if(!dialog){dialog=node('dialog',undefined,'i3-dialog');dialog.id='if-continuity';document.body.append(dialog);}
 const status=note(language==='zh-CN'?'正在准备比较…':'Preparing comparison…');status.setAttribute('role','status');dialog.replaceChildren(status);if(!dialog.open)dialog.showModal();
 try{
  const result=await d3Post('ifs/prepare',{decision_id:decisionId,text:source});
  if(result.status!=='ready'){
   const messages={amount_unknown:language==='zh-CN'?'你原本预计这项决策要花多少钱？':'What did you originally expect this Decision to cost?',planning_context_needed:language==='zh-CN'?'这项决策需要更多规划细节，才能比较方案。':'This Decision needs more planning details before comparing alternatives.',evidence_needs_review:language==='zh-CN'?'先前的估算需要核对，才能继续使用。':'The earlier estimate needs review before it can be used.'};
   status.textContent=messages[result.reason]||d3Text('error');
   const action=button('continue',async()=>{dialog.close();if(result.reason==='evidence_needs_review'){
    await open('decision');const section=[...document.querySelectorAll('.d1-baseline')].find(e=>e.dataset.d1Decision===decisionId);section?.querySelector('.d1-advanced button')?.click();
   }else await showPlanning({decisions:[{ref:{ref:{id:decisionId}},label:result.decision}]},()=>focusIfDraft(decisionId,source,open,showFeedback));});
   action.textContent=language==='zh-CN'?'补充或核对估算':'Add or review estimate';dialog.append(action,button('close',()=>dialog.close()));return;
  }
  dialog.close();await open('decision');
  const baselineSection=[...document.querySelectorAll('.d1-baseline')].find(e=>e.dataset.d1Decision===decisionId);
  baselineSection?.dispatchEvent(new Event('finvis-baseline-ready'));
  for(let i=0;i<60;i++){
   const current=[...document.querySelectorAll('.d1-baseline')].find(e=>e.dataset.d1Decision===decisionId);
   const section=current?.querySelector('.d2-ifs');
   if(section&&!current.hasAttribute('aria-busy')){deliverPreparedIf(decisionId,result.proposal);section.scrollIntoView({block:'start'});return;}
   await new Promise(r=>setTimeout(r,100));
  }
  showFeedback(d3Text('error'));
 }catch{
  status.textContent=language==='zh-CN'?'暂时无法准备比较。请重试。':'Could not prepare the comparison. Please retry.';
  dialog.append(button('refresh',()=>{dialog.close();focusIfDraft(decisionId,source,open,showFeedback);}),button('close',()=>dialog.close()));
 }
}
