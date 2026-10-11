/* A transient understanding, coordinated through the existing reviewed flows. */
import {language} from './i18n.js';
import {node,cloudHeaders,providerMessage} from './shared.js';

const COPY={
 en:{incomplete:'One part still needs checking. Nothing from this description will be recorded yet.',unclear:'This part is not clear yet.',title:'Here’s what I understood',intro:'Review what happened, then explore what you’re considering.',actual:'Already happened',current:'Current situation',future:'Considering',conditional:'If this happens',reported:'Reported',complete:'Recorded',ready:'Ready to review',waiting:'One detail needed',staged:'Review in progress',unsupported:'Understood · recording isn’t supported yet',unknown:'Amount unknown',about:'About ',review:'Review',resume:'Continue review',explore:'Explore these possibilities',continue:'Continue',answer:'Your answer',answerHint:'Or answer in your own words',close:'Close',cancel:'Stop here',reopen:'Continue this description',source:'Your original words',question:'One detail before continuing',network:'Could not continue. Your description and answer are still here. Try again.',invalid:'That answer needs a little more detail. Your earlier information is still here.',stale:'Information changed. Refresh this review to continue.',expired:'This review is no longer available. Your original description is preserved below.',refresh:'Refresh review',working:'Working…',cancelled:'Stopped here. Any changes you already applied remain recorded.',boundary:'Financial records change only after you review and apply them.',remaining:'You can continue with the remaining items.',done:'The reviewed items are recorded.',empty:'There are no more items to review.',limited:'Some parts can be understood but cannot be carried out here yet.',transfer:'A transfer is understood, but recording transfers is not supported yet.',futureReady:'Ready to explore',alternatives:'Alternative to',condition:'Only if',replacement:'If replacing',linked:'Related to',answerRequired:'Enter an answer or choose one of the options.'},
 'zh-CN':{incomplete:'还有一处需要核对，这条描述暂不写入记录。',unclear:'这部分还没理解清楚。',title:'我理解你现在是这样',intro:'先核对已发生的事，再探索你正在考虑的选择。',actual:'已发生',current:'当前情况',future:'正在考虑',conditional:'如果发生',reported:'你提到的',complete:'已记录',ready:'可以审阅',waiting:'待补充一点',staged:'正在审阅',unsupported:'已理解 · 暂不支持记录',unknown:'金额未知',about:'约 ',review:'审阅',resume:'继续审阅',explore:'探索这些选择',continue:'继续',answer:'你的回答',answerHint:'也可以用自己的话回答',close:'关闭',cancel:'先到这里',reopen:'继续这条描述',source:'你的原话',question:'继续前补充一点',network:'暂时无法继续。描述和回答都已保留，请重试。',invalid:'这个回答还需要补充一点。之前理解的内容仍保留。',stale:'相关信息有变化，请刷新审阅后继续。',expired:'这次审阅已失效，原始描述仍保留在下方。',refresh:'刷新审阅',working:'处理中…',cancelled:'已先停在这里。此前已应用的更改仍保留在记录中。',boundary:'财务记录会在你审阅并应用后才更改。',remaining:'你可以继续处理剩下的内容。',done:'已审阅的内容已记录。',empty:'没有其他待审阅内容。',limited:'部分内容已理解，但暂时还不能在这里处理。',transfer:'已理解这是转账，目前还不支持记录转账。',futureReady:'可以探索',alternatives:'另一种选择',condition:'仅当',replacement:'如果替换',linked:'关联',answerRequired:'请填写回答，或选择一个选项。'}
};
const L=key=>(COPY[language]||COPY.en)[key]??COPY.en[key]??'';
const localized=value=>typeof value==='string'?value:value?.[language]??value?.en??'';
const button=(text,action,primary=false)=>{const value=node('button',text,primary?'primary-button':'secondary-button');value.type='button';value.addEventListener('click',action);return value;};
const futureKinds=new Set(['future_outflow','future','future_possibility','possibility','planned_outflow','planned_expense','expected_inflow','expected_future_inflow','conditional_consequence','conditional_cost','conditional']);
const actualKinds=new Set(['actual','actual_expense','actual_inflow','actual_outflow','refund','transfer']);
const isFuture=claim=>claim.flow!==undefined?claim.flow==='future':futureKinds.has(claim.kind);
function formatMoney(money){
 if(!money)return '';
 const currency=money.currency?`${money.currency} `:'';
 const format=value=>{const raw=String(value??'');if(!/^-?\d+(?:\.\d+)?$/.test(raw))return raw;const [whole,fraction]=raw.split('.');return new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG').format(BigInt(whole))+(fraction?'.'+fraction:'');};
 if(money.precision==='unknown'||money.value==null&&money.lower==null&&money.upper==null)return currency+L('unknown');
 if(money.precision==='range'&&money.lower!=null&&money.upper!=null)return `${currency}${format(money.lower)}–${format(money.upper)}`;
 return `${money.precision==='approximate'?L('about'):''}${currency}${format(money.value)}`;
}
const fromResponse=value=>value?.compound??(value?.routing?.kind==='compound'?value.routing:null)??(value?.kind==='compound'?value:null);

// Successful exploration completes input recovery only when no claim is left unresolved.
// Structural parents are included in supported_claim_ids, not claim_options.
export function futureInputComplete(view,trial){
 const supported=new Set(trial?.supported_claim_ids||[]);
 return !!view?.claims?.length&&!view.question&&!view.limited_understanding&&!view.active_review&&
  !(trial?.blocked?.length||trial?.unsupported?.length)&&
  view.claims.every(c=>c.state==='complete'||isFuture(c)&&supported.has(c.id));
}

export function createCompoundMeaning({onReview,onRoute,onFuture,onComplete,showFeedback}){
 if(!document.querySelector('link[data-compound-style]')){const style=document.createElement('link');style.rel='stylesheet';style.href=new URL('./compound_meaning.css',import.meta.url).href;style.dataset.compoundStyle='true';document.head.append(style);}
 let view=null,busy=false,errorKey='',expired=false,returnFocus=null,done=null,renderedLanguage=null,epoch=0,inputComplete=false;
 const answers=new Map();
 const dialog=node('dialog',undefined,'compound-dialog');dialog.id='compound-dialog';dialog.setAttribute('aria-labelledby','compound-title');dialog.setAttribute('aria-describedby','compound-intro');
 const header=node('header'),title=node('h2');title.id='compound-title';title.tabIndex=-1;
 const close=button('×',()=>hide());close.className='compound-close';header.append(title,close);
 const body=node('div',undefined,'compound-body'),footer=node('footer');dialog.append(header,body,footer);document.body.append(dialog);
 const reopen=button('',()=>resume());reopen.id='compound-open';reopen.classList.add('compound-reopen');reopen.hidden=true;document.querySelector('#finvis-command')?.append(reopen);
 function hide(){if(dialog.open)dialog.close();}
 function show(){if(!dialog.open){returnFocus=document.activeElement;dialog.showModal();}title.focus({preventScroll:true});}
 function update(next){if(!next)return;const changed=view?.bundle_id!==next.bundle_id;view=next;expired=false;if(changed){answers.clear();inputComplete=false;}if(view.stage==='complete'){done?.();done=null;onComplete?.();}}
 async function call(action,payload={}){
  const expected=view.bundle_id;
  const seed=await fetch('/api/state',{cache:'no-store'});if(!seed.ok)throw Error('network');
  const token=(await seed.json()).token;
  const response=await fetch(`/api/user/compound/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify({bundle_id:expected,...payload})});
  const result=await response.json();const next=fromResponse(result);if(next&&view?.bundle_id===expected)update(next);
  if(!response.ok){const error=Error(result.code||'network');error.status=response.status;throw error;}return result;
 }
 async function perform(action,payload={},follow=null){
  if(busy||!view)return null;const ticket=epoch;busy=true;errorKey='';render();
  try{const out=await call(action,payload);if(ticket!==epoch)return null;if(follow)await follow(out);return out;}
  catch(error){if(ticket!==epoch)return null;expired=error.status===404||/^compound_(missing|expired)$/.test(error.message);errorKey=providerMessage(error.message)?error.message:(expired?'expired':error.status===409?'stale':error.status===400?'invalid':'network');return null;}
  finally{if(ticket===epoch){busy=false;render();if(errorKey&&dialog.open)body.querySelector('[role="alert"]')?.focus({preventScroll:true});}}
 }
 async function resume(){if(!view)return;show();await perform('view');}
 async function stage(claim){await perform('stage',{revision:view.revision,claim_id:claim.id},async out=>{if(out.review){hide();await onReview(out.review);}else if(out.routing&&out.routing.kind!=='compound'){hide();await onRoute(out.routing);}else if(fromResponse(out))title.focus({preventScroll:true});});}
 async function answer(value){if(!String(value??'').trim()){errorKey='answerRequired';render();body.querySelector('input')?.focus();return;}const q=view.question;await perform('answer',{revision:view.revision,ambiguity_id:q.id,answer:value});}
 async function explore(){const context={};window.dispatchEvent(new CustomEvent('finvis-compound-future-context',{detail:context}));await perform('future',{...context,revision:view.revision},async out=>{if(out.trial?.space&&(out.trial.stage==='retained'||out.trial.selection&&out.trial.proposal_id)){const omitted=[...(out.trial.blocked||[]),...(out.trial.unsupported||[])].map(item=>view.claims?.find(c=>c.id===item.claim_id&&isFuture(c))?.label).filter(Boolean);hide();await onFuture({...out.trial,omitted_labels:[...new Set(omitted)]});if(futureInputComplete(view,out.trial)){inputComplete=done?.()===true;done=null;}}else{errorKey='limited';}});}
 async function cancel(){await perform('cancel',{},()=>{view=null;done=null;answers.clear();hide();showFeedback?.(L('cancelled'));});}
 function claimCategory(claim){return isFuture(claim)?(/conditional/.test(claim.kind)||view.relationships?.some(r=>r.kind==='conditional_on'&&r.from===claim.id)?'conditional':'future'):actualKinds.has(claim.kind)?'actual':/current|balance/.test(claim.kind)?'current':'reported';}
 function render(force=true){
  if(!force&&renderedLanguage===language)return;renderedLanguage=language;
  const focused=document.activeElement,focusKey=dialog.contains(focused)?focused?.dataset.compoundFocus:null,selectionStart=focused?.selectionStart;
  title.textContent=L('title');close.setAttribute('aria-label',L('close'));reopen.textContent=L('reopen');reopen.hidden=!view||inputComplete;body.replaceChildren();footer.replaceChildren();
  if(!view)return;
  dialog.setAttribute('aria-busy',String(busy));
  const intro=node('p',L(view.limited_understanding?'incomplete':view.claims?.some(c=>c.state==='complete')?'remaining':'intro'),'compound-intro');intro.id='compound-intro';body.append(intro);
  const list=node('ul',undefined,'compound-claims');
  for(const claim of view.claims||[]){
   const row=node('li',undefined,'compound-claim'),content=node('div',undefined,'compound-content');row.dataset.claimState=claim.state;
   const category=node('span',L(claimCategory(claim)),'compound-category');content.append(category,node('h3',claim.label||L('reported')));
   const amount=formatMoney(claim.money);if(amount)content.append(node('p',amount,'compound-money'));
   const timing=typeof claim.timing==='string'?claim.timing:claim.timing?.text||claim.timing?.value;if(timing)content.append(node('p',timing,'compound-timing'));
   for(const relation of view.relationships||[]){
    if(relation.from!==claim.id||!['conditional_on','alternative','replacement'].includes(relation.kind))continue;
    const related=view.claims?.find(c=>c.id===relation.to);if(!related)continue;
    content.append(node('p',`${L(relation.kind==='conditional_on'?'condition':relation.kind==='alternative'?'alternatives':'replacement')} · ${related.label}`,'compound-relation'));
   }
   if(claim.state==='complete')content.append(node('span',L('complete'),'compound-state'));
   else if(claim.state==='unsupported')content.append(node('p',L(claim.kind==='transfer'?'transfer':'unsupported'),'compound-state'));
   else if(claim.state==='blocked')content.append(node('span',L('waiting'),'compound-state'));
   row.append(content);
   if((claim.flow==='review'||claim.flow===undefined&&!isFuture(claim))&&['pending','staged'].includes(claim.state)){
    const action=button(L(claim.state==='staged'?'resume':'review'),()=>stage(claim));action.dataset.compoundFocus='review-'+claim.id;action.setAttribute('aria-label',`${action.textContent} · ${claim.label||L('reported')}`);action.disabled=busy||expired||!!view.active_review&&view.active_review!==claim.id;row.append(action);
   }
   list.append(row);
  }
  if(list.childElementCount)body.append(list);else if(!view.limited_understanding)body.append(node('p',L('empty')));
  if(view.question){
   const q=view.question,section=node('section',undefined,'compound-question'),heading=node('h3',localized(q.question)||L('question'));heading.id='compound-question-title';section.setAttribute('aria-labelledby',heading.id);section.append(heading);
   if(view.limited_understanding&&q.source)section.append(node('p',q.source,'compound-question-context'));
   const affected=(view.limited_understanding?[]:q.claim_ids||[]).map(id=>view.claims?.find(c=>c.id===id)?.label).filter(Boolean);if(affected.length)section.append(node('p',affected.join(' · '),'compound-question-context'));
   const choices=node('div',undefined,'compound-choices');for(const [index,item] of (q.choices||[]).entries()){
    const label=localized(item.label??item.text??item),value=item.value??item.id??item;
    const choice=button(label,()=>answer(value));choice.dataset.compoundFocus='choice-'+index;choice.disabled=busy||expired;choices.append(choice);
   }if(choices.childElementCount)section.append(choices);
   const form=node('form',undefined,'compound-answer'),label=node('label',L('answerHint')),input=node('input');input.id='compound-answer';input.name='answer';input.type='text';input.autocomplete='off';input.maxLength=1000;input.value=answers.get(q.id)||'';input.dataset.compoundFocus='answer';input.setAttribute('aria-labelledby','compound-answer-label');label.id='compound-answer-label';label.htmlFor=input.id;input.disabled=busy||expired;input.addEventListener('input',()=>answers.set(q.id,input.value));
   const submit=button(L(busy?'working':'continue'),()=>answer(input.value),true);submit.disabled=busy||expired;submit.dataset.compoundFocus='answer-submit';form.addEventListener('submit',event=>{event.preventDefault();answer(input.value);});form.append(label,input,submit);section.append(form);body.append(section);
  }
  if(errorKey){const error=node('p',(providerMessage(errorKey)||L(errorKey)),'compound-error');error.setAttribute('role','alert');error.tabIndex=-1;body.append(error);if(errorKey==='stale'){const refresh=button(L('refresh'),resume);refresh.disabled=busy;body.append(refresh);}}
  if(view.limitations?.length&&!view.claims?.some(c=>c.state==='unsupported'))body.append(node('p',L('limited'),'compound-state'));
  if(view.source){const original=node('details',undefined,'compound-source');original.append(node('summary',L('source')),node('p',typeof view.source==='string'?view.source:view.source.text||''));body.append(original);}
  if(!view.claims?.every(c=>c.state==='complete'))body.append(node('p',L('boundary'),'compound-boundary'));
  const stop=button(L('cancel'),cancel);stop.disabled=busy;footer.append(stop);
  if(view.future_available){const future=button(L('explore'),explore,true);future.disabled=busy||expired;future.dataset.compoundFocus='future';footer.append(future);}
  else if(view.stage==='complete')footer.append(button(L('close'),hide,true));
  if(focusKey){const replacement=[...dialog.querySelectorAll('[data-compound-focus]')].find(e=>e.dataset.compoundFocus===focusKey);if(replacement&&!replacement.disabled){replacement.focus({preventScroll:true});if(selectionStart!=null&&replacement.setSelectionRange)replacement.setSelectionRange(selectionStart,selectionStart);}}
 }
 dialog.addEventListener('cancel',event=>{event.preventDefault();hide();});
 dialog.addEventListener('close',()=>{if(returnFocus?.isConnected&&!document.querySelector('dialog[open]'))returnFocus.focus({preventScroll:true});document.querySelector('#finvis-command')?.dispatchEvent(new Event('finvis-composer-compact'));});
 dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),summary,[tabindex="0"]')].filter(e=>e.getClientRects().length);const first=controls[0],last=controls.at(-1);if(!first){event.preventDefault();title.focus();}else if(event.shiftKey&&(document.activeElement===first||document.activeElement===title)){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}});
 window.addEventListener('finvis-future-changed',()=>{if(view&&!busy)perform('view');});
 return {open(next,completion=null){epoch++;busy=false;update(next);done=completion;errorKey='';render();show();},resume,hide,render:()=>render(false),current:()=>view};
}
