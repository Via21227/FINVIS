/* Targeted application clarification. Evidence and financial mapping stay in Core. */
import {language,t} from './i18n.js';

const COPY={
  en:{title:'A little clarification',intro:'Only these details need your input. The other evidence is already prepared.',continue:'Continue to changes',edit:'Edit original text',source:'Which statement supports this proposed meaning?',account:'Which account does this refer to?',new_account:'A new account',composition:'Are these separate balances that should be added once?',disjoint:'Yes — these amounts are separate, with no overlap',decision:'Which decision does this context belong to?',role:'How is this money reserved?',other:'Other purpose',purchase:'Purchase fund',emergency:'Emergency reserve, explicitly available for emergency use',uncertainties:'Keep these uncertainties visible in the changes',correction:'Which current record are you correcting?',reason:'Reason for this correction',scope:'Choose the exact replacement scope',scopeIntro:'Only selected current records may be retired. History is preserved.',add:'Add only; keep all existing records',replace:'Replace selected accounts and currencies',settlement:'How does this actual occurrence affect a reported balance?',position:'Source or destination balance',accountSource:'Statement identifying the source or destination',basis:'Is the occurrence included in that balance?',exclusive:'Separate transaction to post',inclusive:'Already included in the reported balance',coverage:'Statement supporting the balance coverage',rationale:'Explain the balance coverage',freshness:'The occurrence and selected balance evidence are current for this review',prepared:'Other source evidence has been prepared automatically.',choose:'Choose…',revise_conflicting_interpretation:'The interpretation contains conflicting meanings or assumptions. Edit your statement so FINVIS can extract it again.',revise_unsupported_interpretation:'Part of this request is outside the supported update contract. Edit the statement to keep supported changes separate.',no_supported_update:'There is no supported financial update to preview yet.',separate_actual_occurrence:'Review one actual expense or inflow in a separate statement.',record_balance_before_settlement:'Record the source or destination balance first, then report this occurrence.',context_needs_decision:'This context needs a decision in the same statement.',revise_missing_account_or_amount:'Name the account and report its amount or explicitly unknown amount.',revise_account_evidence:'The selected source does not establish the account relationship. Clarify it in your statement.',revise_missing_correction_target:'The correction needs a current record and a reason. Clarify which record is changing.',blocked:'The existing reconciliation checks still need clearer information. Edit the statement before continuing.'},
  'zh-CN':{title:'补充一点信息',intro:'只需确认以下细节；其他依据已准备好。',continue:'继续查看更改',edit:'修改原文',source:'哪段陈述支持这项拟议含义？',account:'这里指的是哪个账户？',new_account:'一个新账户',composition:'这些金额是否互不重叠、应当合计一次？',disjoint:'是的，这些是互不重叠的金额',decision:'这项背景关联哪些决策？',role:'这笔资金预留作什么用途？',other:'其他用途',purchase:'购买资金',emergency:'应急储备，并明确可用于应急',uncertainties:'在更改中保留并显示以下不确定信息',correction:'要修正哪条当前记录？',reason:'本次修正的原因',scope:'选择精确的替换范围',scopeIntro:'只有选中的当前记录可能退役，历史仍会保留。',add:'仅新增；保留全部现有记录',replace:'替换选定账户及货币的记录',settlement:'这笔实际收支如何影响已报告的余额？',position:'来源或去向余额',accountSource:'说明来源或去向的原文',basis:'这笔收支是否已包含在该余额中？',exclusive:'需要另行入账',inclusive:'已包含在报告余额中',coverage:'说明余额覆盖关系的原文',rationale:'解释余额覆盖关系',freshness:'本次审阅时，这笔收支和所选余额依据仍然有效',prepared:'其他原文依据已自动准备。',choose:'请选择…',revise_conflicting_interpretation:'当前理解存在冲突或假设。请修改原文，让 FINVIS 重新提取。',revise_unsupported_interpretation:'请求中有当前契约尚不支持的内容。请将支持的更改单独陈述。',no_supported_update:'目前没有可预览的财务更新。',separate_actual_occurrence:'请将一笔实际支出或收入单独陈述和审阅。',record_balance_before_settlement:'请先记录来源或去向账户的余额，再报告这笔收支。',context_needs_decision:'请在同一陈述中提供这项背景关联的决策。',revise_missing_account_or_amount:'请说明账户及金额，或明确金额未知。',revise_account_evidence:'所选原文尚不能确定账户关系，请在陈述中说明。',revise_missing_correction_target:'修正需要当前记录及原因，请说明要修改哪项记录。',blocked:'现有核对规则仍需要更明确的信息，请修改陈述后继续。'}
};
COPY.en.revise_actual_account_or_scope='Name the actual source or destination account and report this occurrence separately from a replacement request.';
COPY['zh-CN'].revise_actual_account_or_scope='请说明实际收支的来源或去向账户，并与替换请求分开陈述。';

Object.assign(COPY.en,{
  intro:'Review the extracted details and answer only what is needed before an exact preview.',
  blockedIntro:'Review what FINVIS extracted and why this request cannot proceed.',
  revise_unsupported_interpretation:'This request cannot be separated safely from the proposed changes. Edit the original statement; no changes have been applied.',
  understood:'FINVIS extracted {count} financial items:',unsupportedTitle:'Cannot do in this review',
  unsupportedWhy:'Why:',unsupportedNext:'Next:',unsupported_operation:'This action is outside the current update contract; FINVIS cannot safely separate its effect from the proposed changes.',
  unsupported_fact:'This extracted item cannot be prepared under the current evidence contract.',
  conditional_verdict_scope:'The proposed update depends on FINVIS giving a verdict first. That condition cannot be met here, so the update is blocked.',
  unsupported_currency:'This currency is outside the supported CNY/SGD evidence path; no conversion is assumed.',
  financial_verdict_label:'Financial opinion request',
  financial_verdict_unavailable:'A financial verdict needs a supported deterministic evaluation; this review only records reported facts and cannot give purchase advice.',
  independent_scope:'Review supported items separately',supported_only:'Review only the extracted financial items. Leave the separate request out of this change.',
  independentNext:'Choose this only if the request left out does not change the meaning or scope of the financial items below. Check the exact preview before authorization.',
  blockedNext:'Revise the original statement or submit a separate supported request.',
  adviceNotice:'A requested financial opinion is not included in these changes. FINVIS has not evaluated affordability or given a verdict.',
  unsupportedPreviewNote:'Excluded from these changes; no financial verdict was calculated.',
  addOnlyUnsupported:'To continue without the separate request, choose add-only. Replacement will not happen in this review; edit the statement to pursue a scoped replacement.',
  preparedCount:'{count} source excerpts prepared from your words',
});
Object.assign(COPY['zh-CN'],{
  intro:'请核对提取的内容，仅补充生成精确预览所需的信息。',
  blockedIntro:'请核对 FINVIS 提取的内容，以及本次请求无法继续的原因。',
  revise_unsupported_interpretation:'这项请求无法安全地从拟议更改中分离。请修改原文；目前没有应用任何更改。',
  understood:'FINVIS 提取了 {count} 项财务内容：',unsupportedTitle:'本次审阅无法执行',
  unsupportedWhy:'原因：',unsupportedNext:'下一步：',unsupported_operation:'这项操作不在当前更新能力内，FINVIS 无法确认它与拟议更改相互独立。',
  unsupported_fact:'当前依据规则无法准备这项内容。',
  conditional_verdict_scope:'拟议更新以 FINVIS 先给出财务判断为条件；目前无法满足这个条件，因此更新被阻止。',
  unsupported_currency:'目前只支持 CNY/SGD 的依据路径；FINVIS 不会自行换算。',
  financial_verdict_label:'财务意见请求',
  financial_verdict_unavailable:'财务判断需要受支持的确定性评估；本次审阅只记录你报告的事实，不能给出购买建议。',
  independent_scope:'单独审阅受支持内容',supported_only:'仅审阅已提取的财务内容；这项独立请求不纳入更改。',
  independentNext:'仅当未纳入的请求不改变下列财务内容的含义或范围时，才选择继续。授权前请检查精确预览。',
  blockedNext:'请修改原文，或单独提交可支持的请求。',
  adviceNotice:'你提出的财务意见请求不在本次更改内。FINVIS 尚未评估可负担性，也没有给出判断。',
  unsupportedPreviewNote:'这项请求未纳入更改；FINVIS 没有给出财务判断。',
  addOnlyUnsupported:'若要排除这项独立请求继续审阅，请选择“仅新增”。本次不会替换旧记录；若需范围明确的替换，请修改陈述。',
  preparedCount:'已从你的原文准备 {count} 段依据',
});

COPY.en.coverage_review='What does this replacement report cover?';COPY['zh-CN'].coverage_review='这次替换陈述覆盖哪些财务信息？';
export const interactionText=(key,vars={})=>((COPY[language]||COPY.en)[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(vars[name]??''));

export function clarificationForm(state,{el,field,choice,detail,edit}){
  const T=interactionText,form=el('div',undefined,'i3-clarification-form'),getters=[];
  const interaction=state.interaction;
  form.append(el('p',T(interaction?.blockers?.length?'blockedIntro':'intro'),'i3-lead'));
  const facts=interaction?.evidence?.understood||[];
  if(facts.length){const summary=el('details',undefined,'i3-understood');summary.append(el('summary',T('understood',{count:facts.length})));
    const list=el('ul');facts.forEach(f=>list.append(el('li',f.label)));summary.append(list);form.append(summary);}
  const unsupported=interaction?.evidence?.unsupported||[];
  if(unsupported.length){const summary=el('section',undefined,'i3-unsupported');summary.append(el('h3',T('unsupportedTitle')));
    const list=el('ul');unsupported.forEach(item=>{const label=item.label==='evaluate_decision'||item.label==='financial_verdict'?T('financial_verdict_label'):item.label;
      const row=el('li');row.append(el('strong',label),el('p',`${T('unsupportedWhy')} ${T(item.reason)}`));list.append(row);});
    summary.append(list,el('p',T(interaction?.blockers?.length?'blockedNext':'independentNext')));form.append(summary);
  }else if(interaction?.evidence?.advice_notice)form.append(el('p',T('adviceNotice'),'i3-advice-note'));

  const select=(items)=>choice(items,T('choose'));
  const required=control=>{if(!control.value.trim())throw Error('incomplete');return control.value;};
  const checks=(items,card)=>items.map(([label,value])=>{const input=el('input');input.type='checkbox';card.append(field(label,input));return {value,input};});
  const checked=items=>items.filter(x=>x.input.checked).map(x=>x.value);
  for(const q of (interaction?.questions||[]).slice(0,2)){
    const card=el('section',undefined,'i3-clarification');card.dataset.question=q.id;
    card.append(el('h3',T(q.kind)),el('p',q.label,'i3-muted'));
    const fact=state.candidate.interpretation.candidate_facts.find(f=>`source:${f.id}`===q.id);
    if(fact?.amount){const a=fact.amount;card.append(el('p',`${a.currency||state.candidate.interpretation.default_currency||''} ${a.precision==='approximate'?'≈ ':''}${a.precision==='range'?`${a.range_min}–${a.range_max}`:a.value??(language==='zh-CN'?'金额未知':'Amount unknown')}`));}
    if(q.kind==='scope'){
      if(unsupported.length)card.append(el('p',T('addOnlyUnsupported'),'i3-muted'));
      card.classList.add('i3-warning');card.append(el('p',T('scopeIntro')));
      const mode=select(unsupported.length?[[T('add'),'independent_merge']]:[[T('add'),'independent_merge'],[T('replace'),'replace']]);card.append(field(T('scope'),mode));
      const scope=el('div');scope.hidden=true;const accounts=checks(q.accounts.map(a=>[a.label,a.value]),scope),currencies=checks(q.currencies.map(c=>[c,c]),scope);card.append(scope);
      if(q.required_currencies.length)scope.append(el('p',(language==='zh-CN'?'替换范围也必须包含新增余额的货币：':'The scope must also include the new balance currencies: ')+q.required_currencies.join(', '),'i3-muted'));
      mode.addEventListener('change',()=>{scope.hidden=mode.value!=='replace';});
      getters.push(()=>{const m=required(mode),a=checked(accounts),c=checked(currencies);if(m==='replace'&&(!a.length||!c.length||q.required_currencies.some(currency=>!c.includes(currency))))throw Error('scope');return [q.id,{mode:m,account_refs:m==='replace'?a:[],currencies:m==='replace'?c:[]}];});
    }else if(q.kind==='coverage'){
      const input=el('textarea');input.rows=2;card.append(field(T('coverage_review'),input));getters.push(()=>[q.id,required(input)]);
    }else if(q.kind==='correction'){
      const target=select(q.targets.map(item=>[item.value.kind==='monthly_essential_outflow'?`${t('agent.fact.monthly_essential_outflow')} · ${item.currency}`:item.label,JSON.stringify(item.value)])),reason=el('input');card.append(field(T('correction'),target),field(T('reason'),reason));
      getters.push(()=>[q.id,{target:JSON.parse(required(target)),reason:required(reason)}]);
    }else if(q.kind==='settlement'){
      const sourceSelect=()=>select(q.excerpts.map((s,i)=>[s.text,i]));
      const sourceValue=s=>{const part=q.excerpts[Number(required(s))];return {start:part.start,end:part.end};};
      const position=select(q.positions.map(p=>[p.label,JSON.stringify(p.value)])),account=sourceSelect(),basis=select([[T('exclusive'),'apply_to_prior_balance'],[T('inclusive'),'already_in_reported_balance']]),coverage=sourceSelect(),reason=el('input'),freshness=el('input');freshness.type='checkbox';
      card.append(field(T('position'),position),field(T('accountSource'),account),field(T('basis'),basis),field(T('coverage'),coverage),field(T('rationale'),reason),field(T('freshness'),freshness));
      getters.push(()=>{if(!freshness.checked)throw Error('freshness');return [q.id,{position_ref:JSON.parse(required(position)),account_source:sourceValue(account),basis:required(basis),coverage_source:sourceValue(coverage),coverage_rationale:required(reason),freshness_current:true}];});
    }else if(q.multiple){
      const items=checks(q.options.map(o=>[o.label,o.value]),card);
      getters.push(()=>{const values=checked(items);if(!values.length)throw Error('choices');return [q.id,values];});
    }else{
      const control=select(q.options.map(o=>[['role','composition'].includes(q.kind)?T(o.label):q.kind==='account'&&o.value==='new'?T('new_account'):q.kind==='independent_scope'?T(o.label):o.label,o.value]));if(q.kind==='independent_scope'){control.setAttribute('aria-label',T(q.kind));card.append(control);}else card.append(field(q.label||T(q.kind),control));
      getters.push(()=>[q.id,q.kind==='source'?Number(required(control)):required(control)]);
    }
    form.append(card);
  }
  for(const code of interaction?.blockers||[])if(!(code==='revise_unsupported_interpretation'&&unsupported.length))form.append(el('p',T(code),'i3-warning-text'));
  if(state.proposal?.issues?.length)form.append(el('p',T('blocked'),'i3-warning-text'));
  const editButton=el('button',T('edit'),'secondary-button');editButton.type='button';editButton.addEventListener('click',edit);form.append(editButton);
  const evidence=el('div'),prepared=(interaction?.evidence?.preparation?.facts||[]).filter(f=>f.status==='auto_verified'&&f.extraction_verified&&f.excerpt);
  for(const fact of prepared){const label=facts.find(item=>item.id===fact.id)?.label||fact.id;const row=el('div',undefined,'i3-prepared-row');row.append(el('strong',label),el('p',fact.excerpt));evidence.append(row);}
  if(prepared.length)form.append(detail(T('preparedCount',{count:prepared.length}),evidence));
  form.collect=()=>Object.fromEntries(getters.map(get=>get()));
  return form;
}
