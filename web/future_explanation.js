/* Read-only Why: deterministic facts first, explicit local narration on request. */
import {language} from './i18n.js';
import {node} from './shared.js';
const COPY={en:{why:'Why?',title:'Why this impact?',loading:'Reading the supported facts…',drivers:'Main drivers',model:'Organize with local AI',local:'FINVIS explanation · local AI organized',fallback:'FINVIS explanation · supported facts',offline:'Local AI explanation is unavailable. The supported explanation below remains usable.',changed:'The context changed while local AI was replying. These are the current supported facts.',clarify:'Which scenario do you mean?',empty:'Select a possible future or save a scenario first.',details:'Details · explanation facts',close:'Hide explanation',error:'Explanation could not be read. Try Why again.',unassessed:'Financial judgment · Unassessed',stale:'Earlier evaluation · stale',known:'Supported impact'},'zh-CN':{why:'为什么？',title:'为什么会有这些影响？',loading:'正在读取已支持的事实…',drivers:'主要影响因素',model:'用本地 AI 整理解释',local:'FINVIS 解释 · 本地 AI 整理',fallback:'FINVIS 解释 · 基于已支持事实',offline:'本地 AI 解释暂不可用。下面基于已支持事实的解释仍可使用。',changed:'本地 AI 回复期间背景已变化；下面显示当前支持的事实。',clarify:'你指的是哪个情景？',empty:'请先选择一个未来设想，或保存情景。',details:'详情 · 解释事实',close:'收起解释',error:'暂时无法读取解释，请重新点“为什么？”。',unassessed:'财务判断 · 尚未评估',stale:'早期评估 · 已过期',known:'已支持的影响'}};
const L=k=>(COPY[language]||COPY.en)[k];
const button=(text,fn)=>{const b=node('button',text,'secondary-button');b.type='button';b.addEventListener('click',fn);return b;};
export const explanationQuestion=text=>/为什么|为何|主要影响|哪个因素|省了多少|能省多少|买得起|承受得起|应该买|建议|\b(?:why|main (?:impact|drivers?)|largest|biggest|how much.*sav(?:e[sd]?|ings?)|afford|should I|recommend)\b/iu.test(text);
// Only a standalone question may bypass financial Meaning. Mixed statements remain intact.
export const explanationOnlyQuestion=text=>explanationQuestion(text)&&/^(?:why\b|how\b|which\b|what\b|can I afford\b|should I\b|would\b|为什么|为何|哪个|哪项|省了多少|能省多少|买得起|承受得起|我应该|建议)/iu.test(text.trim())&&!/(?:\bI\s+(?:(?:have|already|also|just|actually)\s+)*(?:paid|bought|booked|spent|received|purchased|want|plan|will)\b|(?:我|另外|还有).{0,8}(?:付了|买了|收到|支付了|打算|计划)|(?:please|请)(?:\s+|把|将).{0,20}(?:change|update|record|改|记录))/iu.test(text)&&!/[.!?。！？]\s*\S/u.test(text.trim().replace(/([.!?。！？])$/u,''));
async function call(payload){const state=await(await fetch('/api/state',{cache:'no-store'})).json();const r=await fetch('/api/user/future-explanation',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':state.token},body:JSON.stringify({...payload,language})});if(!r.ok)throw Error('explanation');return r.json();}
export function createExplanation(){
 function showDialog(drawBody){
  document.querySelector('#finvis-why-dialog')?.close();
  const previous=document.activeElement,dialog=node('dialog',undefined,'fs-review ss-why-dialog');dialog.id='finvis-why-dialog';dialog.setAttribute('aria-labelledby','finvis-why-title');
  const title=node('h2',L('title'));title.id='finvis-why-title';title.tabIndex=-1;
  const body=node('section',undefined,'why-dialog-body'),close=button(language==='zh-CN'?'关闭':'Close',()=>dialog.close());
  dialog.append(title,body,close);document.body.append(dialog);
  dialog.addEventListener('close',()=>{dialog.remove();if(previous?.isConnected)previous.focus({preventScroll:true});});
  dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button,select,input,summary,a[href]')].filter(e=>!e.disabled&&e.getClientRects().length);if(!controls.length)return;const first=controls[0],last=controls[controls.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===title)){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
  dialog.showModal();title.focus({preventScroll:true});drawBody(body);return dialog;
 }
 async function askDialog(context,onDone){let body;const dialog=showDialog(host=>{body=host;});const out=await ask(body,context,null,onDone);if(dialog.open&&out?.stage==='empty')onDone?.();return out;}
 function attach(parent,context){const box=node('section',undefined,'why-space');const control=button(L('why'),()=>ask(parent,context,box));control.classList.add('why-trigger');box.append(control);parent.append(box);}
 async function ask(parent,context,existing=null,onDone=null){
  let host=existing||parent.querySelector(':scope > .why-space');if(!host){host=node('section',undefined,'why-space');parent.append(host);}
  const ticket={};host._whyTicket=ticket;host.setAttribute('aria-busy','true');host.replaceChildren(node('p',L('loading'),'fs-note'));
  const payload={...context};delete payload.node;if(context.node&&!context.options&&!context.scenario)payload.node=context.node;
  try{const out=await call(payload);if(!host.isConnected||host._whyTicket!==ticket)return;draw(host,out,payload,onDone);if(out.stage==='explained')onDone?.();host.scrollIntoView({block:'start',behavior:'smooth'});return out;}catch{if(host.isConnected&&host._whyTicket===ticket)host.replaceChildren(node('p',L('error'),'fs-error'));}finally{host.removeAttribute('aria-busy');}
  host.scrollIntoView({block:'start',behavior:'smooth'});
 }
 function draw(host,out,payload,onDone){
  host.replaceChildren();host.dataset.stage=out.stage;
  if(out.stage==='clarify'){host.append(node('h3',L('clarify')));for(const s of out.choices)host.append(button(s.name,()=>ask(host.parentElement,{question:payload.question,scenario:s.ref},host,onDone)));return;}
  if(out.stage==='empty'){host.append(node('p',L('empty'),'fs-note'));return;}
  host.dataset.digest=out.facts.digest;host.append(node('h3',L('title')));
  if(out.facts.status==='stale')host.append(node('p',L('stale'),'fs-stale'));
  if(['unavailable','context_changed'].includes(out.model_state))host.append(node('p',L(out.model_state==='context_changed'?'changed':'offline'),'fs-note why-model-status'));
  host.append(node('p',L(out.model_state==='ready'?'local':'fallback'),'why-caption'));
  const list=node('div',undefined,'why-narration');for(const text of out.narration)list.append(node('p',text));host.append(list);
  const actions=node('div',undefined,'fs-actions');const model=button(L('model'),async()=>{model.disabled=true;await ask(host.parentElement,{...payload,model:true},host);});actions.append(model,button(L('close'),()=>{host._whyTicket=null;attach(host.parentElement,payload);host.remove();}));host.append(actions);
  const details=node('details');details.append(node('summary',L('details')));const evidence=node('pre',JSON.stringify(out.facts,null,2));details.append(evidence);host.append(details);
 }
 return {attach,ask,showDialog,askDialog};
}
