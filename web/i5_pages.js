/* I5 read-only pages. Every view renders one I1 snapshot supplied by I4 Home. */
import {createIfSpace} from './if_space.js';
import {createFutureExperience} from './future_experience.js';
import {renderLifecycle} from './plan_reality.js';
import {language} from './i18n.js';
import {node} from './shared.js';
import {formatUserAmount} from './i4_home.js';
import {renderHistory} from './s2_history.js';
import {createHistoryActions} from './history_actions.js';
import {attachBaseline} from './decision_baseline.js';

const COPY={
  en:{context:'My Financial World',revision:'World revision',close:'Close',back:'Home',loading:'Reading the User World…',failed:'The User World could not be read. No Demo data was substituted.',emptyWorld:'No User World has been recorded in this local session.',unknown:'Unknown',date:'Time',amount:'Reported amount',decision:'Decisions',plan:'Plan',future:'Future',activity:'Records',more:'Show more',noDecisions:'No decisions are being considered yet.',question:'Question',considering:'Considering',decisionPending:'Financial impact, affordability and recommendation have not been evaluated.',contextLabel:'Relevant context',noPlan:'No evaluated User plan has been established yet.',planNotEvaluated:'A User plan has not been evaluated yet. No financial recommendation is available.',planRecord:'Current User plan record',planPending:'Financial impact, affordability and recommendation have not been evaluated.',planState:'Plan status',planEvaluation:'Evaluation status',planDecisions:'Linked decisions',planExpenses:'Linked planned expenses',legacyAllocations:'Historical goal allocation references are preserved; native Goal details are unavailable.',noFuture:'No future inflows, planned expenses or future Decisions have been recorded.',inflows:'Expected inflows · not current cash',expenses:'Planned expenses · not paid',pastInflows:'Realized or cancelled inflow records',pastExpenses:'Completed or cancelled expense records',futureDecisions:'Future decisions · not a cash flow',ifPending:'Financial Future Space and If Lines still need a supported Financial Engine evaluation. Demo If Lines are not User results.',noActivity:'No authoritative User activity has been recorded yet.',receipt:'Authorized World change',added:'Records introduced',retired:'Records retired',replacement:'Reviewed replacement',occurrence:'Actual occurrence',expense:'Expense',inflow:'Inflow',settlement:'Balance treatment',correction:'Corrected record',recorded:'Recorded',known:'Known date',window:'Date window',relative:'Relative time',unknownTime:'Time unknown',status:'Status',pending:'Pending review',noVerdict:'No financial verdict has been calculated.'},
  'zh-CN':{context:'我的财务世界',revision:'财务世界修订号',close:'关闭',back:'首页',loading:'正在读取用户财务世界…',failed:'无法读取用户财务世界；未用演示数据代替。',emptyWorld:'本地会话中尚未记录用户财务世界。',unknown:'未知',date:'时间',amount:'报告金额',decision:'决策',plan:'计划',future:'未来',activity:'记录',more:'查看更多',noDecisions:'目前还没有正在考虑的决策。',question:'待决定的问题',considering:'正在考虑',decisionPending:'财务影响、负担能力与建议尚未评估。',contextLabel:'相关背景',noPlan:'尚未建立经过评估的用户计划。',planNotEvaluated:'用户计划尚未经过评估，目前没有财务建议。',planRecord:'当前用户计划记录',planPending:'财务影响、负担能力与建议尚未评估。',planState:'计划状态',planEvaluation:'评估状态',planDecisions:'关联决策',planExpenses:'关联计划支出',legacyAllocations:'历史目标分配引用已保留；原生目标详情暂不可用。',noFuture:'尚无未来流入、计划支出或未来决策记录。',inflows:'预期流入 · 不是当前现金',expenses:'计划支出 · 尚未支付',pastInflows:'已实现或已取消的流入记录',pastExpenses:'已完成或已取消的支出记录',futureDecisions:'未来决策 · 不属于现金流',ifPending:'财务未来空间与 If Lines 仍需经过支持的财务引擎评估；演示路径不是用户结果。',noActivity:'尚无权威用户动态记录。',receipt:'已授权的财务世界变更',added:'新增记录',retired:'退出当前状态的记录',replacement:'经审阅的替换',occurrence:'实际发生',expense:'支出',inflow:'流入',settlement:'余额处理',correction:'已更正记录',recorded:'记录时间',known:'确定日期',window:'日期范围',relative:'相对时间',unknownTime:'时间未知',status:'状态',pending:'待审阅',noVerdict:'尚未计算财务判断。'}
};
Object.assign(COPY.en,{decisionPending:'No purchase recommendation yet.',planNotEvaluated:'Plan not evaluated yet.',noPlan:'No evaluated plan yet.',ifPending:'Hypothetical scenarios are explored with each Decision.',details:'Details',expenses:'Open planning estimates',inflows:'Expected inflows',futureDecisions:'Hypothetical · considering'});
Object.assign(COPY['zh-CN'],{decisionPending:'尚无购买建议。',planNotEvaluated:'计划尚未评估。',noPlan:'尚无经过评估的计划。',ifPending:'可在各项决策中探索假设分支。',details:'详情',expenses:'未完成的计划支出',inflows:'预期流入',futureDecisions:'假设 · 正在考虑'});
const L=key=>(COPY[language]||COPY.en)[key]||key;
const STATUS={
  en:{exploring:'Exploring',selected:'Selected',deferred:'Deferred',resolved:'Resolved',expected:'Expected',realized:'Realized',cancelled:'Cancelled',planned:'Planned',completed:'Completed',draft:'Draft',adopted:'Adopted record',active:'Active',pending:'Pending evaluation',current:'Current record',stale:'Stale',unallocated:'Source or coverage unresolved',applied_to_balance:'Applied to reviewed balance',already_in_balance:'Already included in reported balance',superseded:'Superseded'},
  'zh-CN':{exploring:'考虑中',selected:'已选择',deferred:'已延后',resolved:'已解决',expected:'预期',realized:'已实现',cancelled:'已取消',planned:'计划中',completed:'已完成',draft:'草稿',adopted:'已采纳的记录',active:'有效',pending:'待评估',current:'当前记录',stale:'已过期',unallocated:'来源或覆盖范围未确定',applied_to_balance:'已应用到经审阅余额',already_in_balance:'已包含在报告余额中',superseded:'已被取代'}
};
const status=value=>(STATUS[language]||STATUS.en)[value]||L('unknown');
const date=value=>new Intl.DateTimeFormat(language==='zh-CN'?'zh-CN':'en-GB',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));
export function userTime(value){
  if(!value)return L('unknownTime');
  if(value.kind==='known_date')return `${L('known')}: ${date(value.date_value)}`;
  if(value.kind==='date_window')return `${L('window')}: ${date(value.window_start)}–${date(value.window_end)}`;
  if(value.kind==='relative')return `${L('relative')}: ${value.source_text||L('unknownTime')}`;
  return L('unknownTime');
}
const refKey=value=>value?.ref?`${value.record_kind}:${value.ref.id}:${value.ref.version}`:'';
const line=(label,value)=>{const row=node('p',undefined,'i5-line');row.append(node('span',label),node('strong',value||L('unknown')));return row;};
const note=(value,state='insufficient')=>{const item=node('p',value,`i5-note i5-state i5-state-${state}`);item.dataset.state=state;item.setAttribute('role',state==='error'?'alert':'status');return item;};
const card=(title)=>{const item=node('article',undefined,'i5-card');item.append(node('h3',title));return item;};
const appendRows=(root,rows,make,limit=2)=>{rows.slice(0,limit).forEach(row=>root.append(make(row)));if(rows.length>limit){const rest=node('details',undefined,'i5-more');rest.append(node('summary',`${L('more')} (${rows.length-limit})`));rows.slice(limit).forEach(row=>rest.append(make(row)));root.append(rest);}};
const list=(root,rows,empty,make)=>{if(!rows.length)root.append(note(L(empty)));else appendRows(root,rows,make,3);};

export function renderUserPage(root,page,state,historyState={}){
  const baselineKey=`${JSON.stringify(state.home?.version)}:${language}`;
  const baselines=new Map([...root.querySelectorAll('.d1-baseline')].filter(el=>el.dataset.readKey===baselineKey).map(el=>[el.dataset.d1Decision,el]));
  root.replaceChildren();
  root.dataset.state='loading';
  const title=node('div',undefined,'i5-heading');title.append(node('h2',L(page)));
  
  root.append(node('p',L('context'),'i5-context'),title);
  if(state.readError){root.dataset.state='error';root.append(note(L('failed'),'error'));return;}
  if(!state.home){root.dataset.state='loading';const loading=note(L('loading'),'loading');loading.setAttribute('aria-busy','true');root.append(loading);return;}
  if(!state.home.initialized){root.dataset.state='empty';root.append(note(L('emptyWorld'),'empty'));return;}
  const p=state.home.projection;
  if(!p||p.revision!==state.home.version.revision){root.dataset.state='error';root.append(note(L('failed'),'error'));return;}
  root.dataset.state='ready';
  if(page==='decision'){
    if(!p.decisions.length){root.dataset.state='empty';root.append(note(L('noDecisions'),'empty'));return;}
    list(root,p.decisions,'noDecisions',decision=>{
      const item=card(decision.subject);item.append(node('strong',decision.amount?formatUserAmount(decision.amount):L('unknown'),'uf-page-amount'),node('p',status(decision.status),'uf-caption'));const detail=node('details');detail.append(node('summary',L('details')),line(L('question'),decision.question),line(L('date'),userTime(decision.timing)));item.append(detail);
      const contexts=p.contexts.filter(context=>context.lifecycle==='active'&&context.decision_refs.some(ref=>refKey(ref)===refKey(decision.ref)));
      if(contexts.length){const details=node('details');details.append(node('summary',L('contextLabel')));for(const context of contexts)details.append(note(`${context.statement} · ${userTime(context.timing)}`));item.append(details);}
      const retained=baselines.get(decision.ref.ref.id);
      if(retained)item.append(retained);
      else{attachBaseline(item,decision,p);item.querySelector('.d1-baseline').dataset.readKey=baselineKey;}
      return item;
    });const limit=node('details',undefined,'vh-limitation');limit.append(node('summary',language==='zh-CN'?'暂未评估资金承受能力 · 为什么？':'Affordability not evaluated · Why?'),note(L('planPending')));root.append(limit);
  }else if(page==='plan'){
    const plan=p.plan;
    const evaluated=plan?.evaluation_status==='current';
    if(!evaluated){root.dataset.state='insufficient';root.append(note(plan?L('planNotEvaluated'):L('noPlan'),'insufficient'));}
    const hasPlanDetails=plan&&(plan.status!=='draft'||plan.decision_refs.length||plan.expense_refs.length||plan.goal_allocations.length);
    if(plan&&(evaluated||hasPlanDetails)){const item=node('details',undefined,'i5-plan-details');item.append(node('summary',language==='zh-CN'?'计划详情与关联记录':'Plan details and related records'));const audit=node('details');audit.append(node('summary',language==='zh-CN'?'技术信息 · 版本':'Technical · version'),line(L('revision'),String(plan.ref.ref.version)));item.append(audit);const names=(refs,rows,getName)=>refs.map(ref=>rows.find(row=>refKey(row.ref)===refKey(ref))).filter(Boolean).map(getName);item.append(line(L('planState'),status(plan.status)),line(L('planEvaluation'),status(plan.evaluation_status)),line(L('planDecisions'),names(plan.decision_refs,p.decisions,row=>row.subject).join(' · ')||L('unknown')),line(L('planExpenses'),names(plan.expense_refs,p.planned_expenses,row=>row.label).join(' · ')||L('unknown')));root.append(item);if(plan.goal_allocations.length)root.append(note(L('legacyAllocations'),'insufficient'));}
    renderLifecycle(root,state.home.lifecycle);
  }else if(page==='future'){
    const activeInflows=p.future_inflows.filter(row=>row.status==='expected');
    const activeExpenses=p.planned_expenses.filter(row=>['planned','partially_realized'].includes(row.status));
    const hasFuture=activeInflows.length||activeExpenses.length||p.decisions.length;
    if(!hasFuture){root.dataset.state='empty';root.append(note(L('noFuture'),'empty'));}
    for(const [heading,rows] of [[L('inflows'),activeInflows],[L('expenses'),activeExpenses]]){
      if(!rows.length)continue;const group=node('section',undefined,'i5-group');group.append(node('h3',heading));appendRows(group,rows,row=>{const item=card(row.label);item.append(node('strong',formatUserAmount(row.amount),'uf-page-amount'),node('p',`${userTime(row.timing)} · ${status(row.status)}`,'uf-caption'));return item;});root.append(group);
    }
    for(const [heading,rows] of [[L('pastInflows'),p.future_inflows.filter(row=>row.status!=='expected')],[L('pastExpenses'),p.planned_expenses.filter(row=>!['planned','partially_realized'].includes(row.status))]]){
      if(!rows.length)continue;const history=node('details',undefined,'i5-more');history.append(node('summary',`${heading} (${rows.length})`));appendRows(history,rows,row=>{const item=card(row.label);item.append(node('strong',formatUserAmount(row.amount),'uf-page-amount'),node('p',`${userTime(row.timing)} · ${status(row.status)}`,'uf-caption'));return item;});root.append(history);
    }
    if(p.decisions.length){const group=node('section',undefined,'i5-group');group.append(node('h3',L('futureDecisions')));appendRows(group,p.decisions,row=>{const item=card(row.subject);item.append(node('strong',row.amount?formatUserAmount(row.amount):L('unknown'),'uf-page-amount'),node('p',userTime(row.timing),'uf-caption'));return item;});root.append(group);}
    if(hasFuture){root.dataset.state='insufficient';root.append(note(L('ifPending'),'insufficient'));}
  }else if(page==='activity'){
    if(historyState.error)root.dataset.state='error';
    else if(historyState.loading)root.dataset.state='loading';
    else if(historyState.history?.entries.length===0)root.dataset.state='empty';
    if(p.occurrences.length){const events=node('section',undefined,'uf-activity');for(const event of [...p.occurrences].reverse().slice(0,5)){const position=p.positions.find(x=>x.ref.ref.id===(event.source_position_ref||event.destination_position_ref)?.ref.id),account=p.accounts.find(x=>x.ref.ref.id===position?.account_ref.ref.id);const item=card(`${L(event.kind==='expense'?'expense':'inflow')}${account?' · '+account.label:''}`);item.append(node('strong',formatUserAmount(event.amount),'uf-page-amount'),node('p',userTime(event.timing),'uf-caption'),node('small',status(event.settlement_status)));events.append(item);}root.append(events);}
    renderHistory(root,historyState.history,historyState.actions||{},historyState.loading,historyState.error);
  }
}

export function createUserPages(i4){
  const mobile=!!document.querySelector('.mobile-app'),pages=['decision','plan','future','activity'];
  let active=null,future=null;const roots=new Map();
  let history=null,historyLoading=false,historyError=false,historyRequest=0;
  let dialog=null;
  const historyActions=createHistoryActions(i4,()=>loadHistory());
  if(mobile){
    const main=document.querySelector('#mobile-main');
    for(const page of pages){const view=node('section',undefined,'mobile-view i5-mobile-view');view.dataset.view=`user-${page}`;view.hidden=true;const back=node('button',`← ${L('back')}`,'mobile-back');back.type='button';back.addEventListener('click',()=>open('home'));const body=node('div',undefined,'i5-page');view.append(back,body);main.append(view);roots.set(page,body);}
  }else{
    dialog=node('section',undefined,'i5-persistent');dialog.hidden=true;dialog.id='i5-user-pages';const shell=node('div',undefined,'i5-shell');const header=node('header',undefined,'i5-head');const close=node('button','×','icon-button');close.type='button';close.setAttribute('aria-label',L('close'));close.addEventListener('click',()=>{active=null;open('home');});header.append(node('strong',L('context')),close);const nav=node('nav',undefined,'i5-tabs');for(const page of pages){const button=node('button',L(page));button.type='button';button.dataset.i5Page=page;button.addEventListener('click',()=>open(page));nav.append(button);const body=node('section',undefined,'i5-page');body.hidden=true;shell.append(body);roots.set(page,body);}shell.prepend(header,nav);dialog.append(shell);document.querySelector('#main').append(dialog);
  }
  function render(){
    // Keep field input stable; the action dialog redraws only its own transitions.
    if(mobile)document.querySelectorAll('.i5-mobile-view .mobile-back').forEach(button=>{button.textContent=`← ${L('back')}`;});
    else{dialog.querySelector('.i5-head strong').textContent=L('context');dialog.querySelector('.i5-head button').setAttribute('aria-label',L('close'));dialog.querySelectorAll('[data-i5-page]').forEach(button=>{button.textContent=L(button.dataset.i5Page);button.classList.toggle('active',button.dataset.i5Page===active);});}
    if(!active||active==='home')return;
    const current=i4.current();
    if(active==='activity'&&current.home?.initialized&&!historyLoading&&!historyError&&(!history||history.current_revision!==current.home.version.revision))loadHistory();
    renderUserPage(roots.get(active),active,current,{history,loading:historyLoading,error:historyError,actions:{select:revision=>loadHistory(revision),back:()=>{if(history)history={...history,detail:null};render();},retry:()=>loadHistory(),act:historyActions.open,resume:historyActions.resume}});
  }
  async function loadHistory(revision=null){
    const request=++historyRequest;historyLoading=true;historyError=false;render();
    try{
      const response=await fetch(`/api/user/history${revision===null?'':`?revision=${revision}`}`,{cache:'no-store'});
      if(!response.ok)throw new Error('history_unavailable');
      const data=await response.json();if(request===historyRequest&&i4.isUser()){history=data.history;historyError=false;}
    }catch{if(request===historyRequest){history=null;historyError=true;}}
    finally{if(request===historyRequest){historyLoading=false;render();}}
  }
  async function open(page){
    if(page==='home'){active=null;if(mobile){document.querySelectorAll('.mobile-view').forEach(view=>{const here=view.dataset.view==='home';view.hidden=!here;view.classList.toggle('active',here);});document.querySelectorAll('.bottom-nav button').forEach(button=>button.classList.toggle('active',button.dataset.go==='home'));}else dialog.hidden=true;if(i4.isUser())future?.home();return;}
    if(!i4.isUser()||!roots.has(page))return;
    if(page==='future'){active=null;future.open();return;}
    future?.beforeLegacy(page);
    active=page;
    if(mobile){document.querySelectorAll('.mobile-view').forEach(view=>{const here=view.dataset.view===`user-${page}`;view.hidden=!here;view.classList.toggle('active',here);});document.querySelectorAll('.bottom-nav button').forEach(button=>button.classList.toggle('active',button.dataset.go===page));}
    else{for(const [key,root] of roots)root.hidden=key!==page;dialog.hidden=false;}
    render();await i4.refresh();render();
  }
  future=(new URLSearchParams(location.search).get('interaction')==='legacy'?createFutureExperience:createIfSpace)(i4,{openLegacy:open});
  i4.subscribe(state=>{if(state.selected==='demo'){historyRequest++;history=null;historyLoading=false;open('home');}else render();});
  return {open,render:()=>{render();future.render();},interpret:future.interpret,resumeHistoryAction:historyActions.resume};
}
