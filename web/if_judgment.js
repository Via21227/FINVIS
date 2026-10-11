/* Explicit selected-cost budgets only. No financial authorization or advice. */
import {language} from './i18n.js';
import {node} from './shared.js';
const words={en:{within_target:'Within your budget estimate',conflict:'Over your budget estimate',unassessed:'Not enough information yet',scope:'Selected one-time costs only · other expenses are not assessed.',target:'Your maximum',why:'Why this comparison?',drivers:'Main impact',try:'Try another version',ask:'What maximum would you like for these selected costs?',use:'Use this target',skip:'Keep impact only',change:'Change target',remove:'Remove target',details:'Evidence',loading:'Checking your target…',error:'Could not check your target. Try again.',none:'No supported alternative is available.',keep:'Keep this If before setting a target.',missing:'No personal budget has been set for this event.',inputs_changed:'The supporting information changed. Review this If again.',supporting_information_unknown:'The supporting information has not been verified.',amount_unknown:'A selected amount is unknown.',mixed_currency:'These costs use different currencies; no conversion is assumed.',unsupported_time_basis:'Recurring costs need a different comparison.',payment_target_basis:'Your target does not distinguish total price from remaining payment.',range_crosses_target:'The quoted range crosses your maximum.',no_selected_cost:'There is no selected cost to compare.',estimated:'This is an estimate, not an affordability verdict.'},'zh-CN':{within_target:'估算在你的预算内',conflict:'估算超过你的预算',unassessed:'信息还不够',scope:'仅比较所选一次性费用，其他费用尚未评估。',target:'你设定的上限',why:'为什么这样比较？',drivers:'主要影响',try:'试试另一种版本',ask:'这些所选费用最多打算花多少？',use:'使用这个目标',skip:'只看影响',change:'更改目标',remove:'移除目标',details:'依据',loading:'正在核对你的目标…',error:'暂时无法核对目标，请重试。',none:'还没有可计算的替代方案。',keep:'先保留这个设想，再设置目标。',missing:'这件事还没有个人预算目标。',inputs_changed:'支持它的信息已变化，请重新查看假设。',supporting_information_unknown:'支持这些输入的信息还未核实。',amount_unknown:'所选项目的金额尚不清楚。',mixed_currency:'所选费用使用不同货币，不做换算。',unsupported_time_basis:'持续性费用需要另一种比较。',payment_target_basis:'目标尚未区分总价与剩余付款。',range_crosses_target:'报价范围跨越了你的上限。',no_selected_cost:'没有所选费用可以比较。',estimated:'这是估算，不是负担能力结论。'}};
const L=k=>(words[language]||words.en)[k]||k;
const button=(label,fn)=>{const b=node('button',label,'secondary-button');b.type='button';b.onclick=fn;return b;};
const money=(currency,v,approx=false)=>`${approx?'≈ ':''}${currency} ${new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-IE',{maximumFractionDigits:2}).format(v/100)}`;
const amount=a=>a.precision==='range'?`${a.currency} ${a.lower_minor/100}–${a.upper_minor/100}`:money(a.currency,a.value_minor,a.precision==='approximate');
export function createJudgment(call,{trial,refresh,reportError}){
 let cache=new Map(),requests=new Map(),declined=new Set(),generation=0;
 async function target(box,d,p,remove=false){
  const currency=d.target?.currency||p.currency;
  if(!currency)return;
  box.replaceChildren(node('p',L('ask')));
  const input=node('input');input.type='text';input.inputMode='decimal';input.setAttribute('aria-label',L('ask'));input.placeholder=currency;input.value=d.target?(d.target.maximum_minor/100).toString():'';
  const save=button(L(remove?'remove':'use'),async()=>{save.disabled=true;try{
   const source=language==='zh-CN'?`我为 ${d.title} 所选的一次性费用设定上限 ${currency} ${input.value}`:`My maximum for selected one-time costs of ${d.title} is ${currency} ${input.value}`;
   const out=await call('if-space/prepare_target',{node:d.node,text:remove?`Remove my budget for ${d.title}`:source,...(remove?{remove:true}:{currency,maximum:input.value})});
   await call('future-space/confirm',{proposal_id:out.proposal_id});cache.clear();declined.delete(d.node.id);generation++;await refresh();
  }catch(e){reportError(e);save.disabled=false;}});
  if(!remove)box.append(input);
  box.append(save,button(L('skip'),()=>{declined.add(d.node.id);box.replaceChildren(node('p',L('unassessed')),node('p',L('missing')));}));if(!remove)input.focus();
 }
 function show(box,out,p){box.replaceChildren();box.dataset.stage='assessed';for(const d of out.dimensions){const section=node('section',undefined,'if-budget-dimension');section.dataset.judgment=d.state;section.append(node('h4',d.title),node('strong',L(d.state),'if-budget-state'));
  if(d.target)section.append(node('p',L('target')+': '+money(d.target.currency,d.target.maximum_minor)));
  if(d.estimate)section.append(node('p',amount(d.estimate),'if-budget-number'));
  if(d.comparison&&d.state!=='unassessed'){const diff=d.state==='conflict'?d.comparison.lower_difference_minor:-d.comparison.upper_difference_minor;
   section.append(node('p',(language==='zh-CN'?`所选估算${d.state==='conflict'?'超出上限':'低于或等于上限'} `:`Selected estimate ${d.state==='conflict'?'exceeds your maximum by':'leaves room of'} `)+money(d.target.currency,diff)));}
  else section.append(node('p',L(d.reason==='target_missing'?'missing':d.reason)));
  const scope=node('details');scope.append(node('summary',L('why')),node('p',L('scope'),'fs-note'),node('p',L('estimated'),'fs-note'));section.append(scope);
  if(d.drivers.length){section.append(node('p',L('drivers'),'fs-note'));for(const [i,f] of d.drivers.entries())(i===0?section:scope).append(node('p',f.event+' · '+amount(f.selected_amount),'if-budget-driver'));}
  const currencies=[...new Set(out.explanation_facts.factors.filter(f=>f.effect_type==='cost').map(f=>f.currency))].filter(Boolean);const currency=d.target?.currency||(currencies.length===1?currencies[0]:null);
  if(!d.target){if(p.proposal_id)section.append(node('p',L('keep'),'fs-note'));else if(!declined.has(d.node.id)&&currency)section.append(button(L('ask'),()=>target(section,d,{currency})));}
  else if(!p.proposal_id){const manage=node('details',undefined,'if-budget-manage');manage.append(node('summary',L('change')),button(L('change'),()=>target(section,d,{currency})),button(L('remove'),()=>target(section,d,{currency},true)));section.append(manage);}
  box.append(section);
 }
 if(out.alternatives.length){const actions=node('div',undefined,'if-budget-alternatives');actions.append(node('p',L('try')));for(const a of out.alternatives){const d=a.dimensions.find(d=>d.estimate);actions.append(button(a.label+(d?' · '+amount(d.estimate):''),()=>trial(a.selection)));}box.append(actions);}else box.append(node('p',L('none'),'fs-note'));
 const details=node('details');details.append(node('summary',language==='zh-CN'?'高级 · 技术证据':'Advanced · technical evidence'),node('pre',JSON.stringify(out,null,2)));const evidence=node('details');evidence.append(node('summary',L('details')),details);box.append(evidence);
 }
 function showSimple(box,out,p){box.replaceChildren();box.dataset.stage='assessed';for(const d of out.dimensions.filter(d=>d.target)){const row=node('section',undefined,'ss-target');row.dataset.judgment=d.state;let why=d.state==='unassessed'?L(d.reason):d.title+' · '+amount(d.estimate)+(language==='zh-CN'?'，你的目标是 ':'; your maximum is ')+money(d.target.currency,d.target.maximum_minor)+'.';if(d.state==='conflict'&&d.comparison)why+=(language==='zh-CN'?' 高出约 ':' About ')+money(d.target.currency,d.comparison.lower_difference_minor)+(language==='zh-CN'?'。':' above your target.');row.append(node('strong',L(d.state)),node('p',why));box.append(row);}const more=node('details',undefined,'ss-budget-details');more.append(node('summary',language==='zh-CN'?'目标与详细比较':'Targets and detailed comparison'));const inner=node('div');more.append(inner);show(inner,out,p);box.append(more);}
 return {attach(parent,p,{compact=false}={}){const showView=compact?showSimple:show;const box=node('section',undefined,'if-budget');box.setAttribute('aria-live','polite');parent.append(box);const key=JSON.stringify([p,language]),epoch=generation;
 if(cache.has(key)){showView(box,cache.get(key),p);return;}
 box.append(node('p',L('loading'),'fs-note'));
 if(!requests.has(key))requests.set(key,call('if-space/assess',p).then(out=>{requests.delete(key);if(epoch===generation)cache.set(key,out);return out;},e=>{requests.delete(key);throw e;}));
 requests.get(key).then(out=>{if(box.isConnected&&epoch===generation)showView(box,out,p);}).catch(()=>{if(box.isConnected){box.replaceChildren(node('p',L('error')),button(language==='zh-CN'?'重试':'Try again',()=>{cache.delete(key);box.replaceChildren();this.attach(box,p,{compact});}));}});
 },invalidate(){generation++;cache.clear();requests.clear();}};
}
