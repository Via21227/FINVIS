/* Explicit payment/receipt date editor; no inference or financial writer. */
import {node} from './shared.js';
import {language} from './i18n.js';
const t=(en,zh)=>language==='zh-CN'?zh:en;
export function inlineTiming(option,{save}){
 const box=node('div',undefined,'ft-editor');if(option.effects?.length!==1||option.effects[0].absence||option.effects[0].time_basis!=='one_time')return box;
 const timing=option.effects[0].money_timing,inflow=option.effects[0].effect_type==='expected_inflow';
 const label=inflow?t('Receive','到账'):t('Pay','付款');
 const button=node('button',label+' · '+(timing?.value||t('When?','何时？')));button.type='button';button.setAttribute('aria-label',label+' · '+option.label);box.append(button);
 button.onclick=()=>{const input=node('input');input.type='text';input.placeholder=t('2026-11 or 2026-11-20','2026-11 或 2026-11-20');input.value=timing?.source_text||'';input.setAttribute('aria-label',label+' · '+option.label);const ok=node('button',t('Save','保存')),cancel=node('button',t('Cancel','取消'));ok.type=cancel.type='button';const commit=async()=>{if(!input.value.trim())return;ok.disabled=true;try{await save(input.value.trim());}finally{ok.disabled=false;}};ok.onclick=commit;cancel.onclick=()=>{box.replaceChildren(button);button.focus();};input.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();cancel.click();}if(e.key==='Enter'){e.preventDefault();commit();}};box.replaceChildren(input,ok,cancel);input.focus();};return box;
}

const num=(n,c)=>c+' '+new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-GB',{maximumFractionDigits:2}).format(n/100);
const amt=(a,c)=>a?(a.lower_minor===a.upper_minor?num(a.lower_minor,c):num(a.lower_minor,c)+'–'+num(a.upper_minor,c)):t('Unknown','未知');
export function renderTiming(parent,timing){
 let shown=false;
 for(const currency of timing?.currencies||[]){
  const groups=currency.groups;
  if(!groups.length)continue;shown=true;
  const section=node('section',undefined,'ft-timeline');section.dataset.currency=currency.currency;section.append(node('h3',t('When money changes','何时变化')),node('p',t('Date · change → after known events','日期 · 收支 → 已知变化后'),'ft-caption'));
  const values=groups.map(g=>g.balance?.lower_minor??0),span=Math.max(...values)-Math.min(...values);
  const chart=groups.length>2&&groups.length<=8&&span>Math.max(...values.map(Math.abs),1)*.05&&groups.every(g=>g.balance&&!g.partial&&g.balance.lower_minor===g.balance.upper_minor);
  const track=node('div',undefined,chart?'ft-track ft-stepped':'ft-track');
  if(chart){
   const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 800 90');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('aria-hidden','true');const values=groups.map(g=>g.balance.lower_minor),low=Math.min(0,...values),high=Math.max(1,...values),y=v=>80-70*(v-low)/(high-low),x=i=>800*(i+.5)/groups.length;let d='M '+x(0)+' '+y(values[0]);for(let i=1;i<groups.length;i++)d+=' H '+x(i)+' V '+y(values[i]);const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);track.append(svg);
  }
  const list=node('div',undefined,'ft-groups');list.style.setProperty('--ft-count',groups.length);
  for(const g of groups){const row=node('details',undefined,'ft-group');row.dataset.when=g.when;if(g.negative||g.crosses_zero)row.classList.add('ft-negative');const title=node('summary');const label=node('span',g.when);label.append(node('small',g.items.length===1?g.items[0].title:t(`${g.items.length} items`,`${g.items.length} 项`)));title.append(label,node('span',(g.known_change.lower_minor>=0?'+ ':'')+amt(g.known_change,currency.currency),'ft-change'));if(g.balance)title.append(node('strong',(g.approximate?'≈ ':'')+amt(g.balance,currency.currency)));row.append(title);
   for(const i of g.items){const part=node('div',undefined,'ft-event');part.append(node('span',i.title+(i.title===i.label?'':' · '+i.label)),node('strong',(i.effect_type==='cost'?'− ':'+ ')+amt(i.amount,currency.currency)));if(i.money_timing?.source_text)part.append(node('small',i.money_timing.source_text));row.append(part);}list.append(row);}
  track.append(list);section.append(track);
  const notes=[];if(currency.conditional_inflow)notes.push(t('Income not yet received','收入待到账'));if(currency.unknown_count)notes.push(t(`${currency.unknown_count} unknown amount(s) excluded`,`${currency.unknown_count} 项金额未计`));if(currency.undated.length)notes.push(t(`${currency.undated.length} undated item(s) excluded here`,`${currency.undated.length} 项时间未定`));if(!currency.starting_known)notes.push(t('Starting funds unknown','起始资金未知'));if(notes.length)section.append(node('p',notes.join(' · '),'ss-omissions'));
  const low=groups.find(g=>g.negative||g.crosses_zero);if(low){const message=low.negative?t('Known changes may leave a shortfall in ','按已知收支，可能缺钱：'):t('The known range may dip below zero in ','已知范围可能低于零：');section.append(node('p',message+low.when+' · '+amt(low.balance,currency.currency),'ft-shortfall'));}
  parent.append(section);
 }
 if(!shown&&timing?.currencies?.some(c=>c.undated.length))parent.append(node('p',t('To see when: add payment or receipt dates in your choices.','想看何时变化：返回选择，补充付款或到账时间。'),'ss-omissions ft-undated-note'));
 return shown;
}
