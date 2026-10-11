/* Presentation-only explanation of an existing frozen simulation; no recalculation. */
import {node} from './shared.js';
import {language} from './i18n.js';
const text=(en,zh)=>language==='zh-CN'?zh:en;
const months=['january','february','march','april','may','june','july','august','september','october','november','december'];
const chineseMonths=['一','二','三','四','五','六','七','八','九','十','十一','十二'];
export function requestedMonths(question){
 const found=new Set();for(const [index,month] of months.entries())if(new RegExp('\\b'+month+'\\b','i').test(question))found.add(index+1);
 for(const match of question.matchAll(/(?:^|[^\d])(?:\d{4}\s*年\s*)?(1[0-2]|0?[1-9])\s*月/gu))found.add(Number(match[1]));
 for(const match of question.matchAll(/(?:^|[^一二三四五六七八九十])([一二三四五六七八九十]{1,3})月/gu)){const index=chineseMonths.indexOf(match[1]);if(index>=0)found.add(index+1);}
 for(const match of question.matchAll(/\b\d{4}-(0[1-9]|1[0-2])(?:-\d{2})?\b/gu))found.add(Number(match[1]));
 return [...found];
}
export function explanationGroups(out,question){
 const wanted=requestedMonths(question),years=[...question.matchAll(/\b(\d{4})(?=年|\b)/gu)].map(x=>x[1]);
 return (out.timing?.currencies||[]).flatMap(currency=>currency.groups.filter(group=>{const date=/^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(group.when);return !!date&&(!wanted.length||wanted.includes(Number(date[2])))&&(!years.length||years.includes(date[1]));}).map(group=>({currency:currency.currency,group,context:currency})));
}
const amount=(value,currency,approximate=false)=>{
 if(!value)return text('Unknown','未知');
 const format=n=>currency+' '+new Intl.NumberFormat(language==='zh-CN'?'zh-CN':'en-GB',{maximumFractionDigits:2}).format(n/100);
 return (approximate?'≈ ':'')+(value.lower_minor===value.upper_minor?format(value.lower_minor):format(value.lower_minor)+'–'+format(value.upper_minor));
};
export function renderSimulationWhy(host,out,question,{stale=false,earlier=false}={}){
 host.dataset.why='frozen-simulation';
 if(earlier)host.append(node('p',text('Earlier simulation · your current choices may differ.','上次模拟 · 当前选择可能已有不同。'),'ss-omissions'));
 if(stale)host.append(node('p',text('This explains the earlier result. Information has changed since then.','这里解释的是原结果，之后的信息已有变化。'),'ss-omissions'));
 const wanted=requestedMonths(question),groups=explanationGroups(out,question);
 if(wanted.length){
  if(!groups.length)host.append(node('p',text('This simulation has no dated change for that month. Check payment and receipt dates in your choices.','本次模拟没有该月已确定时间的变化。请检查所选项目的付款或到账时间。')));
  for(const {currency,group,context} of groups){
   const section=node('section',undefined,'why-month');section.dataset.when=group.when;section.append(node('h3',group.when+' · '+currency));
   const list=node('ul');for(const item of group.items){const label=item.title===item.label?item.title:item.title+' · '+item.label;list.append(node('li',label+' · '+(item.effect_type==='cost'?'− ':'+ ')+amount(item.amount,currency,item.approximate)+(item.effect_type==='expected_inflow'?text(' · if received',' · 待到账'):'')));}section.append(list);
   section.append(node('p',text('Known change: ','已知收支：')+amount(group.known_change,currency,group.approximate)),node('p',text('After known events: ','已知变化后：')+amount(group.balance,currency,group.approximate)));
   const notes=[];if(context.conditional_inflow)notes.push(text('Includes income only if received','收入以实际到账为前提'));if(context.unknown_count)notes.push(text('Unknown amounts excluded','未计未知金额'));if(context.undated?.length)notes.push(text('Undated items excluded here','此处未计时间未定项目'));if(!context.starting_known)notes.push(text('Starting funds unknown','起始资金未知'));if(notes.length)section.append(node('p',notes.join(' · '),'ss-omissions'));host.append(section);
  }
 }else{
  host.append(node('p',text('Recorded funds + selected income − selected spending.','记录资金＋所选收入－所选支出。')));
  for(const bucket of out.buckets||[])host.append(node('p',bucket.currency+' · '+text('After known choices: ','已知选择后：')+amount(bucket.after,bucket.currency,bucket.starting_approximate||out.items.some(i=>i.currency===bucket.currency&&i.approximate))));
 }
 host.append(node('p',text('Monthly cash flow and unknown amounts are excluded, not zero. Expected income is not guaranteed.','每月收支和未知金额未计入，不代表零；预期收入不保证到账。'),'ss-omissions'));
 if(/买得起|承受得起|应该|建议|\b(?:afford|should|recommend)\b/iu.test(question))host.append(node('p',text('This explains the calculation, not whether you should buy.','这里只解释计算，不判断是否应该购买。')));
}
