/* Competition-only bootstrap. Never loaded by the personal host. */
(()=>{
const epochMeta=document.querySelector('meta[name="finvis-competition-generation"]');
const generation=epochMeta?.content||'initial';
const epochKey='finvis-competition-generation';
function clearPreferences(){
  for(const storage of [localStorage,sessionStorage]){
    for(const key of Object.keys(storage))if(key.startsWith('finvis-')||key.startsWith('finvis.'))storage.removeItem(key);
  }
}
try{
  const seen=localStorage.getItem(epochKey);
  // Only an explicitly completed clear may write the shared epoch or clear
  // shared preferences. A late old document must never undo that signal.
  if(seen&&seen!==generation){
    for(const key of Object.keys(sessionStorage))if(key.startsWith('finvis-')||key.startsWith('finvis.'))sessionStorage.removeItem(key);
  }
  if(!localStorage.getItem('finvis-language'))localStorage.setItem('finvis-language','en');
  sessionStorage.setItem('finvis-home-context','user');
}catch{}
// Every mutation carries the epoch of the page that initiated it. Old tabs
// cannot adopt a fresh CSRF token and replay a pre-clear financial draft.
const originalFetch=window.fetch.bind(window);
window.fetch=(input,init)=>{
  const url=new URL(input instanceof Request?input.url:input,location.href);
  const method=(init?.method||(input instanceof Request?input.method:'GET')).toUpperCase();
  if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&method==='POST'){
    const headers=new Headers(init?.headers||(input instanceof Request?input.headers:undefined));
    headers.set('X-FINVIS-Competition-Generation',generation);
    init={...init,headers};
  }
  return originalFetch(input,init);
};
window.finvisEnvironment=Object.freeze({kind:'competition',defaultCurrency:'EUR'});
document.documentElement.dataset.competition='true';
window.addEventListener('storage',e=>{
  if(e.key===epochKey&&e.newValue&&e.newValue!==generation){
    try{for(const key of Object.keys(sessionStorage))if(key.startsWith('finvis-')||key.startsWith('finvis.'))sessionStorage.removeItem(key);}catch{}
    location.replace(location.pathname==='/mobile'?'/mobile':'/');
  }
});
const text=(en,zh)=>document.documentElement.lang.startsWith('zh')?zh:en;
const node=(tag,value)=>{const el=document.createElement(tag);if(value)el.textContent=value;return el;};
document.addEventListener('DOMContentLoaded',()=>{
  const brand=document.querySelector('.brand,.mobile-brand');
  if(brand){const label=node('span','Competition Demo');label.className='competition-label';brand.after(label);}
  if(window.top!==window||!epochMeta)return; // Preview and older hosts cannot expose clear.
  let entry,settings;
  const dialog=node('dialog');dialog.id='competition-clear';dialog.className='ss-settings competition-clear';
  const heading=node('h2');heading.id='competition-clear-title';dialog.setAttribute('aria-labelledby',heading.id);
  const note=node('p'),status=node('p');status.setAttribute('role','alert');
  const phrase=node('input');phrase.id='competition-clear-phrase';phrase.type='text';phrase.autocomplete='off';phrase.spellcheck=false;phrase.maxLength=5;
  const label=node('label');label.htmlFor=phrase.id;
  const footer=node('div');footer.className='competition-clear-actions';
  const cancel=node('button'),next=node('button');cancel.type=next.type='button';next.className='competition-clear-danger';footer.append(cancel,next);
  dialog.append(heading,note,label,phrase,status,footer);document.body.append(dialog);
  let stage=1,prepared,grant,busy=false;
  const show=()=>{
    dialog.dataset.step=String(stage);
    heading.textContent=text(`Clear Competition Demo · ${stage}/3`,`清空比赛版 · ${stage}/3`);
    note.textContent=stage===1?text('Only this Competition Demo will be emptied. Personal FINVIS and other demo instances stay unchanged. Previous records will be archived.','仅清空当前比赛实例，正式版和其他比赛实例不受影响。旧记录会保留为备份。'):stage===2?text('Type CLEAR to confirm. Current funds, Future, History, drafts and display preferences will be cleared from this instance.','输入 CLEAR 确认。此实例的资金、打算、历史、草稿和显示偏好将恢复为空。'):text('Final confirmation: start this Competition Demo empty. Previous records remain in its private backup.','最后确认：将当前比赛版恢复为空。旧记录保留在此实例的私有备份中。');
    label.textContent=text('Type CLEAR','输入 CLEAR');label.hidden=phrase.hidden=stage!==2;
    next.textContent=stage===1?text('Continue','继续'):stage===2?text('Confirm CLEAR','确认 CLEAR'):text('Clear this demo','清空当前比赛版');
    cancel.textContent=text('Cancel','取消');next.disabled=busy||(stage===2&&phrase.value!=='CLEAR');cancel.disabled=busy;
  };
  phrase.addEventListener('input',show);
  const post=async(action,payload)=>{
    const state=await(await fetch('/api/state')).json();
    const response=await fetch('/api/competition/reset/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-FINVIS-Token':state.token},body:JSON.stringify(payload)});
    const result=await response.json();if(!response.ok)throw new Error(result.code||'unavailable');return result;
  };
  const close=()=>dialog.close();cancel.addEventListener('click',close);
  dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
  dialog.tabIndex=-1;
  dialog.addEventListener('keydown',e=>{
    if(e.key!=='Tab')return;
    const list=[...dialog.querySelectorAll('button,input')].filter(el=>!el.disabled&&!el.hidden&&el.getClientRects().length);
    e.preventDefault();
    if(!list.length){dialog.focus();return;}
    const index=list.indexOf(document.activeElement);list[(index+(e.shiftKey?-1:1)+list.length)%list.length].focus();
  });
  dialog.addEventListener('close',()=>{prepared=grant=undefined;settings.showModal();entry.focus();});
  next.addEventListener('click',async()=>{
    if(busy||next.disabled)return;busy=true;show();status.textContent='';
    try{
      if(stage===1){prepared=await post('prepare',{});stage=2;}
      else if(stage===2){grant=await post('confirm',{confirmation_id:prepared.confirmation_id,phrase:phrase.value});stage=3;}
      else{
        const result=await post('apply',{confirmation_id:prepared.confirmation_id,authorization:grant.authorization});
        // The server already completed the clear. Browser storage failures must
        // never misreport it as failed or invite another apply.
        try{clearPreferences();}catch{}
        try{localStorage.setItem(epochKey,result.generation);}catch{}
        location.replace('/?competition-cleared=1');return;
      }
    }catch(error){
      stage=1;prepared=grant=undefined;phrase.value='';
      status.textContent=error.message==='competition_reset_busy'?text('Another action is running. Finish it, then start again.','另一个操作正在进行，请结束后重新开始。'):text('Clear was not completed. Refresh and check the demo before trying again.','清空未完成。请刷新并核对比赛版后再试。');
    }finally{busy=false;show();(stage===2?phrase:next).focus();}
  });
  const install=()=>{
    settings=document.querySelector('#ss-settings');if(!settings||entry)return;
    entry=node('button');entry.id='competition-clear-open';entry.type='button';entry.className='competition-clear-entry';
    entry.textContent=text('Clear Competition Demo','清空比赛版');settings.append(entry);
    entry.addEventListener('click',()=>{stage=1;prepared=grant=undefined;phrase.value='';status.textContent='';settings.close();show();dialog.showModal();cancel.focus();});
  };
  new MutationObserver(install).observe(document.body,{childList:true,subtree:true});install();
  new MutationObserver(()=>{if(entry)entry.textContent=text('Clear Competition Demo','清空比赛版');if(dialog.open)show();}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  if(new URLSearchParams(location.search).get('competition-cleared')==='1'){
    const url=new URL(location.href);url.searchParams.delete('competition-cleared');history.replaceState(null,'',url.pathname+url.search+url.hash);
    const notice=node('p',text('Competition Demo cleared. Previous records were archived.','比赛版已清空，旧记录已备份。'));notice.className='competition-clear-notice';notice.setAttribute('role','status');document.querySelector('main')?.prepend(notice);
    new MutationObserver(()=>{notice.textContent=text('Competition Demo cleared. Previous records were archived.','比赛版已清空，旧记录已备份。');}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  }
});
})();
