/* I4 Home reads only the fresh I1 User projection selected by explicit UI context. */
import {renderDashboard} from './simple_surface.js';
import {renderLifecycle} from './plan_reality.js';
import {language,t} from './i18n.js';
import {$, node} from './shared.js';

const COPY={
  en:{context:'Financial data source',demo:'Synthetic Demo',user:'My Financial World',demoNote:'These figures belong to the separate synthetic Demo.',userNote:'Home, Decisions, Plan, Future and Activity read the same User World. Evaluations that need the Financial Engine remain pending.',previewUser:'The same live User World in a mobile view. Actions here update the desktop workspace.',emptyTitle:'Your financial world is ready when you are',emptyDescription:'FINVIS does not have enough financial information yet. Share only what you want to start.',emptyAction:'Tell FINVIS',empty:'FINVIS does not have enough financial information yet. No Demo data is shown here.',noAssets:'No current asset information is recorded yet.',assets:'Current assets',earmarked:'Earmarked within assets',unallocated:'Unallocated within assets',future:'Expected inflows, not current cash',planned:'Open planning estimates',noTotal:'No supported total; reported amounts are separate',decisions:'Considering',essentials:'Essential monthly outflow',essentialsReported:'User report · coverage not evaluated',essentialsMissing:'Not recorded',runway:'Runway',runwayUnavailable:'Not enough information to assess runway.',essentialsReason:'Essential monthly outflow has not been recorded.',eligibilityReason:'Emergency liquidity eligibility has not been assessed.',runwayPending:'Financial Engine evaluation is still required.',pending:'Pending review or calculation',receipt:'Authoritative receipts',revision:'World revision',loading:'Reading your Financial World…',readFailed:'Your saved Financial World could not be verified or refreshed. User changes are unavailable. Resolve the storage issue and restart FINVIS; no Demo data was substituted.',approx:'about',range:'range',session:'Applied changes are saved on this Mac and recovered when FINVIS restarts. Pending reviews and authorizations expire.',demoOnly:'This action belongs to the separate synthetic Demo.'},
  'zh-CN':{context:'财务数据来源',demo:'合成演示',user:'我的财务世界',demoNote:'此处数字属于独立的合成演示。',userNote:'首页、决策、计划、未来和动态读取同一份用户财务世界；需要财务引擎的评估仍待完成。',previewUser:'同一份实时用户财务世界，以移动端视图呈现。这里的操作会同步到桌面工作台。',emptyTitle:'你的财务世界，等你准备好再开始',emptyDescription:'FINVIS 目前还没有足够的财务信息。你可以从愿意分享的内容开始。',emptyAction:'告诉 FINVIS',empty:'FINVIS 目前还没有足够的财务信息。这里不会显示演示数据。',noAssets:'尚未记录当前资产信息。',assets:'当前资产',earmarked:'资产内的预留款',unallocated:'资产内未分配部分',future:'预期流入（不是当前现金）',planned:'尚未结束的规划估计',noTotal:'没有可计算的合计；下列报告金额彼此分开',decisions:'正在考虑',essentials:'必要月支出',essentialsReported:'用户报告 · 覆盖范围尚未评估',essentialsMissing:'尚未记录',runway:'资金可支撑时间',runwayUnavailable:'信息不足，暂时无法评估资金可支撑时间。',essentialsReason:'尚未记录必要月支出。',eligibilityReason:'尚未评估哪些资金可用于应急。',runwayPending:'仍需财务引擎进行评估。',pending:'待审阅或待计算',receipt:'权威收据',revision:'财务世界修订号',loading:'正在读取你的财务世界…',readFailed:'暂时无法验证或刷新已保存的财务世界，用户更改不可用。请解决存储问题后重启 FINVIS；未用演示数据代替。',approx:'约',range:'范围',session:'已应用的更改保存在这台 Mac 上，重启 FINVIS 后会恢复。待处理的审阅和授权会失效。',demoOnly:'此操作只属于独立的合成演示。'}
};
const L=key=>(COPY[language]||COPY.en)[key]||key;
Object.assign(COPY.en,{userNote:'Your recorded finances · saved on this Mac',future:'Expected inflows',planned:'Planned expenses',accounts:'Where your money is',included:'Reserved within this balance',details:'Details and availability',decisions:'Considering'});
Object.assign(COPY['zh-CN'],{userNote:'你记录的财务信息 · 保存在这台 Mac',future:'预期流入',planned:'计划支出',accounts:'资金分布',included:'已包含在此余额内',details:'详情与可用信息',decisions:'正在考虑'});
COPY.en.unknown='Unknown';COPY['zh-CN'].unknown='未知';
export const formatUserAmount=value=>{
  const currency=value?.amount?.currency||value?.lower_bound?.currency||value?.unknown_currency;
  if(!value||value.precision==='unknown'||!currency)return L('unknown');
  const format=minor=>new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG',{minimumFractionDigits:0,maximumFractionDigits:2}).format(minor/100);
  if(value.precision==='range'&&value.lower_bound&&value.upper_bound)return `${currency} ${format(value.lower_bound.minor_units)}–${format(value.upper_bound.minor_units)}`;
  if(!value.amount)return L('unknown');
  return `${value.precision==='approximate'?L('approx')+' ':''}${currency} ${format(value.amount.minor_units)}`;
};
const amount=formatUserAmount;
const line=(label,value)=>{const row=node('div',undefined,'i4-line');row.append(node('span',label),node('strong',value));return row;};
const hasUserRecords=projection=>['currencies','accounts','positions','earmarks','future_inflows','planned_expenses','decisions','contexts','occurrences','receipt_refs'].some(key=>Array.isArray(projection[key])&&projection[key].length>0);
const stateNote=(text,state,className='i4-empty')=>{const element=node('p',text,`${className} i4-state`);element.dataset.state=state;element.setAttribute('role',state==='error'?'alert':'status');return element;};
function reportedTotals(totals){
  if(!totals.length)return L('unknown');
  return totals.map(row=>row.amount.value.precision==='unknown'?`${row.currency} · ${language==='zh-CN'?'合计暂不可用':'Total unavailable'}`:amount(row.amount.value)).join(' · ');
}
function summaryCard(label,totals,records,semantic){
 const card=node('article');card.dataset.semantic=semantic;card.append(node('h3',label),node('p',reportedTotals(totals)));
 if(records.length){const detail=node('details',undefined,'vh-summary-items');detail.append(node('summary',language==='zh-CN'?'查看项目':'View items'));for(const record of records)detail.append(line(record.label,amount(record.amount)));card.append(detail);}
 return card;
}

export function createUserHome({showFeedback}){
  let selected=new URLSearchParams(location.search).get('interaction')==='legacy'?'demo':'user',home=null,readError=false,serial=0;
  const competition=window.finvisEnvironment?.kind==='competition';
  const listeners=new Set();
  const notify=()=>listeners.forEach(listener=>listener({selected,home,readError}));
  try{const saved=sessionStorage.getItem('finvis-home-context');if(saved==='user'||saved==='demo')selected=saved;}catch{}
  if(competition)selected='user';
  const root=$('home')||$('mobile-home');
  const selector=node('div',undefined,'i4-context');selector.id='i4-context';selector.setAttribute('role','group');
  const demoButton=node('button');demoButton.type='button';demoButton.id='i4-demo';
  const userButton=node('button');userButton.type='button';userButton.id='i4-user';
  const contextNote=node('p',undefined,'i4-context-note');contextNote.id='i4-context-note';
  selector.append(demoButton,userButton,contextNote);
  root.querySelector('.home-intro,.mobile-intro').after(selector);
  const section=node('section',undefined,'i4-user-home');section.id='i4-user-home';section.setAttribute('aria-live','polite');
  $('finvis-command').after(section);
  const focusComposer=()=>{const composer=$('finvis-command'),input=$('finvis-input');composer?.scrollIntoView({behavior:'smooth',block:'center'});input?.focus({preventScroll:true});};
  const demoSurface=root.querySelector('.home-composition')||null;
  const mobileSurfaces=[...root.querySelectorAll('.mobile-state-block,.mobile-progress,.mobile-observation')];
  const unavailable=()=>root.querySelectorAll('.command-chips');
  function select(next,{read=true}={}){
    selected=competition||next==='user'?'user':'demo';serial++;
    try{sessionStorage.setItem('finvis-home-context',selected);}catch{}
    if(selected==='user'){
      home=null;readError=false;
      for(const dialog of ['workspace','state-detail','goals-detail'])if($(dialog)?.open)$(dialog).close();
      document.querySelectorAll('.mobile-view').forEach(view=>{const active=view.dataset.view==='home';view.hidden=!active;view.classList.toggle('active',active);});
      document.querySelectorAll('.bottom-nav button').forEach(button=>button.classList.toggle('active',button.dataset.go==='home'));
    }
    render();notify();if(selected==='user'&&read)refresh();
  }
  function render(){
    selector.setAttribute('aria-label',L('context'));demoButton.textContent=L('demo');userButton.textContent=L('user');
    for(const [button,key] of [[demoButton,'demo'],[userButton,'user']]){button.classList.toggle('active',selected===key);button.setAttribute('aria-pressed',String(selected===key));}
    contextNote.textContent=L(selected==='user'?'userNote':'demoNote');
    const previewCopy=document.querySelector('[data-i18n="preview.copy"]');
    if(previewCopy)previewCopy.textContent=selected==='user'?L('previewUser'):t('preview.copy');
    section.hidden=selected!=='user';if(demoSurface)demoSurface.hidden=selected==='user';
    mobileSurfaces.forEach(surface=>surface.hidden=selected==='user');
    if(document.querySelector('.rail-context'))document.querySelector('.rail-context').hidden=selected==='user';
    const demoFooter=document.querySelector('body > footer');
    if(demoFooter)demoFooter.hidden=selected==='user';
    unavailable().forEach(chips=>chips.hidden=selected==='user');
    document.querySelectorAll('.rail-nav button,.bottom-nav button').forEach(button=>{button.disabled=false;button.title='';});
    if(selected!=='user')return;
    section.replaceChildren();
    if(readError){section.dataset.state='error';section.append(stateNote(L('readFailed'),'error','i4-read-failed'));return;}
    if(home===null){section.dataset.state='loading';const loading=stateNote(L('loading'),'loading');loading.setAttribute('aria-busy','true');section.append(loading);return;}
    if(!home.initialized){section.dataset.state='empty';const empty=node('div',undefined,'i4-empty-primary i4-state');empty.dataset.state='empty';empty.append(node('h2',L('emptyTitle')),node('p',L('empty')));const action=node('button',L('emptyAction'),'i4-empty-action');action.type='button';action.addEventListener('click',focusComposer);empty.append(action);section.append(empty);return;}
    const p=home.projection;
    if(!p||home.version?.revision!==p.revision){section.dataset.state='error';section.append(stateNote(L('readFailed'),'error','i4-read-failed'));return;}
    if(!hasUserRecords(p)){section.dataset.state='empty';const empty=node('div',undefined,'i4-empty-primary i4-state');empty.dataset.state='empty';empty.append(node('h2',L('emptyTitle')),node('p',L('emptyDescription')));const action=node('button',L('emptyAction'),'i4-empty-action');action.type='button';action.addEventListener('click',focusComposer);empty.append(action);section.append(empty);return;}
    section.dataset.state='ready';
    renderDashboard(section,home,amount);
  }
  async function refresh(){
    const current=++serial;
    try{
      const response=await fetch('/api/user/home',{cache:'no-store'});if(!response.ok)throw new Error('read');
      const data=await response.json();if(current!==serial)return false;
      if(data.home?.initialized&&data.home.version?.revision!==data.home.projection?.revision)throw new Error('version');
      home=data.home;readError=false;render();notify();return true;
    }catch{if(current===serial){home=null;readError=true;render();notify();}return false;}
  }
  async function applied(){select('user',{read:false});const okay=await refresh();if(!okay)showFeedback(L('readFailed'),true);return okay;}
  demoButton.addEventListener('click',()=>select('demo'));
  userButton.addEventListener('click',()=>select('user'));
  window.addEventListener('finvis-names-changed',()=>{render();notify();});
  window.addEventListener('finvis-presentation-changed',()=>{render();notify();});
  window.addEventListener('storage',event=>{if(event.key?.startsWith('finvis-display-v1:')){render();notify();}});
  window.addEventListener('focus',()=>{if(selected==='user')refresh();});
  render();if(selected==='user')refresh();
  return {render,refresh,applied,isUser:()=>selected==='user',demoOnlyMessage:()=>L('demoOnly'),select,
    current:()=>({selected,home,readError}),subscribe:listener=>{listeners.add(listener);listener({selected,home,readError});return ()=>listeners.delete(listener);}};
}
