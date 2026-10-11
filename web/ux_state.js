/* Presentation-only state. Never changes a Financial World or Future record. */
export function compactName(source,limit=44){
 const original=String(source||'').trim();let value=original;
 const parts=value.split(/\s*[·]\s*/);if(parts.length>1&&/^(如果|what if)/i.test(parts.at(-1)))value=parts.at(-1);
 value=value.replace(/^(?:如果|假设|要是)\s*(?:我)?(?:买|购买)?\s*/u,'').replace(/^(?:what if|if|I am considering)\s+(?:I\s+)?(?:buy\s+|purchase\s+)?/i,'');
 value=value.replace(/(?:只(?:要|需要)|需要|大概要花|大约|约为)\s*[\d,.]+.*$/u,'').replace(/\s+(?:for|costing|costs?)\s+(?:(?:about|around)\s+)?(?:CNY|SGD|RMB)?\s*[\d,.]+.*$/i,'').replace(/[？?。!！呢]+$/u,'').trim()||original;
 return [...value].length>limit?[...value].slice(0,limit-1).join('')+'…':value;
}
export class DisplayNames{
 constructor(storage,scope){this.storage=storage;this.key='finvis-names-v1:'+scope;this.scope=scope;}
 read(){try{const data=JSON.parse(this.storage.getItem(this.key)||'{}');return data&&typeof data==='object'&&!Array.isArray(data)?data:{};}catch{return {};}}
 get(kind,id,original){const value=this.read()[kind+':'+id];return typeof value==='string'&&value.trim()?value:compactName(original);}
 set(kind,id,value){value=value.trim();if(!this.scope||!value||[...value].length>80)throw Error('name_invalid');const rows=this.read();if(Object.keys(rows).length>=512&&!Object.hasOwn(rows,kind+':'+id))throw Error('names_full');this.storage.setItem(this.key,JSON.stringify({...rows,[kind+':'+id]:value}));}
}
export class InputSession{
 constructor(storage){this.storage=storage;this.active=null;this.edits=0;this.draft='';this.key='finvis-recent-inputs-v1';}
 edited(){this.edits++;}
 start(text){this.active={text,edits:this.edits,done:false};return this.active;}
 recent(){try{const data=JSON.parse(this.storage.getItem(this.key)||'[]');return Array.isArray(data)?data.filter(x=>typeof x==='string').slice(0,12):[];}catch{return [];}}
 completion(read,clear,notify=()=>{}){const ticket=this.active;return ()=>{if(!ticket||ticket.done)return false;ticket.done=true;const rows=[ticket.text,...this.recent().filter(s=>s!==ticket.text)].slice(0,12);try{this.storage.setItem(this.key,JSON.stringify(rows));}catch{notify(false);return false;}if(this.active===ticket&&this.edits===ticket.edits&&read()===ticket.text)clear();notify(true);return true;};}
}
