/* History uses the same exact S1 renderer and separate D authorization/I4 Apply. */
import {language,t} from './i18n.js';
import {node} from './shared.js';
import {renderExactPreview} from './i3.js';
const COPY={
en:{edit:'Edit / Correct',remove:'Remove from current World',restore:'Restore based on this version',manage:'Current records linked to this version',current:'Changes affect the current World, never this historical evidence.',reason:'Reason for this change',field:'What should change?',value:'Proposed value',precision:'Precision',exact_reported:'Exact reported',approximate:'Approximate',range:'Range',unknown:'Unknown',lower:'Lower amount',upper:'Upper amount',unknownReason:'Why is the amount unknown?',preview:'Preview exact changes',authorize:'Authorize these exact changes',apply:'Apply authorized changes',cancel:'Cancel',close:'Close',ready:'FINVIS will make these changes',restoreNotice:'This creates a new version from the current World. All intervening history remains. Historical amounts are not a fresh balance report.',removeNotice:'Remove only from current use. Prior records and receipts remain.',freshness:'Evidence time is preserved; this does not confirm present-day freshness.',applied:'Applied · new World revision',receipt:'Receipt',working:'Working…',retry:'Check receipt / retry Apply',stale:'The World changed. Close and reopen History to review again.',invalid:'Check the proposed value and reason. Nothing was applied.',failed:'The action could not be completed. Check its status before retrying.',sessionLost:'No matching committed receipt was found. Pending reviews expire on restart; reopen History for a fresh review.',history:'Back to updated History',resume:'Resume pending History review',unresolved:'Unavailable',preserve:'Unchanged records preserved',source:'Based on historical revision',balance:'Balance',allocation:'Allocation',expected_amount:'Expected amount',amount:'Amount',purpose:'Purpose',label:'Account name',subject:'Decision subject',question:'Decision question',statement:'Context',protected_reality:'Actual or settled facts are protected. Reversal is not supported.',retired_identity_requires_reactivation:'This requires reactivating a retired identity; that contract is not supported.',unsupported_retirement:'This record has no supported removal lifecycle.',dependent_records:'Other current records depend on this record. Removal is unavailable.',already_matches:'The supported facts already match; no new operation is needed.',closed_record:'This record is already closed.',legacy_restore_unsupported:'A legacy migration baseline cannot be restored automatically.',empty_account_scope:'This account has no supported currency scope for retirement.'},
'zh-CN':{edit:'编辑／更正',remove:'从当前财务世界移除',restore:'基于此版本恢复',manage:'此版本关联的当前记录',current:'更改作用于当前财务世界，不修改这份历史证据。',reason:'本次更改原因',field:'要修改什么？',value:'拟议值',precision:'精度',exact_reported:'报告的精确值',approximate:'近似值',range:'范围',unknown:'未知',lower:'金额下限',upper:'金额上限',unknownReason:'金额未知的原因',preview:'预览精确更改',authorize:'授权这些精确更改',apply:'应用已授权的更改',cancel:'取消',close:'关闭',ready:'FINVIS 将进行以下更改',restoreNotice:'从当前财务世界创建一个新版本，期间所有历史仍会保留。历史金额不代表重新报告了当前余额。',removeNotice:'仅从当前使用中移除，先前记录与收据仍会保留。',freshness:'保留证据原有时间，不据此确认今天仍然有效。',applied:'已应用 · 新财务世界修订号',receipt:'收据',working:'处理中…',retry:'检查收据／重试应用',stale:'财务世界已变更，请关闭并重新打开历史以再次审阅。',invalid:'请检查拟议值及原因，尚未应用。',failed:'操作未能完成，请先检查状态再重试。',sessionLost:'未找到匹配的已提交收据。待处理审阅在重启后失效，请重新打开历史并审阅。',history:'返回更新后的历史',resume:'继续待处理的历史审阅',unresolved:'暂不可用',preserve:'保留未改变的记录',source:'基于历史修订号',balance:'余额',allocation:'用途分配',expected_amount:'预期金额',amount:'金额',purpose:'用途',label:'账户名称',subject:'决策主题',question:'决策问题',statement:'背景',protected_reality:'实际或已结算事实受到保护，不支持撤销收支。',retired_identity_requires_reactivation:'这需要重新激活已退役的身份；当前契约尚不支持。',unsupported_retirement:'此类记录尚无支持的移除生命周期。',dependent_records:'其他当前记录依赖此记录，暂不能移除。',already_matches:'支持的记录已经一致，无需创建新操作。',closed_record:'此记录已经关闭。',legacy_restore_unsupported:'不能自动恢复到旧版迁移基线。',empty_account_scope:'此账户没有可用于退役的货币范围。'}};
Object.assign(COPY.en,{event_edit:'Corrected current records',event_remove:'Removed from current World',event_restore:'Restored based on a historical version',plan_intent_restore_unsupported:'Restoring a different adopted plan or goal intent is not supported.'});
Object.assign(COPY['zh-CN'],{event_edit:'已更正当前记录',event_remove:'已从当前财务世界移除',event_restore:'已基于历史版本恢复',plan_intent_restore_unsupported:'不支持恢复不同的已采纳计划或目标意图。'});
COPY.en.decision_intent_restore_unsupported='Restoring a different decision selection or lifecycle is not supported.';
COPY['zh-CN'].decision_intent_restore_unsupported='不支持通过恢复改变决策选择或生命周期。';
export const historyText=k=>(COPY[language]||COPY.en)[k]||((language==='zh-CN')?'当前规则不支持此操作，请重新检查相关记录。':'The current contract cannot safely perform this action.');
const button=(text,fn)=>{const b=node('button',text);b.type='button';b.addEventListener('click',fn);return b;};
const field=(label,input)=>{const l=node('label',undefined,'history-field');l.append(node('span',label),input);return l;};
const decimal=v=>v==null?'':(BigInt(v)/100n).toString()+'.'+(BigInt(v)%100n).toString().padStart(2,'0');
const storage='finvis-history-pending';

export function renderHistoryControls(root,detail,act){
  if(!detail.actions||!act)return;
  const a=detail.actions,T=historyText;
  if(a.restore.available)root.append(button(T('restore'),()=>act('restore',a.binding,null)));
  else if(!detail.is_current)root.append(node('p',T(a.restore.reason),'s2-note'));
  if(detail.history_action){root.append(node('p',`${T(detail.history_action.action)} · ${T('source')} ${detail.history_action.source_revision}`,'s2-lineage'));}
  const group=node('details',undefined,'s2-snapshot history-controls');group.append(node('summary',T('manage')),node('p',T('current'),'s2-note'));
  const p=detail.projection,all=[...p.accounts,...p.positions,...p.earmarks,...p.future_inflows,...p.planned_expenses,...p.decisions,...p.contexts,...p.occurrences,...(p.monthly_essential_observations||[])];
  for(const row of a.records){
    const old=all.find(r=>r.ref.record_kind===row.source_ref.record_kind&&r.ref.ref.id===row.source_ref.ref.id);
    const label=(row.source_ref.record_kind==='monthly_essential_outflow'?`${t('agent.fact.monthly_essential_outflow')} · ${old?.amount?.amount?.currency||old?.amount?.lower_bound?.currency||old?.amount?.unknown_currency||''}`:null)||row.label||old?.label||old?.purpose||old?.subject||old?.statement||
      (old?.account_ref?p.accounts.find(a=>a.ref.ref.id===old.account_ref.ref.id)?.label:'')||T(row.source_ref.record_kind==='actual_occurrence'?'protected_reality':'unresolved');
    const item=node('section',undefined,'history-record');item.dataset.kind=row.source_ref.record_kind;item.append(node('strong',label));
    if(row.fields.length)item.append(button(T('edit'),()=>act('edit',a.binding,row)));
    if(row.remove)item.append(button(T('remove'),()=>act('remove',a.binding,row)));
    else if(row.reason)item.append(node('p',T(row.reason),'s2-note'));
    group.append(item);
  }
  root.append(group);
}

export function createHistoryActions(i4,refresh){
  const T=historyText,dialog=node('dialog',undefined,'i3-dialog');dialog.id='history-action-dialog';
  const head=node('header'),title=node('h2'),close=button('×',()=>dialog.close());close.className='i3-icon';head.append(title,close);
  const body=node('div',undefined,'i3-body'),foot=node('footer'),cancel=button(T('cancel'),async()=>{if(busy)return;if(lost){if(!message)sessionStorage.removeItem(storage);dialog.close();return;}try{if(pending()&&await recover()){render();return;}await post('cancel',{});sessionStorage.removeItem(storage);dialog.close();}catch{message=T('failed');render();}}),next=button('',()=>advance());
  foot.append(cancel,next);dialog.append(head,body,foot);document.body.append(dialog);
  let submissionDone=()=>{};
  let state=null,draft=null,collect=null,busy=false,message='',receipt=null,lost=false;
  const pending=()=>{try{return JSON.parse(sessionStorage.getItem(storage)||'null');}catch{return null;}};
  async function post(action,payload){const seed=await(await fetch('/api/state',{cache:'no-store'})).json();const response=await fetch('/api/user/history/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':seed.token},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Object.assign(Error(data.code||'failed'),{code:data.code,status:response.status});return data;}
  function render(){
    if(!dialog.open)return;
    title.textContent=T(lost?'unresolved':receipt?'applied':state?'ready':draft.action);close.setAttribute('aria-label',T('close'));cancel.textContent=T('cancel');next.disabled=busy;cancel.disabled=busy;next.hidden=false;
    body.replaceChildren();
    if(lost){body.append(node('p',message||T('sessionLost'),'i3-warning-text'));next.hidden=true;cancel.hidden=false;return;}
    if(receipt){body.append(node('p',`${T('applied')} ${receipt.receipt_revision}`),node('p',`${T('receipt')}: ${receipt.receipt.ref.id}`));next.textContent=T('history');cancel.hidden=true;return;}
    cancel.hidden=false;
    if(state){
      body.append(node('p',T(state.action==='restore'?'restoreNotice':state.action==='remove'?'removeNotice':'freshness'),'i3-warning-text'));
      body.append(node('p',`${T('source')} ${state.source_revision} → ${state.world_revision+1}`));
      body.append(renderExactPreview({...state,proposal:{review_mode:state.preview.destructive?'replace':'merge'},unknowns:[]}));
      body.append(node('p',`${T('preserve')}: ${state.preview.retained_refs.length}`,'s2-note'));
      const details=node('details');details.append(node('summary',T('source')),node('pre',JSON.stringify({source_root:state.source_root,source_digest:state.source_digest,reason:state.reason},null,2),'i3-proof'));body.append(details);
      next.textContent=T(state.stage==='authorized'?'apply':state.stage==='stale'?'stale':'authorize');next.disabled=busy||state.stage==='stale';
    }else{
      body.append(node('p',T('current'),'s2-note'));
      if(draft.action!=='edit')body.append(node('p',T(draft.action==='restore'?'restoreNotice':'removeNotice'),'i3-warning-text'));
      const reason=node('input');reason.value=draft.reason||'';reason.dataset.field='reason';reason.addEventListener('input',()=>draft.reason=reason.value);
      let getValue=()=>null,fieldName=()=>null;
      if(draft.action==='edit'){
        const choose=node('select');for(const f of draft.record.fields){const o=node('option',T(f.name));o.value=f.name;choose.append(o);}choose.value=draft.field||draft.record.fields[0].name;body.append(field(T('field'),choose));const inputs=node('div');body.append(inputs);
        const draw=()=>{draft.field=choose.value;inputs.replaceChildren();const v=draft.record.fields.find(f=>f.name===choose.value).value;
          if(v&&typeof v==='object'&&v.precision){const precision=node('select');for(const k of ['exact_reported','approximate','range','unknown']){const o=node('option',T(k));o.value=k;precision.append(o);}precision.value=v.precision;inputs.append(field(T('precision'),precision));
            const controls={};for(const [k,label,initial] of [['value','value',decimal(v.amount?.minor_units)],['lower','lower',decimal(v.lower_bound?.minor_units)],['upper','upper',decimal(v.upper_bound?.minor_units)],['reason','unknownReason',v.missing_reason||'']]){const input=node('input');input.dataset.field=k;input.value=initial;controls[k]=input;inputs.append(field(T(label),input));}
            const visibility=()=>{for(const [k,input] of Object.entries(controls))input.parentElement.hidden=k==='reason'?precision.value!=='unknown':k==='value'?['range','unknown'].includes(precision.value):!['range','approximate'].includes(precision.value);};
            precision.addEventListener('change',visibility);visibility();getValue=()=>Object.fromEntries([['precision',precision.value],...Object.entries(controls).map(([k,v])=>[k,v.value])]);
          }else{const input=node('input');input.value=v;input.dataset.field='value';inputs.append(field(T('value'),input));getValue=()=>input.value;}
        };choose.addEventListener('change',draw);draw();fieldName=()=>choose.value;
      }
      body.append(field(T('reason'),reason));collect=()=>({binding:draft.binding,action:draft.action,target:draft.record?.ref||null,field:fieldName(),value:getValue(),reason:reason.value});
      next.textContent=T('preview');
    }
    if(message){const p=node('p',message,'i3-warning-text');p.setAttribute('role','alert');body.prepend(p);}
  }
  async function recover(){
    const saved=pending();if(!saved)return false;
    const response=await fetch('/api/user/operation?authorization_id='+encodeURIComponent(saved.authorization_id),{cache:'no-store'});
    if(response.ok){const op=(await response.json()).operation;if(['applied','receipt_recovered'].includes(op.status)){lost=false;receipt=op;submissionDone();sessionStorage.removeItem(storage);await i4.refresh();await refresh();return true;}}
    if(!response.ok&&response.status!==404)throw Error('receipt unavailable');
    return false;
  }
  async function advance(){
    if(busy)return;if(receipt){dialog.close();await refresh();return;}
    // Collect before rerender, so malformed input remains editable.
    const input=state?null:collect();busy=true;next.disabled=true;message='';
    try{
      if(!state){state=(await post('prepare',input)).history_action;}
      else if(state.stage==='preview'){state=(await post('authorize',{preview_id:state.preview.id,digest:state.preview.digest})).history_action;sessionStorage.setItem(storage,JSON.stringify({preview_id:state.preview.id,digest:state.preview.digest,authorization_id:state.authorization.id}));}
      else if(state.stage==='authorized'){
        const args={preview_id:state.preview.id,digest:state.preview.digest,authorization_id:state.authorization.id};
        if(!await recover()){receipt=(await post('apply',args)).operation;submissionDone();sessionStorage.removeItem(storage);await i4.refresh();await refresh();}
      }
    }catch(error){
      if(state?.stage==='authorized'){try{await recover();}catch{}}
      if(!receipt){message=T(['world_changed','preview_stale','authorization_stale'].includes(error.code)?'stale':error.status===400?'invalid':error.code||'failed');if(['world_changed','preview_stale','authorization_stale'].includes(error.code)){if(state)state={...state,stage:'stale'};else lost=true;}}
    }finally{busy=false;if(state||receipt||lost)render();else{next.disabled=false;const p=node('p',message,'i3-warning-text');p.setAttribute('role','alert');body.prepend(p);}}
  }
  async function open(action,binding,record){submissionDone=()=>{};state=null;receipt=null;message='';lost=false;draft={action,binding,record,reason:''};dialog.showModal();render();}
  async function resume(done){submissionDone=typeof done==='function'?done:()=>{};receipt=null;message='';lost=false;try{const response=await fetch('/api/user/history/action',{cache:'no-store'});if(!response.ok)throw Error('read');const data=await response.json();state=data.history_action;lost=state.stage==='idle';if(!dialog.open)dialog.showModal();await recover();render();}catch{message=T('failed');lost=true;if(!dialog.open)dialog.showModal();render();}}
  return {open,resume,render};
}
