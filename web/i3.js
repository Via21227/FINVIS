/* I3 User World review. All financial meaning and mutation rules stay in Core. */
import {captureComposerCompletion,cloudHeaders,providerMessage} from './shared.js';
import {createCompoundMeaning} from './compound_meaning.js';
import {meaningStage,hasDecision,hasFacts,userFirstText as U} from './user_first_review.js';
import {showRealityRoute} from './reality_evaluation.js';
import {showLifecycle} from './plan_reality.js';
import {showPlanning} from './simple_planning.js';
import {language} from './i18n.js';
import {$, node, renderAgentCandidate} from './shared.js';
import {clarificationForm, interactionText} from './interaction_review.js';

const COPY={
  en:{open:'Review proposed changes',review:'Review evidence',proposal:'Proposed changes',preview:'Exact preview',authorized:'Authorized · ready to apply',understanding:'Understanding',idle:'No User World review in progress',stale:'Review again',close:'Close',cancel:'Cancel this review',next:'Review proposed changes',submit:'Create reviewed proposal',makePreview:'Create exact preview',authorize:'Authorize these exact changes',finish:'Done',back:'Back to understanding',stage:'Stage',home:'Home still shows the separate synthetic Demo. The User World has not been updated.',stop:'Authorization is registered, but application is not enabled in this prototype stage. No balance, World revision, or Home value changed.',interpretationStopped:'Interpretation stopped. Your draft is ready to edit.',interpretationStopUnknown:'Stopped waiting locally, but server cancellation could not be confirmed. The draft is preserved.',interpretationTooLate:'This request already advanced to review. Check the current review state.',candidate:'FINVIS understood {n} reported items. Review the evidence before any proposal exists.',understandingNext:'Next: review your evidence, inspect the exact proposal and preview, then authorize separately.',understandingBoundary:'This understanding is not a financial update. Your User World and Home are unchanged.',understandingReplace:'You requested a replacement. The exact current records must be chosen during review.',understandingUpdate:'You requested a financial update. Its exact effect still needs review.',reviewIntro:'Choose an excerpt from your own words for each meaning. No model call is needed after this point.',factEvidence:'Where did you say this?',accountEvidence:'Where did you name this account?',account:'Account',position:'Balance composition',balance:'Count as a balance',allocation:'Also reserve for its purpose (not added to balance)',composition:'How do these amounts combine?',total:'One total balance',disjoint:'Separate amounts to add once',role:'Purpose of this reserve',roleOther:'Other purpose',roleEmergency:'Emergency reserve',rolePurchase:'Purchase fund',emergency:'Explicitly designated for emergency use',funding:'Funded from reviewed reserve',decision:'Related decision',none:'None selected',coverage:'What does this report cover? (optional)',uncertainty:'I reviewed this uncertainty',replaceTitle:'Replacement request needs an exact scope',replace:'Replace selected current records',merge:'Add reviewed facts only; do not clear existing records',selectScope:'Choose current accounts and currencies to replace. History remains preserved.',noScope:'No current User World accounts exist to replace. Choose add-only explicitly or revise your request.',currency:'Currency',selectEvidence:'Select an excerpt',selectComposition:'Select balance meaning',current:'Current User World',unknown:'Unknown / not recorded',before:'Before',after:'After',changes:'What changes',preserved:'What stays',unresolved:'What remains unknown',details:'Change details',evidence:'Evidence and audit',warning:'Destructive replacement: selected current records would be retired only after a later application step.',balanceEffect:'Current balance observations would change if later applied.',noBalanceEffect:'No current balance observation would change.',actual:'An actual occurrence needs separate settlement review; no balance was changed.',needsReview:'More review is required before a proposal can be made.',blocked:'This proposal is blocked. Review the requested evidence or revise your text.',network:'The request was interrupted. Refresh the review status before trying again.',staleMessage:'This Candidate or User World version changed. Start a new review; nothing was applied.',invalid:'Review evidence is incomplete. Check the highlighted choices.',provider:'Local understanding is unavailable. Your draft and financial state are unchanged.',empty:'Nothing to display.',add:'Add',change_replace:'Change / replace',remove_retire:'Remove / retire',preserve:'Preserve',unresolvedAction:'Unresolved',noFinancialEffect:'No financial effect',settlement:'Settlement review required',currentAssets:'Current assets',earmarks:'Earmarks',futureInflows:'Future inflows',planned:'Planned expenses',decisions:'Decisions',context:'Context',actuals:'Actual occurrences',history:'Preserved history',items:'{n} proposed items',scope:'Exact replacement scope',version:'User World revision',digest:'Exact preview digest',waiting:'Working…',issueScope:'Choose the exact current accounts and currencies to replace, or explicitly choose add-only.',issueAccount:'Confirm which account owns this balance and how the amounts combine.',issueCoverage:'Describe which reported accounts and balances this review covers.',issueUncertainty:'Acknowledge the uncertainty before continuing.',issueSource:'Which current balance is the source or destination of this actual occurrence?',issueBalanceCoverage:'Is this occurrence already included in the reported balance, or should it be posted separately?',issueGeneric:'More explicit evidence or clarification is needed for this item.',reviewAgain:'Review the proposed meaning again',source:'Source excerpt',target:'Correction of current record (optional)',reason:'Reason for correction',actualPosition:'Current balance source or destination',basis:'Balance coverage',basisUnknown:'Not established',basisExclusive:'Separate transaction to post',basisInclusive:'Already included in reported balance',coverageSource:'Evidence for balance coverage',coverageReason:'Coverage explanation',destructive:'Replacement request',zero:'No authoritative change has occurred.'},
  'zh-CN':{open:'审阅拟议更改',review:'审阅依据',proposal:'拟议更改',preview:'精确预览',authorized:'已授权 · 等待应用',understanding:'理解内容',idle:'目前没有用户财务世界审阅',stale:'重新审阅',close:'关闭',cancel:'取消本次审阅',next:'审阅拟议更改',submit:'生成已审阅提案',makePreview:'生成精确预览',authorize:'授权这些精确更改',finish:'完成',back:'返回理解内容',stage:'阶段',home:'首页仍显示独立的合成演示数据。用户财务世界尚未更新。',stop:'授权已登记，但此原型阶段尚未启用应用操作。余额、财务世界修订号及首页数字均未改变。',candidate:'FINVIS 理解了 {n} 项陈述。审阅依据后才能生成提案。',understandingNext:'下一步：审阅原文依据、检查精确提案和预览，再单独授权。',understandingBoundary:'目前只是理解内容，并未更新财务数据。用户财务世界与首页均未改变。',understandingReplace:'你提出替换请求。审阅时必须明确选定现有记录范围。',understandingUpdate:'你提出财务更新请求，具体影响仍需审阅。',reviewIntro:'请为每项含义选择你原话中的证据。此后的步骤无需再次调用模型。',factEvidence:'你在何处提到这项内容？',accountEvidence:'你在何处提到这个账户？',account:'账户',position:'余额组成',balance:'计入余额',allocation:'同时指定用途（不重复计入余额）',composition:'这些金额如何组成余额？',total:'一个总余额',disjoint:'互不重叠，合计一次',role:'这笔预留款的用途',roleOther:'其他用途',roleEmergency:'应急储备',rolePurchase:'购买资金',emergency:'明确指定可作应急用途',funding:'由已审阅的预留款支付',decision:'关联的决策',none:'未选择',coverage:'这份陈述覆盖哪些范围？（可选）',uncertainty:'我已审阅这项不确定内容',replaceTitle:'替换请求需要精确范围',replace:'替换选定的现有记录',merge:'仅新增已审阅事实；不清除现有记录',selectScope:'请选择要替换的现有账户和货币。历史记录仍会保留。',noScope:'用户财务世界中还没有可替换的现有账户。请明确选择“仅新增”，或修改陈述。',currency:'货币',selectEvidence:'选择原文片段',selectComposition:'选择余额含义',current:'当前用户财务世界',unknown:'未知／尚未记录',before:'之前',after:'之后',changes:'会改变什么',preserved:'保持不变',unresolved:'仍未知的内容',details:'更改详情',evidence:'证据与审计详情',warning:'破坏性替换：选定的现有记录只有在之后的应用步骤才会退役。',balanceEffect:'若日后应用，当前余额观察值将改变。',noBalanceEffect:'当前余额观察值不会改变。',actual:'实际发生的收支需要单独结算审阅；余额尚未改变。',needsReview:'需要补充审阅才能生成提案。',blocked:'提案仍被阻止。请核对所需证据或修改原文。',network:'请求中断。重试前请刷新审阅状态。',staleMessage:'候选内容或用户财务世界版本已变化。请重新审阅；没有应用任何更改。',invalid:'审阅依据尚不完整，请检查所选内容。',provider:'本地理解功能暂不可用。草稿与财务状态均未改变。',empty:'没有可显示内容。',add:'新增',change_replace:'更改／替换',remove_retire:'移除／退役',preserve:'保留',unresolvedAction:'未解决',noFinancialEffect:'无即时财务影响',settlement:'需要结算审阅',currentAssets:'当前资产',earmarks:'预留款',futureInflows:'未来流入',planned:'计划支出',decisions:'决策',context:'背景',actuals:'实际收支',history:'保留的历史',items:'拟议 {n} 项',scope:'精确替换范围',version:'用户财务世界修订号',digest:'精确预览摘要',waiting:'处理中…',issueScope:'请选择要替换的精确现有账户及货币，或明确选择“仅新增”。',issueAccount:'请确认余额所属账户以及金额如何组成。',issueCoverage:'请说明本次审阅覆盖了哪些账户和余额。',issueUncertainty:'请确认已审阅不确定内容。',issueSource:'这笔实际收支来自哪个当前余额，或进入哪个当前余额？',issueBalanceCoverage:'这笔收支已包含在报告余额中，还是需要单独入账？',issueGeneric:'此项还需要更明确的依据或澄清。',reviewAgain:'重新审阅拟议含义',source:'原文片段',target:'修正现有记录（可选）',reason:'修正原因',actualPosition:'当前余额的来源或去向',basis:'余额覆盖关系',basisUnknown:'尚未确定',basisExclusive:'另行入账的交易',basisInclusive:'已包含在报告余额中',coverageSource:'余额覆盖关系的证据',coverageReason:'覆盖关系说明',destructive:'替换请求',zero:'尚无权威财务变更。'}
};
COPY.en.interpretationUnverified='FINVIS could not reliably understand all parts of this description. Your original text is preserved; try clarifying it.';
COPY['zh-CN'].interpretationUnverified='FINVIS 暂时无法可靠理解这条描述的全部内容。原文已保留，你可以补充说明后重试。';
const I4_COPY={
  en:{coverageRequired:'Which reported accounts and balances does this review cover? (required)',homePending:'Home is unchanged until authorized changes are applied. Demo and My Financial World remain separate.',apply:'Apply authorized changes',applying:'Applying authorized changes…',applied:'Changes applied',recovered:'Receipt recovered: these changes were already applied. No second operation ran.',authorizedReady:'This exact change is authorized but has not been applied. Applying it will update My Financial World and create an authoritative receipt.',appliedCopy:'Changes applied to My Financial World. Home now reads the authoritative World.',appliedReceipt:'Authoritative receipt',appliedRevision:'New World revision',refreshFailed:'The commit succeeded and its receipt is available, but Home could not refresh. Retry the read; do not apply again.',notCommitted:'No receipt was recorded. The same authorized operation can be retried if the review is still current.',outcomeUnknown:'The result cannot be verified right now. Check the operation status before trying to apply again.',checkOutcome:'Check application status',sessionLost:'No matching committed receipt was found in this store. Pending reviews expire on restart; no automatic retry was made.',authorizationStale:'Authorization is stale. No change was applied; review and authorize the current World again.',worldChanged:'The Financial World changed after authorization. No change was applied by this request; start a new review.',previewStale:'The exact preview is stale. No change was applied; create and authorize a new preview.',executionRejected:'The authorized execution was rejected. No receipt was recorded; review the proposal before retrying.',executionFailed:'Execution failed before commit. No receipt was recorded; the same authorized operation can be retried if it is still current.'},
  'zh-CN':{coverageRequired:'本次审阅覆盖哪些已报告账户和余额？（必填）',homePending:'应用已授权更改前，首页不会变化；演示与我的财务世界始终分开。',apply:'应用已授权更改',applying:'正在应用已授权更改…',applied:'更改已应用',recovered:'已找回权威收据：这些更改此前已应用，没有再次执行。',authorizedReady:'这项精确更改已获授权，但尚未应用。应用后将更新我的财务世界并生成权威收据。',appliedCopy:'更改已应用到我的财务世界。首页现读取权威财务世界。',appliedReceipt:'权威收据',appliedRevision:'新财务世界修订号',refreshFailed:'提交已成功，权威收据可查，但首页暂时无法刷新。请重试读取，不要再次应用。',notCommitted:'尚无收据记录。如果审阅仍有效，可以重试同一项已授权操作。',outcomeUnknown:'目前无法核实应用结果。再次应用前请先查询操作状态。',checkOutcome:'查询应用状态',sessionLost:'此存储中未找到匹配的已提交收据。待处理审阅在重启后失效；系统没有自动重试。',authorizationStale:'授权已过期；本次未应用更改。请针对当前财务世界重新审阅并授权。',worldChanged:'授权后财务世界已变化；本次请求未应用更改。请重新审阅。',previewStale:'精确预览已过期；本次未应用更改。请重新生成预览并授权。',executionRejected:'已授权操作被拒绝；没有生成收据。请检查提案后再重试。',executionFailed:'执行在提交前失败；没有生成收据。若审阅仍有效，可重试同一已授权操作。'}
};
const S1_COPY={
  en:{title:'FINVIS will make these changes',proposed:'Changes are ready for an exact preview',preparing:'The reviewed proposal is ready. Open its exact before-and-after preview before authorization.',notRecorded:'Not recorded',removed:'Removed from current view',added:'Add',changed:'Change',retired:'Remove',preserved:'Preserved',historyPreserved:'Earlier records and receipts remain in history.',otherPreserved:'Other current records remain unchanged.',replaceNotice:'Selected current records will leave the current view.',addOnlyNotice:'You chose to add only the listed facts; existing records stay current.',monthly:'Reported monthly essential outflow',currentAssets:'Current funds',earmarks:'Purpose allocations · within current funds',futureInflows:'Future inflows · not current cash',planned:'Planned expenses · not paid',decisions:'Considering',account:'Accounts',context:'Related context',actuals:'Actual occurrences',balanceEffects:'Balance adjustments',alreadyIncluded:'Already included in the reported balance',other:'Other changes',noAmountAdd:'New record',noAmountChange:'Record updated',noAmountRemove:'Record removed from current view',scope:'What changes',unknowns:'Still unknown',otherUnknown:'Other information remains unknown.',essentialRunway:'Essential monthly outflow is not recorded, so runway cannot be assessed.',essentialOnly:'Essential monthly outflow is not recorded.',runwayOnly:'Runway has not been assessed.',evaluationPending:'Financial evaluation is not available yet.',details:'View source and details',source:'Source excerpt',precision:'Precision',beforeRef:'Before record',afterRef:'After record',revision:'Current World revision',previewDigest:'Exact preview digest',unapplied:'These changes have not been applied.',count:'{added} added · {changed} changed · {removed} removed',unknownAmount:'Amount unknown',range:'{lower}–{upper}'},
  'zh-CN':{title:'FINVIS 将进行以下更改',proposed:'更改已可生成精确预览',preparing:'已审阅提案准备就绪。授权前请查看精确的变更预览。',notRecorded:'尚未记录',removed:'已从当前记录移除',added:'新增',changed:'更改',retired:'移除',preserved:'保留',historyPreserved:'先前记录与收据仍保留在历史中。',otherPreserved:'其他当前记录保持不变。',replaceNotice:'选定的当前记录将退出当前视图。',addOnlyNotice:'你选择只新增列出的事实；现有记录保持不变。',monthly:'用户报告的每月必要支出',currentAssets:'当前资金',earmarks:'用途分配 · 包含在当前资金内',futureInflows:'未来收入 · 不属于当前现金',planned:'计划支出 · 尚未支付',decisions:'正在考虑',account:'账户',context:'相关背景',actuals:'实际收支',balanceEffects:'余额调整',alreadyIncluded:'已包含在报告余额中',other:'其他更改',noAmountAdd:'新增记录',noAmountChange:'记录已更新',noAmountRemove:'记录已从当前视图移除',scope:'更改概览',unknowns:'仍未知的信息',otherUnknown:'其他信息仍未知。',essentialRunway:'尚未记录每月必要支出，因此暂时无法评估现金缓冲。',essentialOnly:'尚未记录每月必要支出。',runwayOnly:'现金缓冲尚未经过评估。',evaluationPending:'财务评估尚未完成。',details:'查看依据与详细信息',source:'原文片段',precision:'精度',beforeRef:'原记录',afterRef:'新记录',revision:'当前财务世界修订号',previewDigest:'精确预览摘要',unapplied:'这些更改尚未应用。',count:'新增 {added} · 更改 {changed} · 移除 {removed}',unknownAmount:'金额未知',range:'{lower}–{upper}'}
};
const S=(key,vars={})=>((S1_COPY[language]||S1_COPY.en)[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(vars[name]??''));
const STOP_COPY={'zh-CN':{interpretationStopped:'已停止理解，草稿可以继续编辑。',interpretationStopUnknown:'已在此页面停止等待，但无法确认服务器是否收到取消请求；草稿已保留。',interpretationTooLate:'此请求已进入审阅阶段，请检查当前审阅状态。'}};
const L=(key,vars={})=>((STOP_COPY[language]||{})[key]||(I4_COPY[language]||I4_COPY.en)[key]||(COPY[language]||COPY.en)[key])?.replace(/\{(\w+)\}/g,(_,k)=>vars[k]??'')||key;
const PENDING_AUTHORIZATION='finvis-pending-user-authorization';
const pendingAuthorization=()=>{try{return sessionStorage.getItem(PENDING_AUTHORIZATION);}catch{return null;}};
const rememberAuthorization=id=>{try{sessionStorage.setItem(PENDING_AUTHORIZATION,id);}catch{}};
const clearAuthorization=()=>{try{sessionStorage.removeItem(PENDING_AUTHORIZATION);}catch{}};
const el=(tag,text,cls)=>{const value=node(tag,text,cls);return value;};
const ref=value=>value?{kind:value.record_kind,id:value.ref.id,version:value.ref.version}:null;
const choice=(items,placeholder=L('none'))=>{const select=el('select');select.append(new Option(placeholder,''));items.forEach(([label,value])=>select.append(new Option(label,String(value))));return select;};
const field=(label,control)=>{const wrap=el('label',undefined,'i3-field');wrap.append(el('span',label),control);return wrap;};
const detail=(title,body)=>{const item=el('details',undefined,'i3-detail');item.append(el('summary',title),body);return item;};
const money=amount=>{if(!amount)return L('unknown');const minor=amount.minor_units??amount.amount?.minor_units;if(minor===null||minor===undefined)return L('unknown');const currency=amount.currency||amount.amount?.currency||amount.unknown_currency||'';const value=new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG',{minimumFractionDigits:2,maximumFractionDigits:2}).format(minor/100);return `${amount.precision==='approximate'?language==='zh-CN'?'约 ':'about ':''}${currency} ${value}`;};
const kindName=kind=>({monthly_essential_outflow:'monthly',account:'account',asset_position:'currentAssets',earmark:'earmarks',future_inflow:'futureInflows',planned_expense:'planned',world_decision:'decisions',decision_context:'context',actual_occurrence:'actuals',balance_effect:'balanceEffects'})[kind]||'other';
const issueText=code=>L(code==='replacement_scope_required'?'issueScope':code==='account_composition_required'?'issueAccount':code==='coverage_required'?'issueCoverage':code==='uncertainty_review_required'?'issueUncertainty':code==='source_unknown'?'issueSource':code==='balance_coverage_unknown'?'issueBalanceCoverage':'issueGeneric');
const previewMoney=value=>{
  if(!value)return S('notRecorded');
  const currency=value.amount?.currency||value.lower_bound?.currency||value.upper_bound?.currency||value.unknown_currency||'';
  const digits=minor=>{const number=BigInt(minor),absolute=number<0n?-number:number;return `${number<0n?'-':''}${new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-SG').format(absolute/100n)}.${String(absolute%100n).padStart(2,'0')}`;};
  const symbol=currency?currency+' ':'';
  if(value.precision==='unknown')return `${S('unknownAmount')}${currency?` (${currency})`:''}`;
  if(value.precision==='range'){
    if(value.lower_bound?.minor_units==null||value.upper_bound?.minor_units==null)return `${S('unknownAmount')}${currency?` (${currency})`:''}`;
    return `${symbol}${S('range',{lower:digits(value.lower_bound.minor_units),upper:digits(value.upper_bound.minor_units)})}`;
  }
  if(value.amount?.minor_units===undefined||value.amount?.minor_units===null)return `${S('unknownAmount')}${currency?` (${currency})`:''}`;
  return `${value.precision==='approximate'?'≈':''}${symbol}${digits(value.amount.minor_units)}`;
};
const previewAction=change=>change.retired||change.cancelled?'retired':change.before_ref?'changed':'added';
const previewLabel=change=>{if(change.kind==='monthly_essential_outflow')return S('monthly');const label=change.subject||'';return /^(agent-review|account|position|earmark|future-inflow|planned-expense|decision|balance-effect)\//.test(label)?S(kindName(change.kind)):label||S(kindName(change.kind));};
const systemLabel=kind=>(language==='zh-CN'?{world_state:'财务世界状态版本',world_plan:'计划版本'}:{world_state:'Financial World state version',world_plan:'Plan version'})[kind]||(language==='zh-CN'?'系统版本':'System version');

export function renderExactPreview(state){
  const textBlock=(text,cls)=>el('p',text,cls);
    const preview=state.preview,p=state.proposal,wrap=el('div',undefined,'i3-s1-preview');
    const changes=preview.changes||[],counts={added:0,changed:0,removed:0};
    for(const change of changes)counts[previewAction(change)==='retired'?'removed':previewAction(change)]++;
    const overview=el('section',undefined,'i3-s1-summary');
    overview.append(textBlock(S('unapplied'),'i3-muted'),textBlock(S('count',counts),'i3-s1-count'));
    if(state.interaction?.evidence?.unsupported?.length){const items=state.interaction.evidence.unsupported.map(item=>item.label==='evaluate_decision'||item.label==='financial_verdict'?interactionText('financial_verdict_label'):item.label);
      overview.append(textBlock(`${interactionText('unsupportedTitle')}: ${items.join(' · ')}. ${interactionText('unsupportedPreviewNote')}`,'i3-advice-note'));}
    else if(state.interaction?.evidence?.advice_notice)overview.append(textBlock(interactionText('adviceNotice'),'i3-advice-note'));
    if(p?.review_mode==='replace')overview.append(textBlock(S('replaceNotice'),'i3-warning-text'));
    else if(p?.review_mode==='add_only'&&state.candidate?.interpretation.destructive_actions.length)overview.append(textBlock(S('addOnlyNotice'),'i3-muted'));
    wrap.append(overview);
    const order=['monthly_essential_outflow','asset_position','earmark','future_inflow','planned_expense','world_decision','actual_occurrence','balance_effect','account','decision_context'];
    const structural=el('div');
    for(const kind of [...order,...new Set(changes.map(change=>change.kind).filter(kind=>!order.includes(kind)))]){
      const rows=changes.filter(change=>change.kind===kind);if(!rows.length)continue;
      const group=el('section',undefined,'i3-change-group');group.dataset.changeKind=kind;group.append(el('h3',S(kindName(kind))));
      const list=el('div',undefined,'i3-change-list');
      for(const change of rows){
        const action=previewAction(change),row=el('div',undefined,`i3-change-row i3-change-${action}`);
        row.dataset.action=action;
        const label=el('div',undefined,'i3-change-label');const cue={asset_position:'💳',earmark:'🛡',future_inflow:'↗',planned_expense:'○',world_decision:'◇',monthly_essential_outflow:'◷'}[kind]||'';label.append(el('strong',`${cue} ${previewLabel(change)}`),el('span',S(action)));
        const amount=el('div',undefined,'i3-change-values');
        if(change.kind==='balance_effect')amount.append(el('strong',change.effect_direction===0?S('alreadyIncluded'):`${change.effect_direction<0?'−':'+'}${previewMoney(change.after)}`));
        else if(change.before||change.after){amount.append(el('span',change.before?previewMoney(change.before):S('notRecorded')),el('span','→','i3-change-arrow'),el('strong',action==='retired'?S('removed'):previewMoney(change.after)));}
        else amount.append(el('strong',S(action==='retired'?'noAmountRemove':action==='changed'?'noAmountChange':'noAmountAdd')));
        row.append(label,amount);
        for(const text of change.text_changes||[])row.append(textBlock(`${text.before} → ${text.after}`,'i3-muted'));
        if(change.before_status&&change.before_status!==change.after_status){
          const statuses=language==='zh-CN'?{cancelled:'已取消',planned:'计划中',expected:'预期',realized:'已实现',completed:'已完成',exploring:'考虑中',selected:'已选择',deferred:'已延后'}:{cancelled:'Cancelled',planned:'Planned',expected:'Expected',realized:'Realized',completed:'Completed',exploring:'Exploring',selected:'Selected',deferred:'Deferred'};
          row.append(textBlock(`${statuses[change.before_status]||change.before_status} → ${statuses[change.after_status]||change.after_status}`,'i3-muted'));
        }
        if(change.cancelled)row.append(textBlock(language==='zh-CN'?'取消未履行的计划；不发生资金收支。':'Cancel the unfulfilled plan; no money moves.','i3-muted'));
        list.append(row);
      }
      group.append(list);if(kind==='account'&&rows.every(c=>previewAction(c)==='added'))structural.append(group);else wrap.append(group);
    }
    const recordsMonthly=changes.some(change=>change.kind==='monthly_essential_outflow'&&change.after&&!change.retired);
    const unknowns=(state.unknowns||[]).filter(item=>!(recordsMonthly&&item==='essential_monthly_outflow')),messages=[];
    if(unknowns.includes('essential_monthly_outflow')&&unknowns.includes('runway'))messages.push(S('essentialRunway'));
    else{if(unknowns.includes('essential_monthly_outflow'))messages.push(S('essentialOnly'));if(unknowns.includes('runway'))messages.push(S('runwayOnly'));}
    if(unknowns.some(item=>!['essential_monthly_outflow','runway'].includes(item)))messages.push(S('otherUnknown'));
    if(preview.pending?.some(item=>item.code==='evaluation_pending'))messages.push(S('evaluationPending'));
    for(const item of preview.pending||[])if(item.code!=='evaluation_pending'&&item.disposition!=='ready')messages.push(issueText(item.code));
    for(const item of p?.issues||[])messages.push(issueText(item.code));
    for(const item of state.candidate?.interpretation.uncertainties||[])messages.push(item);
    if(messages.length){const section=el('section',undefined,'i3-s1-unknowns');section.append(el('h3',S('unknowns')));for(const message of [...new Set(messages)])section.append(textBlock(message));wrap.append(detail(S('unknowns'),section));}
    const audit=el('div',undefined,'i3-s1-audit');
    if(structural.childElementCount)audit.append(structural);
    if(preview.preserved_categories?.includes('audit_history'))audit.append(textBlock(S('historyPreserved'),'i3-muted'));
    if(preview.preserved_categories?.includes('unmentioned_world_members'))audit.append(textBlock(S('otherPreserved'),'i3-muted'));
    audit.append(textBlock(`${S('revision')}: ${state.world_revision}`),textBlock(`${S('previewDigest')}: ${preview.digest}`));
    for(const change of changes){const entry=el('section');entry.append(el('h4',previewLabel(change)));for(const [label,value] of [[S('precision'),change.after?.precision||change.before?.precision],[S('beforeRef'),change.before_ref?.ref?.id],[S('afterRef'),change.after_ref?.ref?.id]])if(value)entry.append(textBlock(`${label}: ${value}`));audit.append(entry);}
    for(const change of preview.system_changes||[]){const entry=el('section');entry.append(el('h4',systemLabel(change.kind)));for(const [label,value] of [[S('beforeRef'),change.before_ref?.ref?.id],[S('afterRef'),change.after_ref?.ref?.id]])if(value)entry.append(textBlock(`${label}: ${value}`));audit.append(entry);}
    for(const excerpt of state.excerpts||[])audit.append(textBlock(`${S('source')}: ${excerpt.text}`));
    if(state.interaction){
      const proof=el('pre',JSON.stringify({evidence:state.interaction.evidence,binding:state.interaction.binding},null,2),'i3-proof');
      audit.append(detail(language==='zh-CN'?'确定性审阅记录':'Deterministic review record',proof));
      audit.append(detail(language==='zh-CN'?'提取含义与时间解释':'Extracted meaning and timing',el('pre',JSON.stringify(state.candidate?.interpretation,null,2),'i3-proof')));
    }
    wrap.append(detail(S('details'),audit));
    return wrap;
  }

export function createI3UI({showFeedback,composer,setAgentProcessing,onApplied,onRealityPrepared,onHypothetical}){
  let submissionDone=()=>{};
  let state=null,busy=false,applying=false,recoveredOutcome=false,requestVersion=0,outcomeUnknown=false;
  let activeInterpretation=null,reviewPhase=null,phaseCandidate=null,manual=false,decisionSeen=false,compoundReview=false;
  const changePhase=value=>{reviewPhase=value;render();body.scrollTop=0;title.focus({preventScroll:true});};
  const reviewButton=el('button',undefined,'i3-review-trigger');reviewButton.id='agent-review';reviewButton.type='button';reviewButton.hidden=true;
  $('agent-confirm').before(reviewButton);
  const openButton=el('button',undefined,'i3-open-trigger');openButton.id='i3-open';openButton.type='button';openButton.hidden=true;
  $('finvis-command').append(openButton);
  const dialog=el('dialog',undefined,'i3-dialog');dialog.id='i3-dialog';dialog.setAttribute('aria-labelledby','i3-title');
  const head=el('header');const eyebrow=el('p',undefined,'kicker');const title=el('h2');title.id='i3-title';title.tabIndex=-1;const close=el('button','×','i3-icon');close.type='button';close.setAttribute('aria-label',L('close'));head.append(eyebrow,title,close);
  const body=el('div',undefined,'i3-body');body.id='i3-body';const foot=el('footer');
  const cancelButton=el('button',undefined,'secondary-button');cancelButton.type='button';const next=el('button');next.type='button';foot.append(cancelButton,next);dialog.append(head,body,foot);document.body.append(dialog);
  const show=()=>{if(!dialog.open){dialog.showModal();dialog.scrollTop=0;title.focus({preventScroll:true});}};
  const compound=createCompoundMeaning({
    showFeedback,
    onReview:async review=>{
      compoundReview=true;submissionDone=()=>{};state=review;phaseCandidate=null;reviewPhase=null;
      $('agent-preview').dataset.world='user';render();
      if(state?.stage==='understanding')await start();else show();
    },
    onRoute:async route=>{
      if(route.kind==='lifecycle'){
        await showLifecycle(route,{onApplied:async()=>onApplied?.(),interpret:async()=>compound.resume(),showFeedback});
        document.getElementById('lr-dialog')?.addEventListener('close',()=>compound.resume(),{once:true});
      }else{showRealityRoute(route,{onRealityPrepared:()=>onRealityPrepared(()=>compound.resume()),onHypothetical,showFeedback});}
    },
    onFuture:trial=>window.dispatchEvent(new CustomEvent('finvis-compound-future',{detail:trial}))
  });
  const userCandidateCopy=()=>{
    if($('agent-preview').dataset.world!=='user')return;
    const destructive=state?.candidate?.interpretation.destructive_actions.length>0;
    $('agent-preview-action').textContent=L(destructive?'understandingReplace':'understandingUpdate');
    $('agent-stage-next').textContent=L('understandingNext');
    $('agent-preview-boundary').textContent=L('understandingBoundary');
    document.querySelector('#agent-preview .agent-next').textContent=L('understandingNext');
  };
  const textBlock=(text,cls)=>el('p',text,cls);
  async function request(path,payload){
    if(busy||activeInterpretation)return null;busy=true;requestVersion++;next.disabled=true;
    try{
      const seed=await fetch('/api/state',{cache:'no-store'});if(!seed.ok)throw new Error('network');const token=(await seed.json()).token;
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify(payload)});
      const data=await response.json();if(data.user){state=data.user;render();}
      if(!response.ok){showFeedback(providerMessage(data.code)||(['invalid_interpretation','truncated_response'].includes(data.code)?L('interpretationUnverified'):response.status===409?L('staleMessage'):response.status===400?L('invalid'):String(data.code||'').startsWith('user_')&&response.status===503?L('outcomeUnknown'):response.status===503?L('provider'):L('network')),true);return null;}
      return state;
    }catch{showFeedback(L('network'),true);return null;}
    finally{busy=false;render();}
  }
  async function status(){const startedAt=requestVersion;try{const result=await fetch('/api/user/status',{cache:'no-store'});if(result.ok){const data=await result.json();if(startedAt===requestVersion){state=data.user;render();const pending=pendingAuthorization();if(pending)await lookupOperation(pending);}}}catch{/* The legacy Demo remains usable. */}}
  async function completeApplied(operation,user,recovered=false){
    if(user)state=user;else{try{const response=await fetch('/api/user/status',{cache:'no-store'});if(response.ok)state=(await response.json()).user;}catch{}}
    if(!state||state.stage!=='applied')state={...state,stage:'applied',operation};
    else state.operation=operation;
    submissionDone();
    outcomeUnknown=false;recoveredOutcome=recovered||operation.status==='receipt_recovered';clearAuthorization();render();
    let refreshed;
    try{refreshed=await onApplied?.(operation);}catch{refreshed=false;}
    if(dialog.open)dialog.close();
    showFeedback(refreshed===false?L('refreshFailed'):L(recoveredOutcome?'recovered':'appliedCopy'),refreshed===false);
    return operation;
  }
  async function lookupOperation(id){
    try{
      const response=await fetch(`/api/user/operation?authorization_id=${encodeURIComponent(id)}`,{cache:'no-store'});
      if(response.status===404){outcomeUnknown=true;showFeedback(L('sessionLost'),true);render();return null;}
      if(!response.ok)throw new Error('lookup');
      const data=await response.json(),operation=data.operation;
      if(['applied','receipt_recovered'].includes(operation?.status))return completeApplied(operation,null,true);
      outcomeUnknown=false;render();showFeedback(L('notCommitted'),true);return null;
    }catch{outcomeUnknown=true;render();showFeedback(L('outcomeUnknown'),true);return null;}
  }
  async function apply(){
    if(busy||!state?.authorization)return null;
    const authorizationId=state.authorization.id;
    if(outcomeUnknown)return lookupOperation(authorizationId);
    rememberAuthorization(authorizationId);busy=true;applying=true;requestVersion++;render();
    try{
      const seed=await fetch('/api/state',{cache:'no-store'});if(!seed.ok)throw new Error('seed');
      const token=(await seed.json()).token;
      const response=await fetch('/api/user/apply',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify({proposal_id:state.proposal.id,preview_id:state.preview.id,digest:state.preview.digest,authorization_id:authorizationId})});
      const data=await response.json();
      if(response.ok&&['applied','receipt_recovered'].includes(data.operation?.status))return completeApplied(data.operation,data.user);
      const recovered=await lookupOperation(authorizationId);if(recovered)return recovered;
      const failure={authorization_stale:'authorizationStale',candidate_stale:'authorizationStale',proposal_stale:'authorizationStale',world_changed:'worldChanged',preview_stale:'previewStale',execution_rejected:'executionRejected',execution_failed_before_commit:'executionFailed'}[data.code];
      if(failure)showFeedback(L(failure),true);
      return null;
    }catch{return lookupOperation(authorizationId);}
    finally{busy=false;applying=false;render();}
  }
  const excerpts=()=>state?.excerpts||[];
  const evidenceSelect=()=>choice(excerpts().map((part,i)=>[part.text,i]),L('selectEvidence'));
  const evidenceValue=select=>{const item=excerpts()[Number(select.value)];return item?{start:item.start,end:item.end}:null;};
  const currentRefs=kind=>(state?.current_records||[]).filter(item=>item.kind===kind);
  const subject=item=>{
    if(item.record_kind==='review_issue')return issueText(item.subject);
    if(item.record_kind==='actual_occurrence')return item.subject==='expense'?(language==='zh-CN'?'支出':'Expense'):(language==='zh-CN'?'流入':'Inflow');
    const target=item.target_ref;
    if(target){const current=(state?.current_records||[]).find(row=>row.ref?.record_kind===target.record_kind&&row.ref.ref?.id===target.ref?.id&&row.ref.ref?.version===target.ref?.version);if(current)return current.label;}
    const fact=state?.candidate?.interpretation.candidate_facts.find(row=>row.id===item.subject);return fact?.label||item.subject;
  };
  const refChoice=(kind)=>choice(currentRefs(kind).map(item=>[kind==='monthly_essential_outflow'?`${S('monthly')} · ${item.currency}`:item.label,JSON.stringify(ref(item.ref))]));
  const chosenRef=select=>select?.value?JSON.parse(select.value):null;
  const line=(title,description)=>{const row=el('div',undefined,'i3-line');row.append(el('strong',title),el('small',description));return row;};
  function reviewForm(){
    const candidate=state.candidate, facts=candidate.interpretation.candidate_facts;
    const form=el('div',undefined,'i3-review-form');form.append(textBlock(L('reviewIntro'),'i3-lead'));
    const factRows=[];
    for(const fact of facts){
      const card=el('section',undefined,'i3-review-card');card.append(el('h3',fact.label),textBlock([fact.account,fact.amount?.value&&`${fact.amount.currency||''} ${fact.amount.value}`,fact.timing_note].filter(Boolean).join(' · '),'i3-muted'));
      const source=evidenceSelect();card.append(field(L('factEvidence'),source));
      const role=fact.type==='earmarked_current_asset'?choice([[L('roleOther'),'other'],[L('roleEmergency'),'emergency'],[L('rolePurchase'),'purchase']],L('none')):null;
      const emergency=fact.type==='earmarked_current_asset'?el('input'):null;if(emergency){emergency.type='checkbox';card.append(field(L('role'),role),field(L('emergency'),emergency));}
      const funding=fact.type==='planned_expense'?choice(facts.filter(item=>item.type==='earmarked_current_asset').map(item=>[item.label,item.id])):null;if(funding)card.append(field(L('funding'),funding));
      const decision=fact.type==='context'?choice(facts.filter(item=>item.type==='contemplated_decision').map(item=>[item.label,item.id])):null;if(decision)card.append(field(L('decision'),decision));
      const correction=!['current_asset','actual_expense','actual_inflow'].includes(fact.type)?refChoice(({monthly_essential_outflow:'monthly_essential_outflow',earmarked_current_asset:'earmark',expected_future_inflow:'future_inflow',planned_expense:'planned_expense',contemplated_decision:'world_decision',context:'decision_context'})[fact.type]):null;
      const reason=correction?el('input'):null;if(correction){card.append(field(L('target'),correction),field(L('reason'),reason));}
      form.append(card);factRows.push({fact,source,role,emergency,funding,decision,correction,reason});
    }
    const accountRows=[];
    const accountNames=[...new Set(facts.filter(f=>['current_asset','earmarked_current_asset'].includes(f.type)).map(f=>f.account).filter(Boolean))];
    for(const [index,name] of accountNames.entries()){
      const card=el('section',undefined,'i3-review-card');card.append(el('h3',`${L('account')}: ${name}`));
      const source=evidenceSelect(),existing=refChoice('account');card.append(field(L('accountEvidence'),source),field(L('target'),existing));
      const related=facts.filter(f=>f.account===name&&['current_asset','earmarked_current_asset'].includes(f.type));
      const composition=choice([[L('total'),'total'],[L('disjoint'),'disjoint']],L('selectComposition'));
      card.append(field(L('composition'),composition));
      const balance=[],allocation=[];
      for(const fact of related){const check=el('input');check.type='checkbox';card.append(field(`${L('balance')}: ${fact.label}`,check));balance.push([fact.id,check]);if(fact.type==='earmarked_current_asset'){const mark=el('input');mark.type='checkbox';card.append(field(`${L('allocation')}: ${fact.label}`,mark));allocation.push([fact.id,mark]);}}
      const position=refChoice('asset_position'),reason=el('input');card.append(field(L('target'),position),field(L('reason'),reason));
      form.append(card);accountRows.push({key:`account-${index}`,positionKey:`position-${index}`,name,source,existing,composition,balance,allocation,position,reason});
    }
    let mode=null,accountScope=[],currencyScope=[];
    const destructive=candidate.interpretation.destructive_actions.length>0;
    if(destructive){
      const card=el('section',undefined,'i3-review-card i3-warning');card.append(el('h3',L('replaceTitle')),textBlock(L('selectScope')));
      mode=choice([[L('replace'),'replace'],[L('merge'),'independent_merge']],L('none'));card.append(field(L('destructive'),mode));
      for(const item of currentRefs('account')){const check=el('input');check.type='checkbox';card.append(field(item.label,check));accountScope.push([ref(item.ref),check]);}
      const currencies=[...new Set([...(state.current_currencies||[]),...facts.map(f=>f.amount?.currency).filter(Boolean)])];
      for(const currency of currencies){const check=el('input');check.type='checkbox';card.append(field(`${L('currency')}: ${currency}`,check));currencyScope.push([currency,check]);}
      if(!accountScope.length)card.append(textBlock(L('noScope'),'i3-muted'));
      form.append(card);
    }
    const needsCoverage=facts.some(f=>['current_asset','earmarked_current_asset'].includes(f.type));
    const coverage=el('textarea');coverage.rows=2;form.append(field(L(needsCoverage?'coverageRequired':'coverage'),coverage));
    const uncertainties=(candidate.interpretation.uncertainties||[]).map(value=>{const check=el('input');check.type='checkbox';form.append(field(`${L('uncertainty')}: ${value}`,check));return [value,check];});
    form.collect=()=>{
      const incomplete=factRows.some(row=>!evidenceValue(row.source)||row.role&&!row.role.value)||accountRows.some(row=>{const count=row.balance.filter(([,check])=>check.checked).length;return !evidenceValue(row.source)||!row.composition.value||!count||row.composition.value==='total'&&count!==1;})||destructive&&(!mode.value||mode.value==='replace'&&(!accountScope.some(([,check])=>check.checked)||!currencyScope.some(([,check])=>check.checked)))||uncertainties.some(([,check])=>!check.checked)||needsCoverage&&!coverage.value.trim();
      if(incomplete)throw new Error('incomplete');
      const positionCorrection=new Map(accountRows.map(row=>[row.name,chosenRef(row.position)]));
      return {facts:factRows.map(row=>({id:row.fact.id,source:evidenceValue(row.source),earmark_role:row.role?.value||'other',emergency_use:!!row.emergency?.checked,decision_ids:row.decision?.value?[row.decision.value]:[],funding_fact_id:row.funding?.value||null,expected_ref:chosenRef(row.correction),correction_reason:row.reason?.value||null})),
        accounts:accountRows.map(row=>({key:row.key,label:row.name,source:evidenceValue(row.source),existing_ref:chosenRef(row.existing)})),
        positions:accountRows.map(row=>({key:row.positionKey,account_key:row.key,balance_ids:row.balance.filter(([,check])=>check.checked).map(([id])=>id),allocation_ids:row.allocation.filter(([,check])=>check.checked).map(([id])=>id),composition:row.composition.value,expected_ref:positionCorrection.get(row.name),correction_reason:row.reason.value||null})),
        coverage_note:coverage.value,uncertainties:uncertainties.map(([value])=>value),mode:destructive?mode.value:'merge',replacement:mode?.value==='replace'?{account_refs:accountScope.filter(([,check])=>check.checked).map(([value])=>value),currencies:currencyScope.filter(([,check])=>check.checked).map(([value])=>value)}:null,occurrence:null};
    };
    return form;
  }
  function actualForm(){
    const fact=state.candidate.interpretation.candidate_facts[0],form=el('div',undefined,'i3-review-form');form.append(textBlock(L('actual')));
    const source=evidenceSelect(),accountSource=evidenceSelect(),position=refChoice('asset_position'),basis=choice([[L('basisExclusive'),'apply_to_prior_balance'],[L('basisInclusive'),'already_in_reported_balance']]),coverageSource=evidenceSelect(),coverageReason=el('input'),freshness=el('input');freshness.type='checkbox';
    form.append(field(L('factEvidence'),source),field(L('accountEvidence'),accountSource),field(L('actualPosition'),position),field(L('basis'),basis),field(L('coverageSource'),coverageSource),field(L('coverageReason'),coverageReason),field(language==='zh-CN'?'我确认这笔收支和所选余额依据在本次审阅时仍然有效':'I confirm this occurrence and the selected balance evidence are current for this review',freshness));
    form.collect=()=>{if(!evidenceValue(source)||position.value&&!evidenceValue(accountSource)||position.value&&basis.value&&(!evidenceValue(coverageSource)||!coverageReason.value.trim()))throw new Error('incomplete');return {facts:[],accounts:[],positions:[],coverage_note:'',uncertainties:[],mode:'merge',replacement:null,occurrence:{fact_id:fact.id,source:evidenceValue(source),account_source:evidenceValue(accountSource),position_ref:chosenRef(position),basis:basis.value||null,coverage_source:evidenceValue(coverageSource),coverage_rationale:coverageReason.value,freshness_current:freshness.checked}};};return form;
  }
  function proposalBody(){
    const p=state.proposal,items=p.projection.items||[],wrap=el('section',undefined,'i3-s1-summary');
    const count={added:items.filter(item=>item.action==='add').length,changed:items.filter(item=>item.action==='change_replace').length,removed:items.filter(item=>item.action==='remove_retire').length};
    wrap.append(textBlock(S('preparing'),'i3-lead'),textBlock(S('count',count),'i3-muted'));
    if(p.review_mode==='replace')wrap.append(textBlock(S('replaceNotice'),'i3-warning-text'));
    if(p.review_mode==='add_only'&&state.candidate?.interpretation.destructive_actions.length)wrap.append(textBlock(S('addOnlyNotice'),'i3-muted'));
    if(p.issues?.length)wrap.append(textBlock(p.issues.map(item=>issueText(item.code)).join(' '),'i3-warning-text'));
    return wrap;
  }
  function previewBody(){return renderExactPreview(state);}

  function render(){
    compound.render();
    const previousKey=body.dataset.reviewKey;
    const previousFields=[...body.querySelectorAll('input,select,textarea')].map(control=>({value:control.value,checked:control.checked}));
    reviewButton.textContent=L('next');openButton.textContent=L('open');close.setAttribute('aria-label',L('close'));cancelButton.textContent=L('cancel');
    const stage=state?.stage||'idle';
    if(state?.candidate?.id!==phaseCandidate){phaseCandidate=state?.candidate?.id;reviewPhase=hasFacts(state||{})?'facts':'decisions';manual=false;decisionSeen=false;}
    openButton.hidden=stage==='idle';reviewButton.hidden=!(stage==='understanding'&&$('agent-preview').dataset.world==='user');$('agent-confirm').hidden=$('agent-preview').dataset.world==='user';
    const titles={understanding:'understanding',review:'review',proposal:'proposal',preview:'preview',authorized:'authorized',applied:'applied',stale:'stale',idle:'idle'};
    eyebrow.textContent=['proposal','preview','authorized'].includes(stage)?S('scope'):`${L('stage')} · ${L(titles[stage]||'idle')}`;title.textContent=stage==='preview'||stage==='authorized'?S('title'):stage==='proposal'?S('proposed'):L(titles[stage]||'idle');body.replaceChildren();next.hidden=false;
    if(stage==='understanding'){body.append(textBlock(L('candidate',{n:state.candidate?.interpretation.candidate_facts.length||0}),'i3-lead'),textBlock(L('homePending')));next.textContent=L('next');}
    else if(stage==='review'){
      const facts=state.candidate?.interpretation.candidate_facts||[];const actual=facts.length===1&&['actual_expense','actual_inflow'].includes(facts[0].type);
      const clarificationState=state.interaction&&!decisionSeen&&hasFacts(state)?{...state,interaction:{...state.interaction,questions:state.interaction.questions.filter(q=>q.kind!=='decision')}}:state;
      const form=state.interaction?clarificationForm(clarificationState,{el,field,choice,detail,edit:editOriginal}):actual?actualForm():reviewForm();body.append(form);body._reviewForm=form;
      body.dataset.reviewKey=`${state.candidate.id}:${state.session_id}:${state.interaction?.questions?.map(q=>q.id).join('|')||'legacy'}`;
      if(previousKey===body.dataset.reviewKey){[...body.querySelectorAll('input,select,textarea')].forEach((control,index)=>{if(previousFields[index]){control.value=previousFields[index].value;control.checked=previousFields[index].checked;if(control.tagName==='SELECT')control.dispatchEvent(new Event('change'));}});}
      if(state.proposal?.issues?.length)body.prepend(textBlock(state.proposal.issues.map(i=>issueText(i.code)).join(' '),'i3-warning-text'));
      next.textContent=L('submit');
      if(state.interaction){eyebrow.textContent=language==='zh-CN'?'准备更改':'Preparing changes';title.textContent=state.interaction.blockers.includes('revise_unsupported_interpretation')?interactionText('unsupportedTitle'):interactionText('title');next.textContent=interactionText('continue');next.hidden=!!state.interaction.blockers.length||!state.interaction.questions.length;}
    }else if(stage==='proposal'){body.append(proposalBody());next.textContent=L('makePreview');}
    else if(stage==='preview'){body.append(previewBody());next.textContent=L('authorize');next.classList.add('i3-authorize');}
    else if(stage==='authorized'){body.append(previewBody(),textBlock(L(applying?'applying':'authorizedReady'),'i3-stop'));next.textContent=applying?L('applying'):outcomeUnknown?L('checkOutcome'):L('apply');next.classList.add('i4-apply');}
    else if(stage==='applied'){const operation=state.operation||{};body.append(textBlock(L(recoveredOutcome?'recovered':'appliedCopy'),'i3-stop'),textBlock(`${L('appliedRevision')}: ${operation.receipt_revision??L('unknown')}`),textBlock(`${L('appliedReceipt')}: ${operation.receipt?.ref?.id||L('unknown')}`));next.textContent=L('finish');}
    else if(stage==='stale'){body.append(textBlock(L('staleMessage'),'i3-warning-text'));next.textContent=L('reviewAgain');}
    else{body.append(textBlock(L('idle')));next.hidden=true;}
    if(stage!=='preview')next.classList.remove('i3-authorize');if(stage!=='authorized')next.classList.remove('i4-apply');next.disabled=busy;cancelButton.hidden=stage==='applied';
    if(['review','preview'].includes(stage)&&!state.interaction?.blockers?.length){
      if(['facts','decisions'].includes(reviewPhase)&&!manual){
        title.textContent=U(reviewPhase);eyebrow.textContent=U('understood');body.replaceChildren(meaningStage(state,reviewPhase,renderExactPreview));next.textContent=U('continue');next.hidden=false;next.classList.remove('i3-authorize');
      }
      if(manual){title.textContent=U('evidence');body.replaceChildren();const form=reviewForm();body.append(form);body._reviewForm=form;next.textContent=L('submit');next.hidden=false;}
      const correction=el('div',undefined,'uf-correction-actions');
      const wrong=el('button',U(manual?'back':'wrong'),'secondary-button');wrong.type='button';wrong.addEventListener('click',()=>{manual=!manual;if(!manual)reviewPhase='facts';render();});correction.append(wrong);
      if(manual){const edit=el('button',U('edit'),'secondary-button');edit.type='button';edit.addEventListener('click',editOriginal);correction.append(edit);}
      body.append(correction);
    }
    userCandidateCopy();
  }
  async function interpret(text){
    if(busy||activeInterpretation)return null;
    const done=captureComposerCompletion();submissionDone=done;
    const attempt={id:crypto.randomUUID(),controller:new AbortController()};
    activeInterpretation=attempt;requestVersion++;setAgentProcessing(true);
    try{
      const seed=await fetch('/api/state',{cache:'no-store',signal:attempt.controller.signal});
      if(!seed.ok)throw new Error('network');
      const token=(await seed.json()).token;
      if(activeInterpretation!==attempt)return null;
      const response=await fetch('/api/user/interpret',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify({text,request_id:attempt.id}),signal:attempt.controller.signal});
      const data=await response.json();
      if(activeInterpretation!==attempt)return null;
      if(data.user){state=data.user;render();}
      if(!response.ok){showFeedback(providerMessage(data.code)||(['invalid_interpretation','truncated_response'].includes(data.code)?L('interpretationUnverified'):response.status===409?L('staleMessage'):response.status===400?L('invalid'):String(data.code||'').startsWith('user_')&&response.status===503?L('outcomeUnknown'):response.status===503?L('provider'):L('network')),true);return null;}
      const priorFeedback=$('feedback');if(priorFeedback){priorFeedback.hidden=true;priorFeedback.textContent='';}
      if(data.routing){activeInterpretation=null;setAgentProcessing(false);if(data.routing.kind==='compound'){compoundReview=false;compound.open(data.routing,done);}else if(data.routing.kind==='lifecycle')await showLifecycle(data.routing,{onApplied:async()=>{done();return onApplied?.();},interpret,showFeedback});else if(data.routing.kind==='planning')await showPlanning(data.routing,()=>{done();showFeedback(language==='zh-CN'?'估算已保存，可在决策中查看。':'Estimate saved. Open Decisions to explore it.');});else showRealityRoute(data.routing,{onRealityPrepared:()=>onRealityPrepared(done),onHypothetical,showFeedback});return data.routing;}
      if(data.user?.candidate){
        activeInterpretation=null;setAgentProcessing(false);
        $('agent-preview').dataset.world='user';
        await start();
      }
      return data.user;
    }catch(error){if(activeInterpretation===attempt&&error.name!=='AbortError')showFeedback(L('network'),true);return null;}
    finally{if(activeInterpretation===attempt){activeInterpretation=null;setAgentProcessing(false);}}
  }
  async function stopInterpretation(){
    const attempt=activeInterpretation;if(!attempt)return;
    activeInterpretation=null;const stopVersion=++requestVersion;attempt.controller.abort();setAgentProcessing(false);composer.save();
    showFeedback(L('interpretationStopped'));$('finvis-input').focus();
    try{
      const seed=await fetch('/api/state',{cache:'no-store'});if(!seed.ok)throw new Error('network');
      const token=(await seed.json()).token;
      const response=await fetch('/api/user/interpret/stop',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token,...cloudHeaders()},body:JSON.stringify({request_id:attempt.id})});
      if(!response.ok)throw new Error('stop');
      const result=await response.json();
      if(requestVersion!==stopVersion)return;
      if(result.status==='too_late')showFeedback(L('interpretationTooLate'),true);
      else await status();
    }catch{if(requestVersion===stopVersion)showFeedback(L('interpretationStopUnknown'),true);}
  }
  async function start(){const result=await request('/api/user/review/prepare',{candidate_id:state?.candidate?.id});if(result){$('agent-preview').close();show();}return result;}
  async function editOriginal(){const text=state?.submitted_text||'';const result=await request('/api/user/cancel',{});if(result){if(dialog.open)dialog.close();$('finvis-input').value=text;composer.save();$('finvis-input').focus();}}
  async function advance(){
    if(!state)return;const stage=state.stage;
    if(manual&&['review','preview'].includes(stage)){
      let review;try{review=body._reviewForm.collect();}catch{showFeedback(L('invalid'),true);return;}
      manual=false;reviewPhase='authorize';const result=await request('/api/user/review/submit',{session_id:state.session_id,review});
      if(result?.stage==='proposal')return request('/api/user/preview',{proposal_id:result.proposal.id});return result;
    }
    if(['review','preview'].includes(stage)&&['facts','decisions'].includes(reviewPhase)&&!state.interaction?.blockers?.length){
      if(reviewPhase==='facts')return changePhase(stage==='review'&&state.interaction?.questions.some(q=>q.kind!=='decision')?'questions':hasDecision(state)?'decisions':'authorize');
      decisionSeen=true;return changePhase(stage==='review'?'questions':'authorize');
    }
    if(stage==='understanding')return start();
    if(stage==='review'){
      let review;try{review=body._reviewForm.collect();}catch{showFeedback(L('invalid'),true);return;}
      reviewPhase=hasDecision(state)&&!decisionSeen?'decisions':'authorize';
      const result=await request(state.interaction?'/api/user/review/clarify':'/api/user/review/submit',state.interaction?{session_id:state.session_id,answers:review}:{session_id:state.session_id,review});
      if(result?.stage==='review'){reviewPhase=!decisionSeen&&hasDecision(result)&&!result.interaction?.questions.some(q=>q.kind!=='decision')?'decisions':'questions';render();}
      if(result?.stage==='proposal'&&result.proposal?.id)return request('/api/user/preview',{proposal_id:result.proposal.id});
      return result;
    }
    if(stage==='proposal')return request('/api/user/preview',{proposal_id:state.proposal.id});
    if(stage==='preview')return request('/api/user/authorize',{proposal_id:state.proposal.id,preview_id:state.preview.id,digest:state.preview.digest});
    if(stage==='stale'){dialog.close();$('finvis-input').focus();return;}
    if(stage==='authorized')return apply();
    if(stage==='applied')dialog.close();
  }
  async function cancel(){const result=await request('/api/user/cancel',{});if(result){clearAuthorization();if(dialog.open)dialog.close();if($('agent-preview').open)$('agent-preview').close();$('agent-preview').dataset.world='demo';composer.save();render();}return result;}
  reviewButton.addEventListener('click',start);openButton.addEventListener('click',()=>{if(state?.stage==='understanding'&&state.candidate){renderAgentCandidate(state.candidate,{open:true});$('agent-preview').dataset.world='user';render();}else show();});
  next.addEventListener('click',advance);cancelButton.addEventListener('click',cancel);close.addEventListener('click',()=>dialog.close());
  dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close();});
  dialog.addEventListener('close',()=>{if(compoundReview){compoundReview=false;compound.resume();}});
  status();render();return {interpret,stopInterpretation,cancel,render,status};
}
