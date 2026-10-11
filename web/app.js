'use strict';
let view = null, token = null, busy = false;
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value; };
const node = (tag, value, cls) => { const el = document.createElement(tag); if (value !== undefined) el.textContent = value; if (cls) el.className = cls; return el; };
const ref = r => r ? `${r.id} v${r.version}` : 'Not recorded';
const date = value => new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));
// Presentation only. All financial arithmetic and scenario outputs come from Python.
const money = m => `${m.currency} ${new Intl.NumberFormat('en-SG',{minimumFractionDigits:2,maximumFractionDigits:2}).format(m.minor_units / 100)}`;
const pendingLabels = {fx_required:'An approved CNY-to-SGD exchange-rate assumption is missing.',salary_basis_unresolved:'Gross or net salary, and applicable deductions, are unspecified.',net_income_unresolved:'Spendable net income has not been established.',employment_start_unresolved:'The employment start date and income timing are unknown.',income_cashflow_model_not_implemented:'The income-driven cash-flow model is not implemented in this prototype.'};
function controls() {
  for (const [id, allowed] of [['prepare','prepare'],['authorize','authorize'],['employment-action','employment']]) $(id).disabled = busy || !view || !view.allowed[allowed];
  $('reset-open').disabled = busy || !view;
  $('reset-confirm').disabled = busy || !view;
}
function render(v) {
  view = v;
  const d = v.display, accepted = v.state.employment.status === 'accepted_offer', executed = v.receipts.length > 0;
  text('version', `Living Plan · v${v.plan.ref.version}`);
  text('headline-impact', accepted ? `Offer accepted. Your plan has been re-evaluated; exact SGD income cash flow remains unresolved.` : `The ${d.trip} trip reduces your expense-only runway from ${d.before_runway} to ${d.after_runway} months.`);
  text('liquidity', d.liquidity); text('essentials', d.essentials);
  text('employment', accepted ? 'Offer accepted' : 'No confirmed offer');
  text('employment-note', accepted ? `${d.salary} / month · start date unknown` : 'Employment uncertainty remains open');
  text('runway-before', d.before_runway); text('runway-after', d.after_runway);
  text('trip', d.trip); text('after-trip', d.after_trip);
  text('delta', `Decision delta: ${d.liquidity_delta} in liquidity · ${d.runway_delta} months of runway.`);
  text('story-copy', `“I’m graduating soon, I don’t have a confirmed job offer yet, and I’m considering a ${d.trip} trip before returning home.”`);
  text('decision-caption', accepted ? 'ORIGINAL TRAVEL COMPARISON · HISTORICAL ASSUMPTIONS' : 'THE TRAVEL DECISION');
  text('calculation-model', `Model: ${v.evaluation.calculation.trace.model_version}. Source state: ${ref(v.evaluation.calculation.trace.state_ref)}. After employment acceptance, this original comparison is retained as history.`);
  const goal = v.goals[0];
  text('goal-name', `${goal.title} goal`); text('goal-date', date(goal.target_date)); text('goal-target', `${money(goal.target_amount)} target · ${goal.protection} goal`);
  text('prepare-note', executed ? 'Your authorized change is recorded below.' : v.action ? 'Proposal ready below. Your plan is unchanged.' : 'Reviewing a proposal does not authorize it.');
  text('prepare', executed ? 'Plan update recorded' : v.action ? 'Proposal ready for review' : 'Review Delay Car →');
  const active = v.lines.filter(l => l.record.status === 'active').length;
  text('line-count', `${active} active · ${v.lines.length - active} archived`);
  text('future-context', accepted ? 'The accepted offer resolved the no-confirmed-offer premise. These original futures are archived, with their assumptions and results preserved. Income timing remains unknown.' : 'Different futures depend on how long employment uncertainty lasts. These are conditional scenarios, not probability forecasts.');
  $('lines').replaceChildren(...v.lines.map(l => {
    const box = node('article', undefined, `line ${l.record.status}`);
    box.append(node('span', l.record.status === 'active' ? 'Active future' : 'Archived · premise resolved', 'status'), node('h3', `${l.projection.unemployment_months} month${l.projection.unemployment_months === 1 ? '' : 's'} without income`), node('div', l.with_trip, 'balance'), node('p', 'Projected balance with trip'), node('p', `Without trip: ${l.without_trip}`));
    if (l.record.archive_reason) box.append(node('p', l.record.archive_reason));
    return box;
  }));
  text('assumption-summary', v.evaluation.calculation.trace.projection_assumptions.rationale);
  const unique = new Map(v.lines.flatMap(l => l.assumptions).map(a => [ref(a.ref), a]));
  $('assumptions').replaceChildren(...Array.from(unique.values()).map(a => node('p', `${a.statement} (${a.status.replaceAll('_',' ')} at evaluation.)`)));
  text('plan-summary', `Current plan: v${v.plan.ref.version}. ${d.planned_liquidity} after recorded planned outflows; ${d.planned_runway} months expense-only runway. Observed funds remain ${d.liquidity}.`);
  $('plan-history').replaceChildren(...v.history.map(h => {const li=node('li'); li.append(node('strong', `v${h.record.ref.version}${h.record.ref.version === v.plan.ref.version ? ' · Current' : ' · Preserved'}`),node('p',h.reason),node('small',h.evidence_ref ? `Evidence: ${ref(h.evidence_ref)}` : 'Canonical synthetic starting plan'));return li;}));
  $('proposal').hidden = !v.action || executed;
  if (v.action) {
    const a = v.action;
    $('proposal-changes').replaceChildren(...[
      `Move Car from ${date(a.change.previous_target_date)} to ${date(a.change.proposed_target_date)}.`,
      `Add the ${money(a.planned_outflow)} trip as a planned outflow, not observed spending.`,
      `Create the next version of ${ref(a.expected_plan_ref)}; preserve its predecessor.`
    ].map(x=>node('li',x)));
    text('consent-note', v.authorization ? 'Authorization is recorded, but no successful execution receipt exists yet. You may retry this exact authorized update.' : 'You are authorizing this exact proposal for the current Living Plan.');
  }
  text('allocation-note', `Existing car contribution: ${d.contribution} / month, preserved without recalibration. The runway and branch model excludes this goal allocation and all other nonessential flows; it is not a full budget.`);
  text('offer-salary', d.salary || d.preview_salary);
  text('offer-title', accepted ? 'Accepted offer recorded.' : '“I got a job offer.”');
  text('employment-action', accepted ? 'Reality update recorded' : 'Simulate Reality Update');
  text('event-help', accepted ? 'Applied from the reviewed synthetic event. No employer, bank or email connection.' : executed ? 'Apply the fixed, reviewed demo event. Nothing is detected automatically.' : 'First complete your authorized plan update.');
  $('reality-result').hidden = !v.reevaluation;
  if (v.reevaluation) {
    text('resolution', `${v.reevaluation.resolved_if_line_refs.length} old futures archived. Living Plan v${v.reevaluation.before_plan_ref.version} → v${v.reevaluation.after_plan_ref.version}. ${v.reevaluation.materiality.rationale}.`);
    $('pending').replaceChildren(...v.pending_reasons.map(p=>node('li',pendingLabels[p] || p)));
    text('reevaluated-metrics', `Re-evaluated SGD-only plan: ${d.planned_liquidity}; ${d.planned_runway} months expense-only runway. New salary is excluded.`);
  }
  $('activity-empty').hidden = executed;
  text('activity-empty', v.authorization ? 'Authorization exists, but execution has not completed. No successful plan-change receipt is recorded.' : v.action ? 'Proposal recorded. No plan action has executed; explicit authorization is still required.' : 'No plan action has executed. Reviewing the option comes first; authorization remains yours.');
  const summaries = [];
  for (const r of v.receipts) {
    const consent = v.audit.records.find(x=>x.record_type==='PlanAuthorization' && ref(x.ref)===ref(r.authorization_ref));
    const before = v.audit.records.find(x=>x.record_type==='Goal' && ref(x.ref)===ref(r.before_goal_ref));
    const after = v.audit.records.find(x=>x.record_type==='Goal' && ref(x.ref)===ref(r.after_goal_ref));
    if (consent) summaries.push(`User authorization recorded · ${ref(consent.ref)}`);
    if (before && after) summaries.push(`Car goal moved · ${date(before.target_date)} → ${date(after.target_date)}`);
    summaries.push(`Living Plan v${r.after_plan_ref.version} created; v${r.before_plan_ref.version} preserved.`, `Execution evidence recorded · ${r.outcome}. ${r.reason}`);
  }
  if (v.reevaluation) summaries.push(`Reality update applied · ${v.reevaluation.resolved_if_line_refs.length} futures archived; Living Plan v${v.reevaluation.after_plan_ref.version} re-evaluated.`);
  $('receipt-summary').replaceChildren(...summaries.map(s=>node('li',s)));
  const r = v.receipts[0];
  const rows = [['Decision',ref(v.decision.ref)],['Selected option',ref(v.decision.selected_option_ref)],['Proposal',ref(v.action?.ref)],['Authorization',ref(v.authorization?.ref)],['Execution',r ? `${r.outcome} · ${r.recorded_at}` : 'Not executed'],['Changed fields',r ? r.changed_fields.join(', ') : 'None'],['Reality event',ref(v.reevaluation?.event_ref)],['State / plan',`${ref(v.state.ref)} / ${ref(v.plan.ref)}`],['History',`${v.audit.records.length} immutable records · ${v.revision} committed operations`]];
  $('audit-summary').replaceChildren(...rows.flatMap(([a,b])=>[node('dt',a),node('dd',b)]));
  text('audit-json',JSON.stringify(v.audit,null,2));
  controls();
}
function feedback(message, error=false) {
  text('feedback',message); $('feedback').hidden=false;
  $('feedback').setAttribute('role',error?'alert':'status');
  $('feedback').focus({preventScroll:true});
  if(error) $('feedback').scrollIntoView({block:'nearest'});
}
async function refresh() {
  try {const response=await fetch('/api/state',{cache:'no-store'}); if(!response.ok) throw Error(); const data=await response.json(); token=data.token; render(data.view); $('connection').hidden=true; return true;}
  catch {text('connection-text','Cannot reach FINVIS. Keep the launcher running, then refresh. No action is confirmed.');$('connection').hidden=false; view=null; controls();return false;}
}
async function command(name) {
  if(busy || !view) return;
  busy=true; controls();
  try {
    const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':token},body:JSON.stringify({command:name,expected:{generation:view.generation,revision:view.revision}})});
    const data=await response.json(); if(data.view) render(data.view);
    if(!response.ok){if(!data.view) await refresh();feedback(data.error || 'The action was not completed. Review the current state.',true);return;}
    const messages={prepare:'Proposal ready. Your Living Plan has not changed. Review it before authorizing.',authorize:'Your authorized plan update is recorded. Inspect the new version and execution evidence.',employment:'Reality update recorded. Old futures are archived; your Living Plan has been re-evaluated.',reset:'Demo reset. The original plan and three active futures are restored.'};
    feedback(messages[name]);
    const target={prepare:'proposal',authorize:'activity',employment:'reality',reset:'overview'}[name];
    $(target).scrollIntoView({block:'start'});
    if(name==='prepare') $('proposal').focus({preventScroll:true});
  } catch {await refresh(); feedback('The response was interrupted. Review the refreshed plan and audit trail before retrying; the action may already have completed.',true);}
  finally {busy=false;controls();}
}
$('prepare').addEventListener('click',()=>command('prepare'));
$('authorize').addEventListener('click',()=>command('authorize'));
$('employment-action').addEventListener('click',()=>command('employment'));
$('reset-open').addEventListener('click',()=>{$('reset-dialog').showModal();$('reset-cancel').focus();});
$('reset-cancel').addEventListener('click',()=>$('reset-dialog').close());
$('reset-confirm').addEventListener('click',()=>{$('reset-dialog').close();command('reset');});
$('refresh').addEventListener('click',refresh);
refresh();
