import {applyTranslations, formatDate, language, setLanguage, t} from './i18n.js';

import {InputSession} from './ux_state.js';
let composerCompletion=()=>()=>{};
export const captureComposerCompletion=()=>composerCompletion();

export const $ = id => document.getElementById(id);
export const node = (tag, value, className) => { const element=document.createElement(tag); if(value !== undefined) element.textContent=value; if(className) element.className=className; return element; };
export const ref = value => value ? `${value.id} v${value.version}` : t('audit.notRecorded');
export const money = value => `${value.currency} ${new Intl.NumberFormat('en-SG',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value.minor_units/100)}`;
export const isAccepted = view => view.state.employment.status === 'accepted_offer';
export const isExecuted = view => view.receipts.length > 0;

const CANONICAL_INPUTS = {
  trip: ['i’m considering a graduation trip · sgd 3,000','i am considering a graduation trip','graduation trip','我想去毕业旅行，预算 3000 sgd','我想去毕业旅行','毕业旅行'],
  offer: ['i got a job offer','i got a job offer.','我拿到工作 offer 了','我拿到工作 offer'],
  state: ['i want to add an expense','manage financial state','financial state','我想添加一项支出','管理财务状态','财务状态'],
  if1: ['what if i still have no job in 1 month?','if 1 month without a job','如果 1 个月还找不到工作呢？','如果 1 个月还没有工作'],
  if3: ['what if i still have no job in 3 months?','if 3 months without a job','如果 3 个月还找不到工作呢？','如果 3 个月还没有工作'],
  if6: ['what if i still have no job in 6 months?','if 6 months without a job','如果 6 个月还找不到工作呢？','如果 6 个月还没有工作']
};
const normalized = value => value.trim().toLocaleLowerCase().replace(/[。！!]/g,'').replace(/\s+/g,' ');
export function interpretGlobalInput(value) {
  const candidate=normalized(value);
  return Object.entries(CANONICAL_INPUTS).find(([,examples])=>examples.some(example=>normalized(example)===candidate))?.[0] || null;
}
export function supportedIfMonths(value) {
  const candidate=normalized(value);
  for(const months of [1,3,6]) if(CANONICAL_INPUTS[`if${months}`].some(example=>normalized(example)===candidate) || candidate===String(months)) return months;
  return null;
}

const SEMANTIC_KEYS = {
  decision: {'travel-decision':'decisionCard.title'},
  goal: {'car-goal':'goal.cardTitle'},
  outcome: {applied:'execution.applied',rejected:'execution.rejected'},
  field: {
    'goal.target_date':'field.goalTargetDate','state.goal_refs':'field.stateGoals',
    'plan.allocations.goal_ref':'field.planGoal','plan.state_ref':'field.planState',
    'plan.decision_ref':'field.planDecision','plan.planned_outflows':'field.planOutflows'
  }
};
export function semanticText(kind, value, fallback=value) {
  const key=SEMANTIC_KEYS[kind]?.[value];
  return key?t(key):fallback;
}

const stateChannel = new BroadcastChannel('finvis-demo-state');
const preferenceChannel = new BroadcastChannel('finvis-demo-preferences');
const LOCAL_MODEL_KEY = 'finvis-local-model';
let cloudConsent=null;
export const cloudHeaders=()=>cloudConsent?{'X-FINVIS-Cloud-Consent':cloudConsent}:{};
export function providerMessage(code){const key=`localAI.message.${code}`;return t(key)===key?null:t(key);}
// Active input is page-local RAM; successful recent inputs remain opt-in recovery.

export function bindLanguage(control, rerender) {
  const buttons=[...control.querySelectorAll('[data-language]')];
  const reflect=()=>buttons.forEach(button=>{const active=button.dataset.language===language;button.setAttribute('aria-pressed',String(active));button.classList.toggle('active',active);});
  const choose=next=>{if(next===language)return;setLanguage(next);reflect();rerender();};
  reflect();
  buttons.forEach(button=>button.addEventListener('click',()=>{choose(button.dataset.language);preferenceChannel.postMessage({language});}));
  preferenceChannel.addEventListener('message',event=>{if(event.data?.language&&event.data.language!==language)choose(event.data.language);});
  window.addEventListener('storage',event=>{if(event.key==='finvis-language'&&event.newValue&&event.newValue!==language)choose(event.newValue);});
}

export function createClient({render, setControls, showFeedback, showCandidate, renderLocalAI, setAgentProcessing}) {
  let view=null, token=null, busy=false, localAI=null, connectionMutation=false, connectionVersion=0;
  const lockConnection=()=>{connectionVersion++;connectionMutation=true;document.querySelectorAll("#ai-provider-choices button,#ai-provider-choices input,#local-ai-models button").forEach(el=>el.disabled=true);};
  const unlockConnection=()=>{connectionMutation=false;if(localAI&&renderLocalAI)renderLocalAI(localAI);};
  const controls=()=>setControls({view,busy});
  const localMessage=code=>{const key=`localAI.message.${code}`;const value=t(key);return value===key?t('localAI.message.provider_error'):value;};
  async function refreshLocalAI({applySaved=false}={}) {
    if(connectionMutation)return localAI;
    const version=connectionVersion;
    try {
      const response=await fetch('/api/local-ai/status',{cache:'no-store'});
      if(!response.ok)throw new Error();
      const data=await response.json();
      if(version!==connectionVersion||connectionMutation)return localAI;
      if(!data.local_ai||typeof data.local_ai!=='object')throw new Error();
      localAI=data.local_ai;
    } catch {
      if(version!==connectionVersion||connectionMutation)return localAI;
      // No FINVIS status was obtained; do not blame LM Studio or invent an empty model list.
      localAI={state:'app_unavailable',connection:'unavailable',provider:null,endpoint:null,selected_model:null,models:[],loaded_model_ids:[],ready:false,busy:false,requests_enabled:false,last_successful_check:null,error_category:'app_status_unavailable'};
      if(renderLocalAI)renderLocalAI(localAI);return localAI;
    }
    const saved=localStorage.getItem(LOCAL_MODEL_KEY);
    if(applySaved&&!localAI.cloud&&saved&&saved!==localAI.selected_model&&token){
      try {
        const response=await fetch('/api/local-ai/select',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify({model_id:saved})});
        if(!response.ok)throw new Error();
        localAI=(await response.json()).local_ai;
      } catch {
        // A failed preference sync does not erase the valid discovery just returned.
        localAI={...localAI,error_category:'selection_restore_failed'};
      }
    }
    if(renderLocalAI)renderLocalAI(localAI);return localAI;
  }
  async function selectProvider(provider){
    if(!token||connectionMutation)return false;
    lockConnection();
    try{
      const response=await fetch('/api/ai/provider',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify({provider,cloud_ack:provider==='openai'})});
      const data=await response.json();
      if(!response.ok){showFeedback(localMessage(data.code),true);return false;}
      cloudConsent=data.local_ai.cloud_consent_token||null;
      localAI=data.local_ai;delete localAI.cloud_consent_token;
      if(renderLocalAI)renderLocalAI(localAI);return true;
    }catch{showFeedback(localMessage('app_unavailable'),true);return false;}
    finally{unlockConnection();}
  }
  async function sessionCredential(action,apiKey,ack){
    if(!token||connectionMutation)return "provider_busy";
    lockConnection();
    let body=action==='connect'?{action,api_key:apiKey,cloud_ack:ack}:{action};
    apiKey=null;
    try{
      const request=fetch('/api/ai/credential',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify(body)});
      body=null;
      const response=await request,data=await response.json();
      if(!response.ok){return data.code||'credentials_rejected';}
      cloudConsent=data.local_ai.cloud_consent_token||null;
      localAI=data.local_ai;delete localAI.cloud_consent_token;
      if(renderLocalAI)renderLocalAI(localAI);
      return null;
    }catch{return 'app_status_unavailable';}
    finally{body=null;unlockConnection();}
  }
  async function selectLocalModel(modelId){
    if(!token||connectionMutation)return false;
    lockConnection();
    try{
      const response=await fetch('/api/local-ai/select',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify({model_id:modelId})});
      const data=await response.json();if(!response.ok){showFeedback(localMessage(data.code),true);await refreshLocalAI();return false;}
      localAI=data.local_ai;localStorage.setItem(LOCAL_MODEL_KEY,modelId);preferenceChannel.postMessage({localModel:modelId});if(renderLocalAI)renderLocalAI(localAI);return true;
    }catch{showFeedback(localMessage('provider_error'),true);await refreshLocalAI();return false;}
    finally{unlockConnection();}
  }
  async function refresh({quiet=false}={}) {
    try {
      const response=await fetch('/api/state',{cache:'no-store'}); if(!response.ok) throw new Error();
      const data=await response.json(); token=data.token; view=data.view; render(view); $('connection').hidden=true; controls();await refreshLocalAI({applySaved:true});return true;
    } catch {
      if(!quiet){ $('connection-text').textContent=t('connection.error'); $('connection').hidden=false; }
      view=null; controls(); return false;
    }
  }
  async function command(name) {
    if(busy || !view) return false;
    busy=true; controls();
    try {
      const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify({command:name,expected:{generation:view.generation,revision:view.revision}})});
      const data=await response.json(); if(data.view){view=data.view;render(view);}
      if(!response.ok){ if(!data.view) await refresh(); showFeedback(t(`error.${data.code}`) === `error.${data.code}` ? t('feedback.generic') : t(`error.${data.code}`),true); return false; }
      showFeedback(t(`feedback.${name}`)); stateChannel.postMessage({generation:view.generation,revision:view.revision}); return true;
    } catch { await refresh(); showFeedback(t('feedback.interrupted'),true); return false; }
    finally { busy=false; controls(); }
  }
  async function agentRequest(path, payload, trackProcessing=false) {
    if(busy || !view) return false;
    busy=true;controls();if(localAI){localAI={...localAI,busy:true};if(renderLocalAI)renderLocalAI(localAI);}if(setAgentProcessing&&trackProcessing)setAgentProcessing(true);
    try {
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify({...payload,expected:{generation:view.generation,revision:view.revision}})});
      const data=await response.json();if(data.view){view=data.view;render(view);}
      if(!response.ok){await refreshLocalAI();showFeedback(localMessage(data.code||localAI?.state),true);return false;}
      if(showCandidate)showCandidate(data.candidate);
      return data.candidate;
    } catch {await refreshLocalAI();showFeedback(localMessage(localAI?.state),true);return false;}
    finally {busy=false;controls();if(setAgentProcessing&&trackProcessing)setAgentProcessing(false);await refreshLocalAI();}
  }
  async function interpret(text){return agentRequest('/api/agent/interpret',{text},true);}
  async function resolveCandidate(action,candidateId){
    const candidate=await agentRequest(`/api/agent/${action}`,{candidate_id:candidateId});
    if(candidate)showFeedback(t(action==='confirm'?'agent.confirmed':'agent.cancelled'),false,action==='confirm'?'review-only':'default');
    return candidate;
  }
  stateChannel.addEventListener('message',()=>refresh({quiet:true}));
  preferenceChannel.addEventListener('message',event=>{if(event.data?.localModel&&event.data.localModel!==localAI?.selected_model)selectLocalModel(event.data.localModel);});
  window.addEventListener('storage',event=>{if(event.key===LOCAL_MODEL_KEY&&event.newValue&&event.newValue!==localAI?.selected_model)selectLocalModel(event.newValue);});
  window.addEventListener('focus',()=>refresh({quiet:true}));
  return {refresh,refreshLocalAI,selectLocalModel,selectProvider,sessionCredential,command,interpret,resolveCandidate,getView:()=>view,getLocalAI:()=>localAI,rerender:()=>view&&render(view),rerenderLocalAI:()=>localAI&&renderLocalAI&&renderLocalAI(localAI)};
}

export function renderLocalAIConnection(status,onSelect){
  if(!status)return;
  const fixture=status.state==='recording_fixture';
  const cloud=!!status.cloud;
  let chooser=$('ai-provider-choices');
  if(!chooser){chooser=node('section',undefined,'ai-provider-choices');chooser.id='ai-provider-choices';document.querySelector('#local-ai-dialog .manager-intro').after(chooser);}
  chooser.hidden=!status.provider_choices;
  chooser.replaceChildren();
  if(status.provider_choices){
    const local=node('button',t('ai.local'),'secondary-button');local.type='button';local.disabled=status.busy;local.setAttribute('aria-pressed',String(!cloud));local.onclick=()=>chooser.dispatchEvent(new CustomEvent('provider-choice',{bubbles:true,detail:'lmstudio'}));
    const disclosure=node('p',t('ai.disclosure'));disclosure.id='ai-cloud-disclosure';
    const label=node('label');const ack=node('input');ack.type='checkbox';ack.id='ai-cloud-ack';label.append(ack,node('span',t('ai.ack')));
    const use=node('button',t('ai.use'),'primary-button');use.type='button';use.disabled=true;use.setAttribute('aria-describedby','ai-cloud-disclosure');ack.onchange=()=>use.disabled=!ack.checked||status.busy;use.onclick=()=>chooser.dispatchEvent(new CustomEvent('provider-choice',{bubbles:true,detail:'openai'}));
    if(cloud&&cloudConsent){label.hidden=true;use.textContent=t('ai.selected');use.disabled=true;}
    const keyLabel=node('label',t('ai.key'));keyLabel.htmlFor='ai-session-key';
    const key=node('input');key.id='ai-session-key';key.type='password';key.autocomplete='off';key.spellcheck=false;key.maxLength=512;key.setAttribute('aria-describedby','ai-session-note ai-key-feedback');
    const note=node('p',t('ai.sessionNote'));note.id='ai-session-note';
    const feedback=node('p',status.credential_present?t(status.credential_source==='session'?'ai.sessionConfigured':'ai.envConfigured'):'');feedback.id='ai-key-feedback';feedback.setAttribute('role','status');
    const connect=node('button',t(status.credential_present?'ai.replaceKey':'ai.connect'),'primary-button');connect.id='ai-key-connect';connect.type='button';connect.disabled=true;
    const reflect=()=>{connect.hidden=status.credential_present&&!key.value;connect.disabled=status.busy||!ack.checked||!key.value;use.disabled=status.busy||!ack.checked;key.removeAttribute('aria-invalid');};
    key.oninput=reflect;ack.onchange=reflect;
    // Replacement always needs a fresh acknowledgement, even in a selected tab.
    key.oninput=()=>{if(key.value)label.hidden=false;reflect();};
    const disconnect=node('button',t('ai.disconnect'),'secondary-button');disconnect.id='ai-key-disconnect';disconnect.type='button';disconnect.hidden=!status.credential_present;disconnect.disabled=status.busy;
    chooser.append(local,keyLabel,key,note,disclosure,label,connect,use,disconnect,feedback);
    connect.hidden=!!status.credential_present;
    use.hidden=!status.credential_present||(cloud&&!!cloudConsent);
    if(!status.credential_present)label.hidden=false;
  }
  $('local-ai-title').textContent=t('ai.title');
  document.querySelector('#local-ai-dialog .local-ai-model-section').hidden=cloud;
  let notice=$('ai-cloud-notice');
  if(!notice){notice=node('p',undefined,'ai-cloud-notice');notice.id='ai-cloud-notice';document.querySelector('#finvis-command')?.append(notice);}
  notice.hidden=!cloud;notice.textContent=t('ai.notice');
  document.querySelector('#local-ai-dialog .manager-intro').textContent=t(fixture?'localAI.fixtureIntro':cloud?'ai.cloudIntro':'localAI.intro')+(cloud&&status.error_category?' · '+(providerMessage(status.error_category)||''):'');
  document.querySelector('#local-ai-dialog .local-ai-boundary').textContent=t(fixture?'localAI.fixtureBoundary':cloud?'ai.cloudBoundary':'localAI.boundary');
  const stateKey=`localAI.state.${status.state}`;
  $('local-ai-trigger').dataset.state=status.state;
  $('local-ai-trigger-text').textContent=cloud?`OpenAI · ${t(stateKey)}`:t(stateKey);
  $('local-ai-provider').textContent=status.provider||t('localAI.notAvailable');
  $('local-ai-connection').textContent=t(`localAI.connection.${status.connection}`);
  $('local-ai-endpoint').textContent=status.endpoint||t('localAI.notAvailable');
  $('local-ai-selected').textContent=status.selected_model||t('localAI.noneSelected');
  $('local-ai-status').textContent=t(stateKey);
  const models=(status.models||[]).map(model=>{
    const selected=model.id===status.selected_model,ready=selected&&model.loaded&&(status.ready||status.busy);
    const state=ready?'selected-ready':selected&&!model.loaded?'selected-unavailable':model.loaded?'loaded':'installed';
    const row=node('article',undefined,`local-model-row ${state}`);row.dataset.modelId=model.id;row.dataset.modelState=state;
    const copy=node('span');copy.append(node('strong',model.display_name),node('small',model.id),node('small',model.loaded?`${model.runtime} · ${t('localAI.loaded')}`:model.runtime));
    const trailing=node('div',undefined,'local-model-action');
    if(ready){trailing.append(node('span','✓','local-model-check'),node('b',t(status.busy?'localAI.selectedWorking':'localAI.selectedReady')));}
    else if(selected){trailing.append(node('b',t('localAI.selectedUnavailable')));}
    else if(model.loaded){const choose=node('button',t('localAI.select'),'local-model-select');choose.type='button';choose.disabled=status.busy;choose.addEventListener('click',()=>onSelect(model.id));trailing.append(choose);}
    else trailing.append(node('b',t('localAI.installedNotLoaded')));
    row.append(copy,trailing);return row;
  });
  $('local-ai-models').replaceChildren(...models);
  $('local-ai-empty').textContent=t(fixture?'localAI.fixtureNote':status.state==='app_unavailable'?'localAI.notChecked':'localAI.noModels');
  $('local-ai-empty').hidden=models.length>0;
  $('local-ai-diagnostics').textContent=t('localAI.diagnosticsValue',{checked:status.last_successful_check?formatDateTime(status.last_successful_check):t('localAI.never'),error:status.error_category||t('localAI.none')});
}

function formatDateTime(value){try{return new Intl.DateTimeFormat(language==='zh-CN'?'zh-CN':'en-GB',{dateStyle:'medium',timeStyle:'medium'}).format(new Date(value));}catch{return value;}}

export function bindLocalAI(client){
  const dialog=$('local-ai-dialog');
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const controls=[...dialog.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')]
      .filter(el=>!el.disabled&&el.tabIndex>=0&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden');
    const first=controls[0],last=controls.at(-1);
    if(first&&((event.shiftKey&&document.activeElement===first)||(!event.shiftKey&&document.activeElement===last))){
      event.preventDefault();(event.shiftKey?last:first).focus();
    }
  });
  $('local-ai-dialog').addEventListener('provider-choice',event=>client.selectProvider(event.detail));
  dialog.addEventListener('click',async event=>{
    const action=event.target.id==='ai-key-connect'?'connect':event.target.id==='ai-key-disconnect'?'disconnect':null;
    if(!action)return;
    const key=$('ai-session-key');let value=key?.value; if(key)key.value='';
    const ack=$('ai-cloud-ack')?.checked===true;
    const button=event.target;button.disabled=true;
    const pending=client.sessionCredential(action,value,ack);value=null;
    const error=await pending;
    const feedback=$('ai-key-feedback');
    if(feedback)feedback.textContent=error?(providerMessage(error)||t('ai.keyFailed')):t(action==='connect'?'ai.sessionConfigured':'ai.disconnected');
    if(error){$('ai-session-key')?.setAttribute('aria-invalid','true');$('ai-session-key')?.focus();}
    else $('ai-session-key')?.focus();
    if(action==='disconnect'&&button.isConnected)button.disabled=false;
  });
  dialog.addEventListener('close',()=>{const key=$('ai-session-key');if(key)key.value='';});
  $('local-ai-trigger').addEventListener('click',async()=>{await client.refreshLocalAI();if(!$('local-ai-dialog').open)$('local-ai-dialog').showModal();});
  $('local-ai-close').addEventListener('click',()=>$('local-ai-dialog').close());
  $('local-ai-refresh').addEventListener('click',()=>client.refreshLocalAI());
}

export function bindAgentComposer(textarea, form, submit) {
  let storage;try{storage=localStorage;}catch{storage={getItem:()=>null,setItem:()=>{throw Error('storage');}};}const session=new InputSession(storage);let submitting=false;
  const resize=()=>{textarea.style.height='auto';textarea.style.height=`${Math.min(textarea.scrollHeight,Number.parseFloat(getComputedStyle(textarea).maxHeight))}px`;textarea.classList.toggle('scrolling',textarea.scrollHeight>textarea.clientHeight+1);};
  const status=$('agent-draft-status'),clearButton=$('agent-draft-clear');let saveTimer,statusState='';
  const reflect=()=>{
    renderRecent();
    if(!status)return;
    status.setAttribute('aria-live',statusState==='saving'?'off':'polite');
    status.dataset.state=statusState;
    status.textContent=statusState?t(`agent.draft.${statusState}`):'';
    status.hidden=!statusState;
  };
  const setStatus=next=>{statusState=next;reflect();};
  const save=()=>{
    session.draft=textarea.value;setStatus(textarea.value?'saved':'');
  };
  const schedule=()=>{clearTimeout(saveTimer);saveTimer=setTimeout(save,180);};
  const clear=({focus=true}={})=>{session.edited();clearTimeout(saveTimer);session.draft='';textarea.value='';resize();setStatus('');if(focus)textarea.focus({preventScroll:true});};
  const set=value=>{session.edited();clearTimeout(saveTimer);textarea.value=value;resize();save();};
  const recent=node('details',undefined,'composer-recent'),summary=node('summary'),items=node('div');recent.append(summary,items);form.append(recent);
  function renderRecent(){const zh=language==='zh-CN';summary.textContent=zh?'最近输入':'Recent inputs';const rows=session.recent();recent.hidden=!rows.length;items.replaceChildren();if(!rows.length)return;items.append(node('small',zh?'仅此浏览器 · 不属于财务记录':'This browser only · not financial History'));for(const text of rows){const b=node('button',text,'secondary-button');b.type='button';b.title=text;b.onclick=()=>{if(/\S/u.test(textarea.value)&&textarea.value!==text){const note=node('p',zh?'先保留或清空当前草稿，再恢复这条输入。':'Keep or clear your current draft before restoring this input.');note.setAttribute('role','status');items.append(note);return;}set(text);recent.open=false;textarea.focus();};items.append(b);}}
  composerCompletion=()=>session.completion(()=>textarea.value,()=>{clear({focus:false});textarea.dispatchEvent(new Event('input'));form.dispatchEvent(new Event('finvis-composer-compact'));},okay=>{if(!okay)setStatus('failed');renderRecent();});
  const send=async()=>{if(submitting||textarea.disabled||!/\S/u.test(textarea.value))return;submitting=true;recent.open=false;form.dispatchEvent(new Event('finvis-composer-compact'));form.setAttribute('aria-busy','true');$('agent-submit').disabled=true;$('agent-submit').textContent=t('agent.processingButton');session.start(textarea.value);save();try{await submit();}finally{submitting=false;form.removeAttribute('aria-busy');$('agent-submit').disabled=textarea.disabled;$('agent-submit').textContent=t('agent.submit');}};
  // A fresh document never imports a previous successful or unfinished input.
  // Navigation inside this document preserves the same textarea and RAM draft.
  textarea.value='';session.draft='';
  textarea.addEventListener('input',()=>{session.edited();resize();setStatus(textarea.value?'saving':'');schedule();});
  if(clearButton)clearButton.addEventListener('click',()=>clear());
  textarea.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();send();}});
  form.addEventListener('submit',event=>{event.preventDefault();send();});
  resize();reflect();return {resize,clear,save,set,reflect};
}

export function createAgentProcessingController(form,button,status){
  let delayedTimer=null,active=false,long=false;
  const stop=node('button',undefined,'agent-stop secondary-button');stop.id='agent-stop';stop.type='button';stop.hidden=true;status.after(stop);
  const reflect=()=>{button.textContent=t(active?'agent.processingButton':'agent.submit');status.hidden=!active;stop.hidden=!active;stop.textContent=language==='zh-CN'?'停止':'Stop';if(active)status.textContent=t(long?'agent.processingLong':'agent.processing');};
  const controller=next=>{
    clearTimeout(delayedTimer);active=next;long=false;form.setAttribute('aria-busy',String(active));button.disabled=active;reflect();
    if(active)delayedTimer=setTimeout(()=>{long=true;reflect();},12000);
  };
  controller.refresh=reflect;controller.setStop=callback=>stop.addEventListener('click',callback);return controller;
}

const factTypeKey=value=>`agent.fact.${value}`;
const agentSignal=(kind,value)=>{const key=`agent.${kind}.${value}`,localized=t(key);return localized===key?value:localized;};
function candidateAmount(amount){
  if(!amount)return '';
  const value=amount.precision==='range'?`${amount.range_min}–${amount.range_max}`:amount.value;
  if(!value)return t('agent.amountUnknown');
  return t(amount.precision==='approximate'?'agent.amountApprox':'agent.amount',{currency:amount.currency||'',value});
}
export function renderAgentCandidate(candidate,{open=false}={}) {
  if(!candidate)return;
  const interpretation=candidate.interpretation, change=candidate.proposed_change, dialog=$('agent-preview');
  if(dialog.open&&dialog.dataset.world==='user'&&!open)return;
  dialog.dataset.world='demo';
  $('agent-preview-action').textContent=t(change.operation==='replace_financial_state'?'agent.previewReplace':'agent.previewUpdate');
  const categoryFor={current_asset:'currentAssets',earmarked_current_asset:'earmarks',expected_future_inflow:'futureInflows',planned_expense:'planned',contemplated_decision:'decisions',context:'context',actual_expense:'actual',actual_inflow:'actual'};
  const grouped=new Map();interpretation.candidate_facts.forEach(fact=>{const key=categoryFor[fact.type]||'context';if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(fact);});
  const factCard=fact=>{
    const article=node('article',undefined,'agent-fact');
    const heading=node('div',undefined,'agent-fact-heading');heading.append(node('span',t(factTypeKey(fact.type))),node('strong',fact.label));
    const detail=node('p',[fact.account,candidateAmount(fact.amount),fact.earmark||fact.purpose,fact.timing_note].filter(Boolean).join(' · '));
    const boundary=node('small',t('agent.candidateOnly'));
    article.append(heading,detail,boundary);return article;
  };
  $('agent-facts').replaceChildren(...[...grouped].map(([key,facts])=>{const section=node('section',undefined,'agent-fact-group');section.append(node('h3',t(`agent.group.${key}`)));const grid=node('div',undefined,'agent-fact-grid');grid.append(...facts.map(factCard));section.append(grid);return section;}));
  $('agent-understood-summary').textContent=t('agent.understoodSummary',{count:interpretation.candidate_facts.length,groups:grouped.size});
  const attention=[...interpretation.destructive_actions.map(value=>t('agent.attentionDestructive',{value:agentSignal('destructive',value)})),...interpretation.uncertainties,...interpretation.conflicts,...interpretation.unsupported_requests.map(value=>t('agent.attentionUnsupported',{value:agentSignal('unsupported',value)}))];
  $('agent-attention').replaceChildren(...attention.map(value=>node('li',value)));$('agent-attention-section').hidden=attention.length===0;
  const questions=interpretation.clarification_questions.map(value=>node('li',value));
  $('agent-questions').replaceChildren(...questions);$('agent-questions-section').hidden=questions.length===0;
  $('agent-unresolved-summary').textContent=questions.length?t('agent.unresolvedSummary',{count:questions.length}):t('agent.noUnresolved');
  const confirmed=candidate.status==='confirmed_no_commit';
  $('agent-stage-understanding').textContent=t(confirmed?'agent.stage.confirmed':'agent.stage.readyReview');
  $('agent-stage-world').textContent=t('agent.stage.notApplied');
  $('agent-stage-next').textContent=t('agent.stage.noApplyPath');
  $('agent-details-toggle').textContent=t('agent.showDetails',{count:interpretation.candidate_facts.length});$('agent-details').open=false;
  $('agent-preview-boundary').textContent=t('agent.previewBoundary',{count:change.candidate_only_count});
  $('agent-confirm').disabled=candidate.status!=='awaiting_confirmation';$('agent-cancel').disabled=candidate.status!=='awaiting_confirmation';
  dialog.dataset.candidateId=candidate.id;
  if(open&&!dialog.open){dialog.showModal();dialog.scrollTop=0;$('agent-preview-title').focus({preventScroll:true});}
}

export function dashboardSummary(view) {
  return t(isAccepted(view)?'dashboard.accepted':'dashboard.initial',{liquidity:view.display.liquidity,runway:view.display.observed_runway});
}
export function mobileSummary(view) { return t(isAccepted(view)?'mobile.accepted':'mobile.initial',{runway:view.display.observed_runway}); }
export function planStatus(view) { return t(view.plan.ref.version===3?'plan.reevaluated':view.plan.ref.version===2?'plan.updated':'plan.original'); }
export function decisionCollection(view) { return Array.isArray(view.decisions) ? view.decisions : view.decision ? [view.decision] : []; }
export function decisionStatus(view) { return t(isAccepted(view)?'decisionCard.statusResolved':isExecuted(view)?'decisionCard.statusInPlan':view.action?'decisionCard.statusReady':'decisionCard.statusEvaluating'); }
export function renderActiveDecisions(container, view, onOpen) {
  const decisions=decisionCollection(view);
  container.replaceChildren(...decisions.map(decision=>{
    const item=node('article',undefined,'decision-item');item.dataset.kind='decision';item.dataset.decisionId=decision.ref.id;
    const title=node('h4',semanticText('decision',decision.ref.id,t('decisionCard.title')));
    const facts=node('div',undefined,'decision-item-facts');
    const cost=node('div');cost.append(node('span',t('decisionCard.costLabel')),node('strong',view.display.trip));
    const impact=node('div');impact.append(node('span',t('decisionCard.bufferLabel')),node('strong',t('decisionCard.impactShort',{before:view.display.before_runway,after:view.display.after_runway})));
    facts.append(cost,impact);
    const open=node('button',t('decisionCard.open'),'inline-button');open.type='button';open.dataset.openDecision=decision.ref.id;if(onOpen)open.addEventListener('click',()=>onOpen(decision));
    item.append(title,facts,open);return item;
  }));
  return decisions;
}
export function renderGoals(container, view) {
  container.replaceChildren(...view.goals.map(goal=>{
    const item=node('article',undefined,'goal-item');item.dataset.kind='goal';item.dataset.goalId=goal.ref.id;
    item.append(node('h4',semanticText('goal',goal.ref.id,goal.title)),node('p',t('goal.cardTarget',{date:formatDate(goal.target_date),amount:money(goal.target_amount)})),node('small',t('goal.cardFlexible')));return item;
  }));
  return view.goals;
}
export function historyReason(history) {
  if(history.record.ref.version===1) return t('plan.reason.initial');
  return t(history.record.ref.version===2?'plan.reason.execution':'plan.reason.reality');
}
export function renderHistory(container, view, compact=false) {
  container.replaceChildren(...view.history.map(history=>{
    const current=history.record.ref.version===view.plan.ref.version;
    const item=node('li'); item.append(node('strong',t(current?'plan.versionCurrent':'plan.versionPreserved',{version:history.record.ref.version})),node('p',historyReason(history)));
    if(history.evidence_ref && !compact)item.append(node('small',t('plan.evidence',{ref:ref(history.evidence_ref)})));
    return item;
  }));
}
export function selectIfLine(container, months, focus=true) {
  container.dataset.selectedMonths=String(months);
  const branches=[...container.querySelectorAll('[data-if-months]')];
  branches.forEach(branch=>{const selected=Number(branch.dataset.ifMonths)===Number(months);branch.classList.toggle('selected',selected);branch.setAttribute('aria-pressed',String(selected));if(selected&&focus)branch.focus({preventScroll:true});});
  return branches.some(branch=>Number(branch.dataset.ifMonths)===Number(months));
}
export function renderIfLines(container, view, onSelect) {
  const accepted=isAccepted(view);
  container.replaceChildren(...view.lines.map(line=>{
    const archived=line.record.status==='archived'; const branch=node('button',undefined,`if-branch ${archived?'archived':'active'}`);branch.type='button';branch.dataset.ifMonths=String(line.projection.unemployment_months);
    const head=node('div',undefined,'branch-head'); head.append(node('span',t(archived?'futures.archived':'futures.active')),node('strong',t(line.projection.unemployment_months===1?'futures.month':'futures.months',{count:line.projection.unemployment_months})));
    const amounts=node('div',undefined,'branch-amounts'); amounts.append(node('span',t('futures.withTrip')),node('b',line.with_trip),node('small',t('futures.withoutTrip',{amount:line.without_trip})));
    branch.append(head,amounts);
    if(archived) branch.append(node('p',t('futures.contextArchived')));
    branch.addEventListener('click',()=>{selectIfLine(container,line.projection.unemployment_months,false);if(onSelect)onSelect(line.projection.unemployment_months);});
    return branch;
  }));
  if(container.dataset.selectedMonths) selectIfLine(container,Number(container.dataset.selectedMonths),false);
  return accepted;
}
export function renderProposal(view) {
  const list=$('proposal-changes'), goal=view.goals[0], action=view.action;
  const before=action?.change.previous_target_date || '2028-09-18', after=action?.change.proposed_target_date || '2029-09-18';
  list.replaceChildren(...[
    t('proposal.move',{before:formatDate(before),after:formatDate(after)}),
    t('proposal.outflow',{trip:view.display.trip}),
    t('proposal.version',{plan:t('plan.reference',{version:(action?.expected_plan_ref || view.plan.ref).version})})
  ].map(value=>node('li',value)));
  $('prepare').textContent=t(isExecuted(view)?'proposal.recorded':action?'proposal.ready':'proposal.review');
  $('prepare').hidden=Boolean(action)&&!isExecuted(view); $('authorize').hidden=!action||isExecuted(view);
  $('consent-note').textContent=t(isExecuted(view)?'proposal.note.done':view.authorization?'proposal.note.consent':action?'proposal.note.ready':'proposal.note.initial');
}
export function actionSummaries(view) {
  const summaries=[];
  for(const receipt of view.receipts){
    const consent=view.audit.records.find(record=>record.record_type==='PlanAuthorization'&&ref(record.ref)===ref(receipt.authorization_ref));
    const before=view.audit.records.find(record=>record.record_type==='Goal'&&ref(record.ref)===ref(receipt.before_goal_ref));
    const after=view.audit.records.find(record=>record.record_type==='Goal'&&ref(record.ref)===ref(receipt.after_goal_ref));
    if(consent)summaries.push(t('evidence.authorization',{ref:ref(consent.ref)}));
    if(before&&after)summaries.push(t('evidence.goal',{before:formatDate(before.target_date),after:formatDate(after.target_date)}));
    summaries.push(t('evidence.plan',{before:receipt.before_plan_ref.version,after:receipt.after_plan_ref.version}),t('evidence.execution',{outcome:semanticText('outcome',receipt.outcome)}));
  }
  if(view.reevaluation)summaries.push(t('evidence.event',{count:view.reevaluation.resolved_if_line_refs.length,version:view.reevaluation.after_plan_ref.version}));
  return summaries;
}
export function renderEvidence(list, empty, view) {
  const summaries=actionSummaries(view); list.replaceChildren(...summaries.map(value=>node('li',value)));
  empty.hidden=summaries.length>0; empty.textContent=t(view.authorization&&!isExecuted(view)?'activity.proposal':view.action?'activity.proposal':'activity.none');
}
export function auditRows(view) {
  const receipt=view.receipts[0];
  return [
    [t('audit.decision'),ref(view.decision.ref)],[t('audit.option'),ref(view.decision.selected_option_ref)],[t('audit.proposal'),ref(view.action?.ref)],[t('audit.authorization'),ref(view.authorization?.ref)],
    [t('audit.execution'),receipt?`${semanticText('outcome',receipt.outcome)} · ${receipt.recorded_at}`:t('audit.notRecorded')],[t('audit.fields'),receipt?receipt.changed_fields.map(value=>semanticText('field',value)).join(t('audit.separator')):t('audit.none')],[t('audit.event'),ref(view.reevaluation?.event_ref)],
    [t('audit.statePlan'),`${ref(view.state.ref)} / ${ref(view.plan.ref)}`],[t('audit.history'),t('audit.records',{records:view.audit.records.length,commits:view.revision})]
  ];
}
export function renderAudit(container, view) { container.replaceChildren(...auditRows(view).flatMap(([term,value])=>[node('dt',term),node('dd',value)])); }
export function pendingItems(view) { return view.pending_reasons.map(reason=>t(`pending.${reason}`)); }
export function localize(root=document){ applyTranslations(root); }


// An independent startup guard still runs if another ES-module import fails.
// The old Host may serve current files but retain an obsolete in-memory route table.
const finvisBootStatus=document.getElementById('finvis-boot-status');
if(finvisBootStatus){
  const bootZh=(()=>{try{return localStorage.getItem('finvis-language')==='zh-CN'||(!localStorage.getItem('finvis-language')&&navigator.language.startsWith('zh'));}catch{return navigator.language.startsWith('zh');}})();
  const bootText=(zh,en)=>bootZh?zh:en;
  let bootIncompatible=false;
  const bootFail=stale=>{
    if(stale)bootIncompatible=true;
    if(window.finvisBootReady&&!stale)return;
    finvisBootStatus.dataset.state='error';finvisBootStatus.setAttribute('role','alert');
    const box=document.createElement('div'),title=document.createElement('strong'),message=document.createElement('p'),retry=document.createElement('button');
    title.textContent=bootText('FINVIS 未能启动','FINVIS could not start');
    message.textContent=stale?bootText('旧版 FINVIS 服务仍在运行。请在原启动窗口按 Control+C，再重新双击启动器。','An older FINVIS service is still running. Stop it with Control+C in its original window, then launch FINVIS again.'):
      bootText('应用未能完成加载。请重新打开启动器；若问题持续，请检查启动窗口中的错误。','The app did not finish loading. Relaunch FINVIS; if this continues, check the launcher window for errors.');
    retry.type='button';retry.textContent=bootText('重试加载','Retry loading');retry.addEventListener('click',()=>location.reload());
    box.append(title,message,retry);finvisBootStatus.replaceChildren(box);
  };
  window.finvisMarkBootReady=()=>{if(bootIncompatible)return;window.finvisBootReady=true;finvisBootStatus.hidden=true;};
  fetch('/api/health',{cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
    if(!data?.source_fingerprint||data.source_fingerprint!==data.source_current)bootFail(true);
  }).catch(()=>bootFail(false));
  window.setTimeout(()=>bootFail(false),8000);
}
