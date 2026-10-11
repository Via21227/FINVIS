/* Presentation only. Domain code never imports or consumes this registry. */
export const SEMANTIC_ICONS=Object.freeze({other:'◌',travel:'🧳',flight:'✈️',accommodation:'🏨',dining:'🍽️',local_transport:'🚇',activity:'🎫',camera:'📷',concert:'🎵',phone:'📱',emergency:'🛡️',electronics:'🔌',moving:'📦',training:'📚'});
export function semanticCue(category, country=null){
  if(category==='travel'&&/^[A-Z]{2}$/.test(country||''))return [...country].map(c=>String.fromCodePoint(127397+c.charCodeAt(0))).join('');
  return SEMANTIC_ICONS[category]||SEMANTIC_ICONS.other;
}
