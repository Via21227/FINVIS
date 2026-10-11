import {applyTranslations, formatDate, t} from './i18n.js';
import {$, bindAgentComposer, bindLanguage, bindLocalAI, createAgentProcessingController, createClient, interpretGlobalInput, isAccepted, isExecuted, localize, node, pendingItems, planStatus, renderActiveDecisions, renderAgentCandidate, renderAudit, renderEvidence, renderGoals, renderHistory, renderIfLines, renderLocalAIConnection, renderProposal, selectIfLine, semanticText, supportedIfMonths} from './shared.js';
import {installSurface} from './simple_surface.js';
import {initTheme} from './theme.js';
import {createI3UI} from './i3.js';
import {createUserHome} from './i4_home.js';
import {createUserPages} from './i5_pages.js';
import {focusIfDraft} from './reality_evaluation.js';

let client,i3,i4,i5;
function showFeedback(message,error=false,tone='default'){const feedback=$('feedback');feedback.textContent=message;feedback.hidden=false;feedback.dataset.tone=error?'error':tone;feedback.setAttribute('role',error?'alert':'status');feedback.focus({preventScroll:true});}
function setControls({view,busy}){$('reset-open').disabled=busy||!view;$('prepare').disabled=busy||!view||!view.allowed.prepare;$('authorize').disabled=busy||!view||!view.allowed.authorize;$('employment-action').disabled=busy||!view||!view.allowed.employment;}
function openPanel(id){document.querySelectorAll('.workspace-panel').forEach(panel=>{const active=panel.id===id;panel.hidden=!active;panel.classList.toggle('active',active);});document.querySelectorAll('.workspace-tabs button').forEach(button=>button.classList.toggle('active',button.dataset.panel===id));}
function openWorkspace(panel='decision-panel'){if(i4?.isUser()){i5.open({'decision-panel':'decision','plan-panel':'plan','futures-panel':'future','evidence-panel':'activity'}[panel]);return;}if(!$('workspace').open)$('workspace').showModal();openPanel(panel);document.querySelector(`[data-panel="${panel}"]`).focus({preventScroll:true});}
function inspectIf(months){openWorkspace('futures-panel');selectIfLine($('if-lines'),months);$('if-feedback').textContent=t('if.supported',{months});}
function routeAgentIntent(intent){
  if(intent==='trip'){openWorkspace('decision-panel');showFeedback(t('agent.routed.trip'));return true;}
  if(intent==='offer'){openWorkspace('plan-panel');showFeedback(t('agent.routed.offer'));return true;}
  if(intent==='state'){$('state-detail').showModal();showFeedback(t('agent.routed.state'));return true;}
  if(/^if[136]$/.test(intent||'')){const months=Number(intent.slice(2));inspectIf(months);showFeedback(t('agent.routed.if',{months}));return true;}
  showFeedback(t('agent.unsupported'),true);return false;
}
async function submitGlobal(value){if(i4?.isUser()){if(await i5.interpret(value))return true;return i3.interpret(value);}const intent=interpretGlobalInput(value);if(intent)return routeAgentIntent(intent);return i3.interpret(value);}
function submitIf(value){const months=supportedIfMonths(value);if(!months){$('if-feedback').textContent=t('if.unsupported');return false;}selectIfLine($('if-lines'),months);$('if-feedback').textContent=t('if.supported',{months});return true;}

function render(view){
  localize();const accepted=isAccepted(view),executed=isExecuted(view),display=view.display;
  $('liquidity').textContent=display.liquidity;$('current-runway').textContent=display.observed_runway;$('employment').textContent=t(accepted?'employment.accepted':'employment.open');
  $('state-detail-liquidity').textContent=display.liquidity;$('state-detail-employment').textContent=t(accepted?'employment.accepted':'employment.open');$('state-detail-employment-note').textContent=t(accepted?'employment.acceptedNote':'employment.openNote',{salary:display.salary});$('state-detail-essentials').textContent=display.essentials;
  $('plan-version').textContent=`v${view.plan.ref.version}`;$('plan-state').textContent=planStatus(view);
  const decisions=renderActiveDecisions($('active-decisions'),view,()=>openWorkspace('decision-panel'));$('decision-count').textContent=t('decisions.count',{count:decisions.length});renderGoals($('goals-list'),view);
  const goal=view.goals[0];const goalTitle=semanticText('goal',goal.ref.id,goal.title);$('home-goal-title').textContent=goalTitle;$('home-goal-date').textContent=t('goal.homeDate',{date:formatDate(goal.target_date)});$('state-manager-goal').textContent=goalTitle;
  $('plan-overview-copy').textContent=t('plan.summary',{version:view.plan.ref.version,liquidity:display.planned_liquidity,runway:display.planned_runway});
  $('observation-copy').textContent=t(accepted?'observation.offer':'observation.open');$('observation-action').textContent=t(accepted?'observation.reality':'observation.update');
  $('workspace-status').textContent=t(view.reevaluation?'workspace.status.reality':executed?'workspace.status.executed':view.action?'workspace.status.prepared':'workspace.status.initial');
  $('decision-caption').textContent=t(accepted?'decision.historical':'decision.active');$('story-copy').textContent=t('decision.story',{trip:display.trip});
  $('detail-before').textContent=display.before_runway;$('detail-after').textContent=display.after_runway;$('before-liquidity').textContent=display.liquidity;$('trip').textContent=display.trip;$('after-trip').textContent=display.after_trip;$('delta').textContent=t('decision.delta',{liquidity:display.liquidity_delta,runway:display.runway_delta});
  const active=view.lines.filter(line=>line.record.status==='active').length;$('line-count').textContent=t('futures.count',{active,archived:view.lines.length-active});$('future-context').textContent=t(accepted?'futures.contextArchived':'futures.contextOpen');$('reality-origin').textContent=t(accepted?'futures.originAccepted':'futures.originOpen');renderIfLines($('if-lines'),view,months=>{$('if-feedback').textContent=t('if.supported',{months});});
  renderProposal(view);renderHistory($('plan-history'),view);$('allocation-note').textContent=t('goal.note',{contribution:display.contribution});
  $('offer-title').textContent=t(accepted?'reality.recordedTitle':'reality.offerTitle');$('offer-salary').textContent=display.salary||display.preview_salary;$('employment-action').textContent=t(accepted?'reality.recorded':'reality.simulate');$('event-help').textContent=t(accepted?'reality.done':executed?'reality.ready':'reality.wait');$('reality-result').hidden=!view.reevaluation;
  if(view.reevaluation){$('resolution').textContent=t('reality.resolution',{count:view.reevaluation.resolved_if_line_refs.length,before:view.reevaluation.before_plan_ref.version,after:view.reevaluation.after_plan_ref.version});$('pending').replaceChildren(...pendingItems(view).map(value=>node('li',value)));$('reevaluated-metrics').textContent=t('reality.metrics',{liquidity:display.planned_liquidity,runway:display.planned_runway});}
  renderEvidence($('receipt-summary'),$('activity-empty'),view);renderAudit($('audit-summary'),view);$('audit-json').textContent=JSON.stringify(view.audit,null,2);if(view.agent_candidate)renderAgentCandidate(view.agent_candidate);
}

const setAgentProcessing=createAgentProcessingController($('finvis-command'),$('agent-submit'),$('agent-processing'));
client=createClient({render,setControls,showFeedback,showCandidate:candidate=>renderAgentCandidate(candidate,{open:true}),renderLocalAI:status=>renderLocalAIConnection(status,id=>client.selectLocalModel(id)),setAgentProcessing});bindLocalAI(client);
bindLanguage($('language-switch'),()=>{$('feedback').hidden=true;client.rerender();client.rerenderLocalAI();composer.reflect();setAgentProcessing.refresh();i3?.render();i4?.render();i5?.render();});applyTranslations();
const composer=bindAgentComposer($('finvis-input'),$('finvis-command'),()=>submitGlobal($('finvis-input').value));
i4=createUserHome({showFeedback});i5=createUserPages(i4);i3=createI3UI({showFeedback,composer,setAgentProcessing,onApplied:()=>i4.applied(),onRealityPrepared:done=>i5.resumeHistoryAction(done),onHypothetical:(id,text)=>focusIfDraft(id,text,i5.open,showFeedback)});setAgentProcessing.setStop(()=>i3.stopInterpretation());
document.querySelectorAll('[data-agent-example]').forEach(button=>button.addEventListener('click',()=>{composer.set(button.textContent);routeAgentIntent(button.dataset.agentExample);}));
$('contextual-if').addEventListener('submit',event=>{event.preventDefault();submitIf($('if-input').value);});
document.querySelectorAll('[data-if-months]').forEach(button=>button.addEventListener('click',()=>inspectIf(Number(button.dataset.ifMonths))));
document.querySelector('[data-if-custom]').addEventListener('click',()=>{$('if-input').value='';$('if-input').focus();$('if-feedback').textContent=t('if.unsupported');});
$('refresh').addEventListener('click',()=>client.refresh());$('nav-decision').addEventListener('click',()=>openWorkspace('decision-panel'));$('nav-plan').addEventListener('click',()=>openWorkspace('plan-panel'));$('nav-future').addEventListener('click',()=>openWorkspace('futures-panel'));$('nav-activity').addEventListener('click',()=>openWorkspace('evidence-panel'));
$('open-state-detail').addEventListener('click',()=>$('state-detail').showModal());$('state-detail-close').addEventListener('click',()=>$('state-detail').close());$('open-goals').addEventListener('click',()=>$('goals-detail').showModal());$('goals-detail-close').addEventListener('click',()=>$('goals-detail').close());
$('state-open-employment').addEventListener('click',()=>{$('state-detail').close();openWorkspace('plan-panel');});$('state-open-goal-flow').addEventListener('click',()=>{$('state-detail').close();openWorkspace('plan-panel');});
$('observation-action').addEventListener('click',()=>openWorkspace('plan-panel'));$('workspace-close').addEventListener('click',()=>$('workspace').close());document.querySelectorAll('.workspace-tabs button').forEach(button=>button.addEventListener('click',()=>openPanel(button.dataset.panel)));$('to-futures').addEventListener('click',()=>openPanel('futures-panel'));$('to-plan').addEventListener('click',()=>openPanel('plan-panel'));
$('prepare').addEventListener('click',async()=>{if(await client.command('prepare')){openPanel('plan-panel');$('authorize').focus();}});$('authorize').addEventListener('click',async()=>{if(await client.command('authorize'))openPanel('evidence-panel');});$('employment-action').addEventListener('click',async()=>{if(await client.command('employment'))openPanel('plan-panel');});
$('agent-confirm').addEventListener('click',async()=>{if($('agent-preview').dataset.world==='user')return;if(await client.resolveCandidate('confirm',$('agent-preview').dataset.candidateId)){$('agent-preview').close();composer.clear();}});$('agent-cancel').addEventListener('click',async()=>{if($('agent-preview').dataset.world==='user'){await i3.cancel();return;}if(await client.resolveCandidate('cancel',$('agent-preview').dataset.candidateId)){$('agent-preview').close();composer.clear();}});$('reset-open').addEventListener('click',()=>$('reset-dialog').showModal());$('reset-cancel').addEventListener('click',()=>$('reset-dialog').close());$('reset-confirm').addEventListener('click',async()=>{if(await client.command('reset')){$('reset-dialog').close();for(const id of ['workspace','state-detail','goals-detail'])if($(id).open)$(id).close();}});
  $('mobile-preview-open').addEventListener('click',()=>{$('mobile-preview').showModal();$('mobile-frame').contentWindow?.postMessage({type:'finvis-context',context:i4.isUser()?'user':'demo'},location.origin);$('mobile-frame').contentWindow?.postMessage({type:'finvis-refresh'},location.origin);});$('mobile-preview-close').addEventListener('click',()=>$('mobile-preview').close());window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data?.type==='finvis-state-changed'){client.refresh({quiet:true});i4.refresh();}});
client.refresh().finally(()=>window.finvisMarkBootReady?.());

initTheme();installSurface(i4);
