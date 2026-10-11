/* Transient If selection. No storage, amounts, financial writer or inference. */
export const refKey=r=>r?`${r.kind}:${r.id}:${r.version}`:'';
export class IfSelection {
 constructor(records=[],snapshot={roots:[],options:[]}){this.records=records;this.selected=new Map();this.included=new Set(snapshot.roots.map(r=>r.id));for(const r of snapshot.options){const o=this.find(r);if(o?.variable)this.selected.set(o.variable.id,r);}this.prune();}
 find(r){return this.records.find(x=>refKey(x.ref)===refKey(r));}
 rows(kind){return this.records.filter(r=>r.ref.kind===kind);}
 children(n){return this.rows('node').filter(r=>r.parent?.id===n.ref.id);}
 variables(n){return this.rows('variable').filter(v=>v.node.id===n.ref.id);}
 options(v){const all=this.rows('option').filter(o=>refKey(o.variable)===refKey(v.ref));const replaced=new Set(all.map(o=>refKey(o.timing_replaces)).filter(Boolean));return all.filter(o=>!replaced.has(refKey(o.ref))||refKey(this.selected.get(v.ref.id))===refKey(o.ref));}
 displayReference(v){let current=this.find(v.reference);const seen=new Set();while(current&&!seen.has(refKey(current.ref))){seen.add(refKey(current.ref));const next=this.rows('option').filter(o=>refKey(o.variable)===refKey(v.ref)&&refKey(o.timing_replaces)===refKey(current.ref));if(next.length!==1)break;current=next[0];}return current;}
 root(n){let seen=new Set();while(n?.parent&&!seen.has(n.ref.id)){seen.add(n.ref.id);n=this.find(n.parent);}return n;}
 descendant(n,parent){const seen=new Set();while(n&&!seen.has(n.ref.id)){if(n.ref.id===parent.ref.id)return true;seen.add(n.ref.id);n=n.parent?this.find(n.parent):null;}return false;}
 conditions(row){const all=[...(row.active_when||[])];let n=row.ref.kind==='node'?row.parent&&this.find(row.parent):row.node&&this.find(row.node);const seen=new Set();while(n&&!seen.has(n.ref.id)){seen.add(n.ref.id);all.push(...(n.active_when||[]));n=n.parent?this.find(n.parent):null;}return all;}
 active(row){return this.conditions(row).every(r=>{const o=this.find(r);return o&&refKey(this.selected.get(o.variable.id))===refKey(r);});}
 eligible(o){if([...this.selected.values()].some(r=>{const other=this.find(r);return other?.variable.id!==o.variable.id&&(other?.excludes||[]).some(x=>refKey(x)===refKey(o.ref));}))return false;return (o.requires||[]).every(r=>{const q=this.find(r);return q&&refKey(this.selected.get(q.variable.id))===refKey(r);})&&!(o.excludes||[]).some(r=>{const q=this.find(r);return q&&refKey(this.selected.get(q.variable.id))===refKey(r);});}
 choose(o){const v=this.find(o.variable);if(!v||!this.active(v)||!this.eligible(o))return false;this.selected.set(v.ref.id,o.ref);const n=this.find(v.node),root=this.root(n);if(root)this.included.add(root.ref.id);this.prune();return true;}
 prune(){let changed=true;while(changed){changed=false;for(const [id,r] of this.selected){const o=this.find(r),v=o&&this.find(o.variable);if(!v||!this.active(v)||!this.eligible(o)){this.selected.delete(id);changed=true;}}}}
 layer(n){return this.rows('variable').filter(v=>this.descendant(this.find(v.node),n)&&this.active(v));}
 state(n){const vars=this.layer(n),count=vars.filter(v=>this.selected.has(v.ref.id)).length;return {checked:vars.length>0&&count===vars.length,indeterminate:count>0&&count<vars.length,count,total:vars.length};}
 toggle(n,checked){if(!checked){for(const v of this.rows('variable'))if(this.descendant(this.find(v.node),n))this.selected.delete(v.ref.id);this.included.delete(n.ref.id);this.prune();return [];}
 const root=this.root(n);if(root)this.included.add(root.ref.id);const questions=[];const visit=row=>{if(!this.active(row))return;for(const v of this.variables(row)){if(!this.active(v))continue;const available=this.options(v).filter(o=>this.eligible(o));if(!this.selected.has(v.ref.id)){if(available.length===1)this.choose(available[0]);else questions.push(v);}}for(const child of this.children(row))visit(child);};visit(n);return questions;}
 payload(){const roots=new Map();for(const id of this.included){const n=this.rows('node').find(n=>n.ref.id===id),r=n&&this.root(n);if(r)roots.set(r.ref.id,r.ref);}for(const [id] of this.selected){const v=this.rows('variable').find(v=>v.ref.id===id),r=v&&this.root(this.find(v.node));if(r)roots.set(r.ref.id,r.ref);}return {roots:[...roots.values()],options:[...this.selected.values()]};}
}
