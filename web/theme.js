/* Presentation preferences only. No User World, provider, or financial API access. */
export const THEME_KEY='finvis-theme-v1';
export const normalizeTheme=value=>['system','light','dark'].includes(value)?value:'system';
export const resolveTheme=(preference,systemDark)=>normalizeTheme(preference)==='system'?(systemDark?'dark':'light'):normalizeTheme(preference);

export function readThemePreference(storage){
  try{return normalizeTheme(storage?.getItem(THEME_KEY));}catch{return 'system';}
}
export function writeThemePreference(storage,value){
  const preference=normalizeTheme(value);
  try{storage?.setItem(THEME_KEY,preference);}catch{/* The current presentation still works when storage is unavailable. */}
  return preference;
}

let instance;
export function initTheme(){
  if(instance)return instance;
  let storage;try{storage=window.localStorage;}catch{}
  const root=document.documentElement;
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  let preference=readThemePreference(storage);
  const host=document.querySelector('.product-actions,.mobile-head-actions');
  const label=document.createElement('label');label.className='finvis-theme-control';
  const caption=document.createElement('span');caption.className='sr-only';
  const select=document.createElement('select');select.id='finvis-theme';
  for(const value of ['system','light','dark']){const option=document.createElement('option');option.value=value;select.append(option);}
  label.append(caption,select);host?.append(label);

  function localize(){
    const zh=root.lang==='zh-CN';
    caption.textContent=zh?'外观':'Appearance';
    select.setAttribute('aria-label',caption.textContent);
    const names=zh?['跟随系统','浅色','深色']:['System','Light','Dark'];
    [...select.options].forEach((option,index)=>option.textContent=names[index]);
  }
  function broadcast(){
    const message={type:'finvis-theme',preference};
    if(window.parent!==window)window.parent.postMessage(message,location.origin);
    for(const frame of document.querySelectorAll('#mobile-frame'))frame.contentWindow?.postMessage(message,location.origin);
  }
  function apply(){
    const resolved=resolveTheme(preference,media.matches);
    root.dataset.theme=resolved;root.dataset.themePreference=preference;root.style.colorScheme=resolved;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute('content',resolved);
    select.value=preference;localize();
    window.dispatchEvent(new CustomEvent('finvis-theme-changed',{detail:{preference,resolved}}));
  }
  function set(value,{persist=true,sync=true}={}){
    preference=normalizeTheme(value);
    if(persist)writeThemePreference(storage,preference);
    apply();if(sync)broadcast();
  }
  select.addEventListener('change',()=>set(select.value));
  const systemChanged=()=>{if(preference==='system')apply();};
  if(media.addEventListener)media.addEventListener('change',systemChanged);else media.addListener(systemChanged);
  window.addEventListener('storage',event=>{if(event.key===THEME_KEY||event.key===null)set(readThemePreference(storage),{persist:false,sync:false});});
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||!event.data||typeof event.data!=='object')return;
    const fromParent=window.parent!==window&&event.source===window.parent;
    const fromPreview=[...document.querySelectorAll('#mobile-frame')].some(frame=>frame.contentWindow===event.source);
    if(!fromParent&&!fromPreview)return;
    if(event.data.type==='finvis-theme-request'){event.source?.postMessage({type:'finvis-theme',preference},location.origin);return;}
    if(event.data.type==='finvis-theme'&&['system','light','dark'].includes(event.data.preference))set(event.data.preference,{persist:false,sync:false});
  });
  new MutationObserver(localize).observe(root,{attributes:true,attributeFilter:['lang']});
  apply();
  if(window.parent!==window)window.parent.postMessage({type:'finvis-theme-request'},location.origin);
  instance={set,get:()=>({preference,resolved:root.dataset.theme})};
  return instance;
}
