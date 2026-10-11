/* D1 derived estimates: no Apply, authorization, model, or financial writer. */
import {d3Post,d3Text,staleDetails,refreshButton} from './reality_evaluation.js';
import {language} from './i18n.js';
import {node} from './shared.js';
import {SEMANTIC_ICONS,semanticCue} from './semantic_icons.js';
import {attachIfLines} from './if_lines.js';
import {showPlanning,simpleText} from './simple_planning.js';

const COPY={
en:{title:'Decision baseline',empty:'No baseline established yet.',loading:'Reading baseline…',error:'Baseline could not be read or saved. Check the fields and refresh if its version changed.',estimate_available:'Estimate available',partially_available:'Partial estimate · coverage or freshness incomplete',insufficient_evidence:'More context or evidence needed',stale:'Stale · review changed inputs before using',cost:'Estimated cost',partial:'Reported components only',buffer:'Budget buffer · user allowance',recommended:'Recommended baseline budget',policy:'Unavailable · no approved budget recommendation policy',noBuffer:'Not set · automatic buffer policy unavailable',details:'View estimate details',edit:'Establish / revise baseline',save:'Save derived baseline',saving:'Saving…',add:'Add a cost component',remove:'Remove draft component',boundary:'Planning evidence, not spending or a financial recommendation.',retention:'Baselines are session-only derived estimates. Restarting clears them; reviewed financial records remain saved.',research:'Live price research is unavailable. You can supply a sourced quote or an explicit planning assumption.',missing:'Needed context',version:'Baseline version',refresh:'Refresh baseline',context:'Planning context',category:'Category',origin:'Origin',destination:'Destination',location:'Location',country_code:'Country code (optional, e.g. VN)',start_date:'Start / earliest date',end_date:'End / latest date',date_note:'Date window or timing uncertainty',duration_days:'Days',nights:'Nights',party_size:'People',assumptions:'Assumptions (one per line)',scope:'These components cover my stated cost scope',label:'Component name',basis:'Evidence basis',user_assumption:'My planning assumption',world:'Reviewed User record',external:'Sourced quote (manually supplied)',record:'Reviewed cost record',currency:'Native currency',precision:'Precision',exact:'Exact observed amount',approximate:'Approximate',range:'Range',unknown:'Unknown',value:'Amount / lower bound',upper:'Upper bound',source:'Source / quote location',provider:'Provider',evidence_ref:'Evidence reference',observed_at:'Observed at (ISO time with zone)',retrieved_at:'Retrieved at (ISO time with zone)',valid_until:'Valid until (optional; no invented lifetime)',freshness:'Source freshness',current:'Current according to source',unknownFresh:'Not established',staleFresh:'Stale',quote:'Original evidence',planning:'Planning estimate',bufferToggle:'Include my own explicit buffer allowance',reason:'Allowance reason',select:'Choose…',days:'days',nightLabel:'nights',other:'Other',travel:'Travel',flight:'Flight',accommodation:'Accommodation',dining:'Meals',local_transport:'Local transport',activity:'Activity / ticket',camera:'Camera',concert:'Live entertainment',phone:'Phone',emergency:'Emergency reserve',electronics:'Electronics',moving:'Moving / home setup',training:'Course / training',world_inputs_changed:'Relevant financial input changed',authority_changed:'Financial authority session changed',decision_context_changed:'Decision context changed',baseline_superseded:'A newer baseline exists',evidence_stale:'A quote has expired',dates:'Dates or window'},
'zh-CN':{title:'决策基线',empty:'尚未建立决策基线。',loading:'正在读取基线…',error:'无法读取或保存基线。请检查填写内容；若版本已变化，请刷新后重试。',estimate_available:'估算可用',partially_available:'部分估算 · 覆盖范围或时效尚不完整',insufficient_evidence:'还需要背景或证据',stale:'基线已过期 · 使用前请核对变化',cost:'预计成本',partial:'仅合计已提供的组成项',buffer:'预算缓冲 · 用户预留额度',recommended:'推荐基线预算',policy:'暂不可用 · 尚无已批准的预算推荐规则',noBuffer:'未设置 · 自动缓冲规则尚不可用',details:'查看估算依据',edit:'建立／修订基线',save:'保存推导基线',saving:'正在保存…',add:'添加成本组成项',remove:'移除此草稿项',boundary:'用于规划的证据，不是实际支出或财务建议。',retention:'基线是本次会话的推导估算，重启后会清除；已审阅的财务记录仍会保存。',research:'实时价格研究暂不可用。你可以提供有来源的报价，或明确填写自己的规划假设。',missing:'需要补充',version:'基线版本',refresh:'刷新基线',context:'规划背景',category:'类别',origin:'出发地',destination:'目的地',location:'地点',country_code:'国家代码（可选，例如 VN）',start_date:'开始／最早日期',end_date:'结束／最晚日期',date_note:'日期窗口或时间不确定性',duration_days:'天数',nights:'晚数',party_size:'人数',assumptions:'假设（每行一项）',scope:'这些组成项覆盖了我声明的成本范围',label:'组成项名称',basis:'依据类型',user_assumption:'我的规划假设',world:'经审阅的用户记录',external:'有来源的报价（手动提供）',record:'经审阅的成本记录',currency:'原生货币',precision:'精度',exact:'精确的观察金额',approximate:'近似值',range:'区间',unknown:'未知',value:'金额／下限',upper:'上限',source:'来源／报价位置',provider:'提供方',evidence_ref:'证据引用',observed_at:'观察时间（含时区的 ISO 时间）',retrieved_at:'获取时间（含时区的 ISO 时间）',valid_until:'有效期至（可选；不自动推测时效）',freshness:'来源时效',current:'来源表明当前有效',unknownFresh:'尚未确定',staleFresh:'已过期',quote:'原始证据',planning:'规划估算',bufferToggle:'加入我明确指定的缓冲额度',reason:'预留理由',select:'请选择…',days:'天',nightLabel:'晚',other:'其他',travel:'旅行',flight:'航班',accommodation:'住宿',dining:'餐饮',local_transport:'市内交通',activity:'活动／门票',camera:'相机',concert:'现场演出',phone:'手机',emergency:'应急储备',electronics:'电子产品',moving:'搬家／家居布置',training:'课程／培训',world_inputs_changed:'相关财务输入已变化',authority_changed:'财务权威会话已变化',decision_context_changed:'决策背景已变化',baseline_superseded:'已有更新的基线',evidence_stale:'报价已过期',dates:'日期或时间窗口'}
};
const L=k=>(COPY[language]||COPY.en)[k]||k;
Object.assign(COPY.en,{dependencies:'Financial records used as context (optional)',world_evidence_stale:'Reported financial evidence is stale',dependencyChanged:'Review dependencies at their current versions. Retired inputs must be explicitly deselected.'});
Object.assign(COPY['zh-CN'],{dependencies:'作为背景使用的财务记录（可选）',world_evidence_stale:'报告的财务证据已过期',dependencyChanged:'请核对依赖记录的当前版本；已退役的输入需要明确取消选择。'});
Object.assign(COPY.en,{currentEstimate:'Current estimate',estimateHeading:'Planning estimate',empty:'No estimate yet. Add the expected amount to explore alternatives.',reviewEstimate:'Add or review estimate',amount_unknown:'What did you originally expect this Decision to cost?',planning_context_needed:'This Decision needs more planning details before an If can be compared.',evidence_needs_review:'The earlier estimate needs review before it can be used.',stale:'Earlier estimate needs review',error:'Could not prepare the estimate. Retry or review its details.'});
Object.assign(COPY['zh-CN'],{currentEstimate:'当前估算',estimateHeading:'规划估算',empty:'还没有可用估算。补充原本预计金额后即可探索其他方案。',reviewEstimate:'补充或核对估算',amount_unknown:'你原本预计这项决策要花多少钱？',planning_context_needed:'这项决策还需要补充规划细节，才能比较不同方案。',evidence_needs_review:'先前的估算需要核对后才能继续使用。',stale:'先前估算需要核对',error:'暂时无法准备估算，请重试或核对详情。'});
const note=t=>node('p',t,'d1-note');
const line=(label,value)=>{const p=node('p',undefined,'d1-line');p.append(node('span',label),node('strong',value));return p;};
const button=(label,fn)=>{const b=node('button',label,'secondary-button');b.type='button';b.addEventListener('click',fn);return b;};
const amount=a=>{
  if(!a||a.precision==='unknown')return L('unknown');
  const currency=a.amount?.currency||a.lower_bound?.currency;
  const digits=m=>{const n=BigInt(m.minor_units),fraction=n%100n;return new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG').format(n/100n)+(fraction?'.'+String(fraction).padStart(2,'0'):'');};
  return a.precision==='range'?`${currency} ${digits(a.lower_bound)}–${digits(a.upper_bound)}`:`${a.precision==='approximate'?'≈ ':''}${currency} ${digits(a.amount)}`;
};
const lines=v=>(v||'').split('\n').map(x=>x.trim()).filter(Boolean);
const input=(value='',type='text')=>{const i=node('input');i.type=type;i.value=value??'';return i;};
function field(parent,key,control){const label=node('label',undefined,'d1-field');control.setAttribute('aria-label',L(key));label.append(node('span',L(key)),control);parent.append(label);return control;}
function select(options,value){const s=node('select');for(const [key,label] of options){const o=node('option',label);o.value=key;s.append(o);}s.value=value??'';return s;}
function textarea(value){const t=node('textarea');t.rows=2;t.value=value||'';return t;}
const moneyInput=a=>{
  const major=m=>m==null?null:`${BigInt(m)/100n}.${String(BigInt(m)%100n).padStart(2,'0')}`;
  return a?{currency:a.amount?.currency||a.lower_bound?.currency||a.unknown_currency,precision:a.precision==='exact_reported'?'exact':a.precision,value:major(a.amount?.minor_units),range_min:major(a.lower_bound?.minor_units),range_max:major(a.upper_bound?.minor_units)}:null;
};
function amountFields(parent,value=null){
  const box=node('div',undefined,'d1-grid');parent.append(box);
  const currency=field(box,'currency',select([['',L('select')],['CNY','CNY'],['SGD','SGD'],['EUR','EUR']],value?.currency));
  const precision=field(box,'precision',select(['approximate','range','exact','unknown'].map(k=>[k,L(k)]),value?.precision||'approximate'));
  const nominal=field(box,'value',input(value?.value||value?.range_min||''));nominal.inputMode='decimal';
  const upper=field(box,'upper',input(value?.range_max||''));upper.inputMode='decimal';
  const update=()=>{nominal.closest('label').hidden=precision.value==='unknown';upper.closest('label').hidden=precision.value!=='range';};precision.addEventListener('change',update);update();
  return ()=>({currency:currency.value,precision:precision.value,value:['exact','approximate'].includes(precision.value)?nominal.value:null,range_min:precision.value==='range'?nominal.value:null,range_max:precision.value==='range'?upper.value:null});
}

export function attachBaseline(parent,decision,projection){
  const section=node('section',undefined,'d1-baseline');section.dataset.d1Decision=decision.ref.ref.id;parent.append(section);
  section.addEventListener('finvis-baseline-ready',()=>refresh());
  let current=null,readiness=null,serial=0,timer=null;
  async function refresh(){
    const request=++serial;clearTimeout(timer);section.dataset.state='loading';section.setAttribute('aria-busy','true');section.replaceChildren(note(L('loading')));
    try{const response=await fetch(`/api/user/baseline?decision_id=${encodeURIComponent(decision.ref.ref.id)}`,{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(request!==serial)return;current=data.baseline;readiness=null;
      if(!current||!['estimate_available','partially_available'].includes(current.status)){
        const result=await d3Post('ifs/prepare',{decision_id:decision.ref.ref.id,text:null});if(request!==serial)return;
        readiness=result;current=result.baseline||current;
      }
      render();}
    catch{if(request===serial){section.dataset.state='error';const error=note(L('error'));error.setAttribute('role','alert');section.replaceChildren(error,button(L('refresh'),refresh));}}
    finally{if(request===serial)section.removeAttribute('aria-busy');}
  }
  function render(){
    section.replaceChildren();const b=current;section.dataset.state=b?.status||'empty';
    const c=b?.context;const identity=c?.category==='travel'?(c.destination||decision.subject):decision.subject;
    const time=c?.duration_days?` · ${c.duration_days} ${L('days')}${c.nights!=null?` / ${c.nights} ${L('nightLabel')}`:''}`:'';
    section.append(node('h4',b?L('currentEstimate'):L('estimateHeading')));
    if(!b)section.append(note(L(readiness?.reason||'empty')));
    else{
      const status=note(L(b.status));status.setAttribute('role','status');status.classList.add('vh-estimate-status');section.append(status);
      
      if(b.status==='stale'){section.append(note(L(readiness?.reason||'stale')));staleDetails(section,b);}
      if(b.missing_context.length)section.append(note(`${L('missing')}: ${b.missing_context.map(L).join(' · ')}`));
      if(b.status!=='stale'&&b.estimated_cost_by_currency.length)section.append(line(`${L('cost')}${b.cost_scope==='partial'?' · '+L('partial'):''}`,b.estimated_cost_by_currency.map(amount).join(' · ')));
      const details=node('details',undefined,'d1-details');details.append(node('summary',L('details')));
      details.append(note(L(b.status)),note(`${identity}${time}`),note([c.date_note,c.party_size?`${c.party_size} ${language==='zh-CN'?'人':'people'}`:''].filter(Boolean).join(' · ')),note(L('boundary')));const version=node('details',undefined,'vh-advanced');version.append(node('summary',language==='zh-CN'?'高级 · 版本与依赖':'Advanced · versions and dependencies'),line(L('version'),`v${b.ref.version}`),node('pre',JSON.stringify({ref:b.ref,dependencies:b.explicit_dependencies},null,2)));details.append(version);
      details.append(line(L('recommended'),L('policy')));
      if(b.status==='stale')details.append(note(d3Text('historical')));
      for(const r of b.dependency_details||[])if(r.amount||r.status)details.append(line(r.label||d3Text('context'),[r.status?d3Text(r.status):'',r.amount?amount(r.amount):''].filter(Boolean).join(' · ')));
      if(b.prior_versions?.length){const old=node('details',undefined,'d3-history');old.append(node('summary',d3Text('history')));for(const v of b.prior_versions){old.append(note(`${d3Text('historical')} · v${v.ref.version}`));for(const c of v.components)old.append(line(c.label,amount(c.planning_amount)));}details.append(old);}
      for(const component of b.components){const item=node('div',undefined,'d1-component');item.append(node('strong',`${semanticCue(component.category)} ${component.label}`),line(L('planning'),amount(component.planning_amount)),note(L(component.basis)));
        if(component.external){const e=component.external;item.append(line(L('quote'),amount(e.observed_amount)),line(L('source'),e.source),line(L('provider'),e.provider),line(L('evidence_ref'),e.evidence_ref),line(L('observed_at'),e.observed_at),line(L('retrieved_at'),e.retrieved_at),line(L('valid_until'),e.valid_until||L('unknownFresh')),line(L('freshness'),L(e.freshness==='unknown'?'unknownFresh':e.freshness==='stale'?'staleFresh':'current')));for(const a of e.assumptions)item.append(note(a));}
        for(const a of component.assumptions)item.append(note(a));details.append(item);
      }
      details.append(line(`🛡️ ${L('buffer')}`,b.budget_buffer.amount?amount(b.budget_buffer.amount):L('noBuffer')));
      if(b.buffer_reason)details.append(note(b.buffer_reason));for(const a of c.assumptions)details.append(note(a));
      details.append(note(L('research')),note(L('retention')));section.append(details);
      const expiry=b.components.map(x=>x.external?.valid_until).filter(Boolean).map(x=>new Date(x).getTime()).filter(x=>x>Date.now());
      if(expiry.length)timer=setTimeout(()=>{if(section.isConnected)refresh();},Math.min(2147483647,Math.max(100,Math.min(...expiry)-Date.now()+50)));
    }
    if(!b||b.status==='stale'||b.status==='insufficient_evidence')section.append(button(L('reviewEstimate'),()=>readiness?.reason==='evidence_needs_review'?editor():showPlanning({decisions:[{ref:decision.ref,label:decision.subject}]},refresh)));
    const advanced=node('details',undefined,'d1-advanced');advanced.append(node('summary',simpleText('advanced')),button(L('edit'),editor),button(L('refresh'),refresh));section.append(advanced);
    if(b){refreshButton(b.status==='stale'?section:advanced,b,refresh);if(['estimate_available','partially_available'].includes(b.status))attachIfLines(section,b,decision);}
  }
  function editor(){
    clearTimeout(timer);const baseline=current;const form=node('form',undefined,'d1-editor');form.append(node('h4',L('edit')),note(L('boundary')),note(L('research')),note(L('retention')));
    const controls={},grid=node('div',undefined,'d1-grid');form.append(grid);
    controls.category=field(grid,'category',select(Object.keys(SEMANTIC_ICONS).map(k=>[k,L(k)]),baseline?.context.category||'other'));
    for(const key of ['origin','destination','location','country_code','start_date','end_date','date_note','duration_days','nights','party_size'])controls[key]=field(grid,key,input(baseline?.context[key]??'',key.includes('_date')?'date':['duration_days','nights','party_size'].includes(key)?'number':'text'));
    const contextFields=()=>{for(const key of ['origin','destination','country_code','duration_days','nights','party_size'])controls[key].closest('label').hidden=controls.category.value!=='travel';};controls.category.addEventListener('change',contextFields);contextFields();
    controls.assumptions=field(form,'assumptions',textarea(baseline?.context.assumptions.join('\n')));
    const context=()=>Object.fromEntries([...Object.entries(controls).map(([k,i])=>[k,k==='assumptions'?lines(i.value):['duration_days','nights','party_size'].includes(k)?(i.value===''?null:Number(i.value)):i.value||null])]);
    const rows=node('div');form.append(rows);const getters=new Map();let next=0;
    const worldRecords=[...projection.decisions.map(d=>[d.ref,d.subject]),...projection.planned_expenses.map(e=>[e.ref,e.label])];
    const dependencyBox=node('details',undefined,'d1-details');dependencyBox.append(node('summary',L('dependencies')),note(L('dependencyChanged')));form.append(dependencyBox);
    const dependencies=[...projection.decisions,...projection.planned_expenses,...projection.future_inflows,...projection.accounts,...projection.earmarks,...(projection.monthly_essential_observations||[])];
    const refKey=r=>`${r.record_kind}:${r.ref.id}`;
    const previousDependencies=baseline?.explicit_dependencies||[];
    const dependencyControls=dependencies.map(row=>{const label=node('label',undefined,'d1-field'),control=input('','checkbox');control.checked=previousDependencies.some(r=>refKey(r)===refKey(row.ref));label.append(node('span',row.ref.record_kind==='monthly_essential_outflow'?(language==='zh-CN'?'用户报告的每月必要支出':'Reported monthly essential outflow'):row.subject||row.label||row.purpose),control);dependencyBox.append(label);return [control,row.ref];});
    for(const ref of previousDependencies.filter(r=>!dependencies.some(d=>refKey(d.ref)===refKey(r)))){const label=node('label',undefined,'d1-field'),control=input('','checkbox');control.checked=true;label.append(node('span',`${ref.ref.id} · ${L('stale')}`),control);dependencyBox.append(label);dependencyControls.push([control,ref]);}
    function add(existing=null){
      const id=existing?.id||`component-${Date.now()}-${++next}`,box=node('fieldset',undefined,'d1-component');box.append(node('legend',L('label')));rows.append(box);
      const label=field(box,'label',input(existing?.label||''));label.required=true;
      const category=field(box,'category',select(Object.keys(SEMANTIC_ICONS).map(k=>[k,L(k)]),existing?.category||'other'));
      const basis=field(box,'basis',select(['user_assumption','world','external'].map(k=>[k,L(k)]),existing?.basis||'user_assumption'));
      const assumption=field(box,'assumptions',textarea(existing?.assumptions?.join('\n')));
      const costBox=node('div');box.append(costBox);const cost=amountFields(costBox,existing?.amount||moneyInput(existing?.external?.observed_amount||existing?.observed_amount));
      const selected=existing?.world_ref;const world=field(box,'record',select([['',L('select')],...worldRecords.map(([ref,name],i)=>[String(i),name])],selected?String(worldRecords.findIndex(([r])=>JSON.stringify(r)===JSON.stringify(selected))):''));
      const sourceBox=node('div',undefined,'d1-grid');box.append(sourceBox);const evidence={};
      for(const key of ['source','provider','evidence_ref','observed_at','retrieved_at','valid_until'])evidence[key]=field(sourceBox,key,input(existing?.external?.[key]||''));
      evidence.freshness=field(sourceBox,'freshness',select([['unknown',L('unknownFresh')],['current',L('current')],['stale',L('staleFresh')]],existing?.external?.freshness||'unknown'));
      const toggle=()=>{world.closest('label').hidden=basis.value!=='world';costBox.hidden=basis.value==='world';sourceBox.hidden=basis.value!=='external';};basis.addEventListener('change',toggle);toggle();
      box.append(button(L('remove'),()=>{getters.delete(id);box.remove();}));
      getters.set(id,()=>({id,label:label.value,category:category.value,basis:basis.value,assumptions:lines(assumption.value),amount:basis.value==='user_assumption'?cost():null,world_ref:basis.value==='world'&&world.value!==''?worldRecords[Number(world.value)]?.[0]||null:null,external:basis.value==='external'?{...Object.fromEntries(Object.entries(evidence).map(([k,i])=>[k,i.value||null])),category:category.value,observed_amount:cost(),context:context(),assumptions:existing?.external?.assumptions||[]}:null}));
    }
    if(baseline)baseline.components.forEach(add);
    else if(decision.amount)add({id:'reported-decision',label:decision.subject,category:'other',basis:'world',world_ref:decision.ref,assumptions:[]});
    form.append(button(L('add'),()=>add()));
    const complete=field(form,'scope',input('','checkbox'));complete.checked=baseline?.scope_complete||false;
    const includeBuffer=field(form,'bufferToggle',input('','checkbox'));includeBuffer.checked=!!baseline?.buffer;
    const bufferBox=node('div');form.append(bufferBox);const buffer=amountFields(bufferBox,moneyInput(baseline?.buffer));const reason=field(bufferBox,'reason',input(baseline?.buffer_reason||''));bufferBox.hidden=!includeBuffer.checked;includeBuffer.addEventListener('change',()=>bufferBox.hidden=!includeBuffer.checked);
    const message=note('');message.setAttribute('role','status');const submit=node('button',L('save'),'primary-button');submit.type='submit';form.append(message,submit);
    form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;message.textContent=L('saving');try{
      const tokenResponse=await fetch('/api/state',{cache:'no-store'});if(!tokenResponse.ok)throw Error();const {token}=await tokenResponse.json();
      const request={decision_ref:decision.ref,expected_version:baseline?.ref.version||0,context:context(),components:[...getters.values()].map(get=>get()),dependency_refs:dependencyControls.filter(([control])=>control.checked).map(([,ref])=>ref),scope_complete:complete.checked,buffer:includeBuffer.checked?{amount:buffer(),reason:reason.value}:null};
      const response=await fetch('/api/user/baseline',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify(request)});if(!response.ok)throw Error();current=(await response.json()).baseline;serial++;render();
    }catch{message.textContent=L('error');}finally{submit.disabled=false;}});
    section.replaceChildren(form,button(L('refresh'),refresh));
  }
  refresh();
}
