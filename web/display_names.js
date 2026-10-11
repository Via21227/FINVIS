import {language} from './i18n.js';
import {node} from './shared.js';
import {DisplayNames,compactName} from './ux_state.js';
export {compactName};
export const displayName=(scope,kind,id,original)=>{try{return new DisplayNames(localStorage,scope).get(kind,id,original);}catch{return compactName(original);}};
export function renameButton(scope,kind,id,original,onSaved){
 const zh=language==='zh-CN',b=node('button',zh?'改名':'Rename','secondary-button ux-rename');b.type='button';b.disabled=!scope;
 b.onclick=()=>{const d=node('dialog',undefined,'fs-review ux-name-dialog'),title=node('h2',zh?'显示名称':'Display name'),label=node('label',undefined,'fs-field'),input=node('input');input.value=displayName(scope,kind,id,original);input.maxLength=80;input.setAttribute('aria-label',zh?'显示名称':'Display name');label.append(input);const note=node('p',zh?'名称保存在此浏览器，重启后仍在；原始陈述和财务记录不会改变。':'Saved in this browser across restarts. Original evidence and financial records stay intact.','fs-note'),error=node('p','','fs-error'),save=node('button',zh?'保存名称':'Save name','primary-button'),cancel=node('button',zh?'取消':'Cancel','secondary-button');save.type=cancel.type='button';save.onclick=()=>{try{new DisplayNames(localStorage,scope).set(kind,id,input.value);d.close();onSaved?.();window.dispatchEvent(new Event('finvis-names-changed'));}catch{error.textContent=zh?'名称须为 1–80 个字符，且浏览器需要允许本地保存。':'Use 1–80 characters and allow local browser storage.';}};cancel.onclick=()=>d.close();d.append(title,label,note,error,save,cancel);d.onclose=()=>d.remove();document.body.append(d);d.showModal();input.focus();};return b;
}

// Same scoped presentation-name store as the legacy Advanced editor.
export function saveDisplayName(scope,kind,id,value){
 new DisplayNames(localStorage,scope).set(kind,id,value);
 window.dispatchEvent(new Event('finvis-names-changed'));
}
export function inlineTitle(scope,kind,id,original){
 const zh=language==='zh-CN',wrap=node('span',undefined,'if-title'),trigger=node('button',displayName(scope,kind,id,original),'if-title-value');
 trigger.type='button';trigger.disabled=!scope;trigger.title=zh?'编辑名称':'Edit name';
 trigger.onclick=()=>{const form=node('form',undefined,'if-title-editor'),input=node('input'),save=node('button','✓'),cancel=node('button','×'),error=node('span','','fs-error');
 input.value=displayName(scope,kind,id,original);input.maxLength=80;input.setAttribute('aria-label',zh?'显示名称':'Display name');save.type='submit';cancel.type='button';save.setAttribute('aria-label',zh?'保存名称':'Save name');cancel.setAttribute('aria-label',zh?'取消编辑':'Cancel edit');error.id='name-error-'+crypto.randomUUID();error.setAttribute('role','alert');input.setAttribute('aria-describedby',error.id);
 const back=()=>{wrap.replaceChildren(trigger);trigger.focus();};cancel.onclick=back;input.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();back();}};
 form.onsubmit=e=>{e.preventDefault();try{saveDisplayName(scope,kind,id,input.value);requestAnimationFrame(()=>document.querySelector('[data-node-id="'+CSS.escape(id)+'"] > .if2-row .if-title-value')?.focus());}catch{error.textContent=zh?'请输入 1–80 个字符，并允许浏览器保存。':'Use 1–80 characters and allow browser storage.';input.setAttribute('aria-invalid','true');input.focus();}};
 form.append(input,save,cancel,error);wrap.replaceChildren(form);input.focus();input.select();};wrap.append(trigger);return wrap;
}
