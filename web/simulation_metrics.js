/* Presentation ratios only. Native currency, interval bounds; no Engine policy. */
export function spendingProportion(bucket) {
 const a=bucket.starting, c=bucket.known_cost;
 if (!a || !c || bucket.unsupported_count || bucket.unavailable_reason==='stale' || a.lower_minor<=0 || a.upper_minor<a.lower_minor || c.lower_minor<0 || c.upper_minor<=0) return null;
 return {lower:100*c.lower_minor/a.upper_minor,upper:100*c.upper_minor/a.lower_minor};
}
export function composition(items,currency) {
 const eligible=items.filter(i=>i.currency===currency&&i.effect_type==='cost'&&i.time_basis==='one_time'&&i.amount&&i.amount.lower_minor===i.amount.upper_minor&&i.amount.lower_minor>0);
 if(eligible.length<3)return new Map();
 const total=eligible.reduce((s,i)=>s+i.amount.lower_minor,0);
 return new Map(eligible.map(i=>[i,100*i.amount.lower_minor/total]));
}
