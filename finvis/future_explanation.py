"""Read-only, version-bound FS explanation using the existing FS1/D2 arithmetic.

Local narration may only group/reorder approved sentences. No free model prose,
financial writer, provider fallback, evaluation retention, or new judgment rule.
"""
import hashlib
import json
import re
from .future_space import FutureNode, FutureOption, SavedScenario, ReferenceFrame, FutureCatalog, require
from .future_experience import fields, ref, wire_ref, sentence
from .future_memory import validity
from .if_lines import bucket
from .world_values import AmountPrecision

REASONS = {
 'world_input_changed':('A supporting financial record changed.','支持它的财务记录已变化。'),
 'future_version_changed':('An option or reference estimate changed.','选项或参考估算已变化。'),
 'scope_membership_changed':('The event has different parts now.','事情所包含的项目已变化。'),
 'future_path_changed':('A related chosen path or actual event changed.','相关的已选计划或实际事件已变化。'),
 'evidence_stale':('Supporting evidence expired.','支持证据已过期。'),
 'world_evidence_stale':('A supporting observation is stale.','支持它的观察记录已过期。'),
 'future_archived':('A retained input was archived.','一个保留的输入已归档。'),
 'decision_context_changed':('Relevant decision context changed.','相关决策背景已变化。')}

def is_question(text):
    return bool(re.search(r'为什么|为何|主要影响|哪个因素|省了多少|能省多少|买得起|承受得起|应该买|建议|\b(?:why|main (?:impact|drivers?)|largest|biggest|sav(?:e|ing)|afford|should I|recommend)\b',text,re.I))

def mentions(label,text):
    if len(label)<2:
        return bool(re.search(r'(?:scenario|情景)\s*[“"\']?'+re.escape(label)+r'(?!\w)',text,re.I))
    if label.isascii():return bool(re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',text,re.I))
    return label.casefold() in text.casefold()

def intent(text):
    if re.search(r'过期|失效|stale|outdated|expired',text,re.I):return 'stale'
    if re.search(r'买得起|承受得起|应该|建议|afford|should I|recommend',text,re.I):return 'judgment'
    if re.search(r'省|sav(?:e|ing)',text,re.I):return 'savings'
    if re.search(r'哪个|最大|largest|biggest',text,re.I):return 'largest'
    return 'drivers'

def direction(a):
    if a['precision']=='unknown':return 'unknown'
    lo=a['value_minor'] if a['value_minor'] is not None else a['lower_minor']
    hi=a['value_minor'] if a['value_minor'] is not None else a['upper_minor']
    return 'increase' if lo>0 else 'decrease' if hi<0 else 'unchanged' if lo==hi==0 else 'uncertain'

def money(a,zh):
    def major(v):
        v=abs(v);whole,frac=divmod(v,100)
        return format(whole,',')+('.'+str(frac).zfill(2).rstrip('0') if frac else '')
    prefix='≈ ' if a['precision']=='approximate' else ''
    if a['precision']=='unknown':return a['currency']+(' 金额未知' if zh else ' amount unknown')
    def signed(v):return ('−' if v<0 else '')+major(v)
    if a['precision']=='range':return a['currency']+' '+signed(a['lower_minor'])+'–'+signed(a['upper_minor'])
    return prefix+a['currency']+' '+signed(a['value_minor'])

def unsigned(a):
    if direction(a)=='decrease':
        return dict(a,value_minor=-a['value_minor'] if a['value_minor'] is not None else None,
                    lower_minor=-a['upper_minor'] if a['upper_minor'] is not None else None,
                    upper_minor=-a['lower_minor'] if a['lower_minor'] is not None else None)
    return a

def choice_factors(c, factors, chosen, original, has_reference):
    """Collapse only mutually replacing ancestor/descendant coverage.

    Raw factors stay intact. Independent siblings, cost versus income, currencies,
    time bases and control types never net against one another. Reuse FS1 terms,
    including exact immutable-input cancellation, rather than summing deltas.
    """
    nodes = {}
    for f in factors:
        refs = f['option_refs'] or f['reference_refs']
        nodes[f['id']] = c.get(c.get(ref(refs[0])).variable).node
    links = {f['id']: set() for f in factors}
    for a in factors:
        for b in factors:
            if a['id'] == b['id']: continue
            if any(a[k] != b[k] for k in ('effect_type','currency','time_basis','control')): continue
            # Only one side carries each scope. A shared variable is already a
            # user-level alternative; it must not swallow independent effects.
            opposed = ((a['selected_amount'] is None and b['reference_amount'] is None) or
                       (b['selected_amount'] is None and a['reference_amount'] is None))
            na, nb = nodes[a['id']], nodes[b['id']]
            if opposed and na.id != nb.id and (c.is_descendant(na,nb) or c.is_descendant(nb,na)):
                links[a['id']].add(b['id']);links[b['id']].add(a['id'])
    by_id={f['id']:f for f in factors};seen=set();out=[]
    for f in factors:
        if f['id'] in seen:continue
        pending=[f['id']];ids=set()
        while pending:
            key=pending.pop()
            if key not in ids:ids.add(key);pending.extend(links[key]-ids)
        seen.update(ids)
        if len(ids)==1:out.append(f);continue
        fs=[by_id[key] for key in sorted(ids)]
        parent=next((nodes[x['id']] for x in fs if all(nodes[y['id']]==nodes[x['id']] or c.is_descendant(nodes[y['id']],nodes[x['id']]) for y in fs)),None)
        if parent is None:out.extend(fs);continue
        selected={ref(r) for x in fs for r in x['option_refs']}
        references={ref(r) for x in fs for r in x['reference_refs']}
        same=lambda e:(e.effect_type,e.planning.currency.value,e.time_basis)==(f['effect_type'],f['currency'],f['time_basis'])
        new=[(r,e) for _,r,e in chosen if r in selected and same(e)]
        old=[(r,e) for _,r,e in original if r in references and same(e)]
        currencies={e.planning.currency for _,e in (*new,*old)}
        delta=bucket([(e.planning,1) for r,e in new if (r,e) not in old]+[(e.planning,-1) for r,e in old if (r,e) not in new],currencies)[0] if has_reference else None
        out.append(dict(f,id='choice/'+str(len(out)),event=c.get(parent).title,
            selected=list(dict.fromkeys(c.get(r).label for r,_ in new)),
            reference=list(dict.fromkeys(c.get(r).label for r,_ in old)),
            option_refs=[wire_ref(r) for r in sorted(selected,key=lambda r:r.id)],
            reference_refs=[wire_ref(r) for r in sorted(references,key=lambda r:r.id)],
            selected_amount=bucket(((e.planning,1) for _,e in new),currencies)[0] if new else None,
            reference_amount=bucket(((e.planning,1) for _,e in old),currencies)[0] if old else None,
            delta=delta,direction=direction(delta) if delta else 'no_reference',
            source_factor_ids=sorted(ids),structural_choice=True,
            assumptions=list(dict.fromkeys(a for x in fs for a in x['assumptions']))))
    return out

class FutureExplanation:
    def __init__(self,adoption):self.a=adoption;self.x=adoption.x

    def resolve(self,c,p):
        question=p.get('question','');scenario_names=[s for s in c.latest.values() if type(s) is SavedScenario and not s.meta.archived and mentions(s.name,question)]
        if len(scenario_names)==1:return scenario_names[0]
        if len(scenario_names)>1:return scenario_names
        if p.get('scenario'):
            s=c.get(ref(p['scenario']));require(type(s) is SavedScenario and not s.meta.archived,'future_request_invalid');return s
        if p.get('options'):
            return self.x._comparison(c,self.x.memory.read()[2],[ref(r) for r in p['roots']],[ref(r) for r in p['options']])
        # A named alternative is usable only when its retained target is unique.
        named=[o for o in c.latest.values() if type(o) is FutureOption and not o.meta.archived and mentions(o.label,question)]
        if p.get('node'):named=[o for o in named if c.is_descendant(c.get(o.variable).node,ref(p['node']))]
        if len(named)==1:
            o=named[0];n=c.get(c.get(o.variable).node)
            return self.x._comparison(c,self.x.memory.read()[2],[n.meta.ref],[o.meta.ref])
        scenarios=[s for s in c.latest.values() if type(s) is SavedScenario and not s.meta.archived]
        return scenarios[0] if len(scenarios)==1 else scenarios

    def facts(self,p):
        with self.x.memory.store._lock:
            return self._facts(p)

    def _facts(self,p):
        fields(p,'question language scenario roots options node model')
        require(p.get('language','en') in ('en','zh-CN'),'future_request_invalid')
        require(type(p.get('model',False)) is bool,'future_request_invalid')
        question=sentence(p.get('question',''),True)
        require(bool(p.get('roots'))==bool(p.get('options')),'future_request_invalid')
        if p.get('roots'):
            require(type(p['roots']) is list and type(p['options']) is list and len(p['roots'])<=64 and len(p['options'])<=128,'future_request_invalid')
        c,future_revision,snapshot=self.x.memory.read();target=self.resolve(c,p)
        if type(target) is list:
            return dict(stage='clarify' if target else 'empty',choices=[dict(ref=wire_ref(s.meta.ref),name=s.name) for s in target],financial_authority=False)
        if type(target) is SavedScenario:
            selection=target.selection;frame=c.get(target.reference) if target.reference else None
            row=next((r for r in self.a.space()['records'] if r['ref']==wire_ref(target.meta.ref)),None)
            state=validity(c,target,snapshot)
            result=row['result'] if row else self.x.memory.inspect(selection,target.reference)
            reasons=row['reasons'] if row else state['reasons'];changed=row['changed_items'] if row else []
            if state['status']=='stale':result=dict(status='stale',buckets=[])
            name=target.name;scenario_ref=wire_ref(target.meta.ref)
        else:
            selection,frame,result=target;c=FutureCatalog(c.scope,(*c.records,frame));reasons=result.get('reasons',[]);changed=[];name=None;scenario_ref=None
            states=[validity(c,c.get(r),snapshot) for r in (*c.consumed_structure(selection),*selection.options)]
            state={'missing':sorted({m for s in states for m in s['missing']}),'affected':[a for s in states for a in s['affected']]}
        # Pinned historical arithmetic is inspectable but never shown as current.
        historical=c.compile(selection,frame.meta.ref if frame else None)
        if result['status']!='stale':result=dict(result,reference_buckets=c.compile(frame.selection)['buckets'] if frame else [])
        _,chosen=c.check_selection(selection);_,original=c.check_selection(frame.selection) if frame else ((),())
        keys={(c.get(r).variable, e.effect_type,e.planning.currency,e.time_basis,e.slot) for _,r,e in (*chosen,*original)}
        factors=[]
        for variable,typ,currency,basis,slot in sorted(keys,key=lambda k:(k[2].value,k[1],k[3],k[0].id,k[4])):
            new=[(r,e) for _,r,e in chosen if c.get(r).variable==variable and (e.effect_type,e.planning.currency,e.time_basis,e.slot)==(typ,currency,basis,slot)]
            old=[(r,e) for _,r,e in original if c.get(r).variable==variable and (e.effect_type,e.planning.currency,e.time_basis,e.slot)==(typ,currency,basis,slot)]
            # Use exactly FS1's shared immutable-input cancellation and D2 bucket.
            terms=[(e.planning,1) for r,e in new if (r,e) not in old]+[(e.planning,-1) for r,e in old if (r,e) not in new]
            delta=bucket(terms,{currency})[0] if frame else None
            v=c.get(variable);n=c.get(v.node)
            factors.append(dict(id='factor/'+str(len(factors)),event=n.title,selected=[c.get(r).label for r,_ in new],reference=[c.get(r).label for r,_ in old],
                option_refs=[wire_ref(r) for r,_ in new],reference_refs=[wire_ref(r) for r,_ in old],control=v.control,
                effect_type=typ,currency=currency.value,time_basis=basis,
                selected_amount=bucket(((e.planning,1) for _,e in new),{currency})[0] if new else None,
                reference_amount=bucket(((e.planning,1) for _,e in old),{currency})[0] if old else None,
                delta=delta,direction=direction(delta) if delta else 'no_reference',
                current=result['status']!='stale',assumptions=list(dict.fromkeys(a for r,_ in new for a in c.get(r).assumptions))))
        visible_factors=choice_factors(c,factors,chosen,original,frame is not None)
        groups={}
        for f in visible_factors:groups.setdefault((f['effect_type'],f['currency'],f['time_basis']),[]).append(f)
        rankings=[]
        for (typ,currency,basis),fs in groups.items():
            changes=[f for f in fs if f['delta'] and f['direction']!='unchanged']
            if not changes:continue
            comparable=all(f['delta']['precision'] in ('exact_reported','approximate') for f in changes)
            maximum=max(abs(f['delta']['value_minor']) for f in changes) if comparable else None
            rankings.append(dict(effect_type=typ,currency=currency,time_basis=basis,comparable=comparable,
                largest=[f['id'] for f in changes if comparable and abs(f['delta']['value_minor'])==maximum]))
        affected=[]
        for item in state['affected']:
            source=c.get(ref({k:item[k] for k in ('kind','id','version')}))
            label=getattr(source,'title',None) or getattr(source,'label',None) or getattr(source,'name',None)
            affected.append(dict(label=label,reasons=item['reasons']))
        facts=dict(schema='finvis.explanation-facts.v0.1',scenario_ref=scenario_ref,name=name,
            world_version=dict(revision=snapshot.revision,world_ref={'id':snapshot.world.ref.id,'version':snapshot.world.ref.version}),
            future_revision=future_revision,selected=[wire_ref(r) for r in selection.options],reference=wire_ref(frame.meta.ref) if scenario_ref and frame else None,
            status=result['status'],impact=result if result['status']!='stale' else dict(status='stale',buckets=[]),
            historical_impact=historical if result['status']=='stale' else None,factors=factors,choice_factors=visible_factors,rankings=rankings,
            stale=dict(reasons=reasons,changed_items=sorted(set(changed)),affected=affected),
            known_unknowns=sorted(set(state['missing']+historical.get('missing',[])+(['scope_incomplete'] if not selection.scope_complete else [])+
                ['amount_unknown:'+f['id'] for f in factors if f['selected_amount'] and f['selected_amount']['precision']=='unknown'])),
            judgment=dict(status='unassessed',dimensions=['affordability','recommendation'],reason='approved_coverage_liquidity_policy_required'),financial_authority=False)
        facts['digest']=hashlib.sha256(json.dumps(facts,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return dict(stage='explained',facts=facts,intent=intent(question))

    def explain(self,p):
        out=self.facts(p)
        if out['stage']!='explained':return out
        zh=p.get('language','en')=='zh-CN';f=out['facts'];claims=[]
        def add(identity,text,kind='impact'):claims.append(dict(id=identity,text=text,kind=kind))
        if f['status']=='stale':
            add('stale','这个结果已过期，不能继续作为当前影响。原情景和早期估算仍保留。' if zh else 'This result is stale and cannot represent current impact. The original scenario and estimates are retained.','stale')
            for i,reason in enumerate(f['stale']['reasons'][:3]):
                words=REASONS.get(reason,('A relevant dependency changed.','一个相关依赖已变化。'))
                labels=' · '.join(f['stale']['changed_items'] or [a['label'] for a in f['stale']['affected'] if a['label']])
                add('reason/'+str(i),(labels+'：' if labels else '')+words[1 if zh else 0],'stale')
        else:
            for i,b in enumerate(f['impact'].get('buckets',[])):
                a=(b['known_selection_total'] or b['known_subtotal'] or [None])[0]
                if a is None:continue
                noun=('所选费用估算' if b['effect_type']=='cost' else '所选预期流入（并非现金）') if zh else ('Selected estimated outflow' if b['effect_type']=='cost' else 'Selected expected inflow (not cash)')
                partial=a['precision']=='unknown' or f['status']=='needs_information'
                if a['precision']=='unknown' and b['known_subtotal']:a=b['known_subtotal'][0]
                text=noun+'：'+money(a,zh) if zh else noun+': '+money(a,zh)
                if b['time_basis']!='one_time':text+=' · '+({'monthly':'每月','annual':'每年','yearly':'每年','weekly':'每周','daily':'每天'}.get(b['time_basis'],b['time_basis']) if zh else b['time_basis'])
                if partial:text+=('，仅为已知部分。' if zh else '; known portion only.')
                add('total/'+str(i),text)
            active=[x for x in f['choice_factors'] if x['delta'] and x['direction'] not in ('unchanged','unknown')]
            if out['intent']=='savings':
                active=[x for x in active if x['effect_type']=='cost' and x['direction']=='decrease']
                named=[x for x in active if mentions(x['event'],p.get('question','')) or any(mentions(label,p.get('question','')) for label in x['selected'])]
                if named:active=named
            if out['intent']=='largest':
                winners={i for row in f['rankings'] for i in row['largest']};active=[x for x in active if x['id'] in winners]
                if any(not row['comparable'] for row in f['rankings']):add('rank_unknown','存在未知或范围金额，不能确定所有因素的大小排序。' if zh else 'Unknown or range amounts prevent a definite ranking of all factors.','unknown')
            # Do not rank unlike currency/type/time groups against one another.
            active=sorted(active,key=lambda x:(x['currency'],x['effect_type'],x['time_basis'],0 if x['direction']=='increase' else 1))
            for x in active[:3]:
                label=x['event'] if x.get('structural_choice') else x['event']+' · '+' / '.join(x['selected'] or x['reference']);a=money(unsigned(x['delta']),zh)
                noun=('费用' if x['effect_type']=='cost' else '预期流入（并非现金）') if zh else ('estimated outflow' if x['effect_type']=='cost' else 'expected inflow (not cash)')
                verb=('增加' if x['direction']=='increase' else '减少' if x['direction']=='decrease' else '变化范围为') if zh else ('increases' if x['direction']=='increase' else 'reduces' if x['direction']=='decrease' else 'changes by')
                text=f'{label}：相比参考方案，{noun}{verb} {a}。' if zh else f'{label}: {verb} {noun} by {a} compared with the reference.'
                if x.get('structural_choice') and x['selected_amount'] and x['reference_amount']:
                    before,after=money(x['reference_amount'],zh),money(x['selected_amount'],zh)
                    text=(f"{label}：所选{noun} {after}，原估计 {before}，{verb} {a}。" if zh else
                          f"{label}: selected {noun} {after}, previously estimated {before}; {verb} by {a}.")
                if x['time_basis']!='one_time':text+=' · '+({'monthly':'每月','annual':'每年','yearly':'每年','weekly':'每周','daily':'每天'}.get(x['time_basis'],x['time_basis']) if zh else x['time_basis'])
                if out['intent']=='largest':text+=('这是同类／同货币／同时间口径中最大的估算差额。' if zh else ' This is the largest estimated difference within this type, currency and time basis.')
                add(x['id'],text,'offset' if x['effect_type']=='cost' and x['direction']=='decrease' else 'driver')
            if out['intent']=='savings' and not active:add('savings_unknown','当前范围内没有可核验的费用减少，不能编造节省金额。' if zh else 'No supported cost reduction is established in this scope; a saving cannot be invented.','unknown')
            if f['known_unknowns']:add('unknown','还有未确定的选择、金额或证据；已知部分不是完整结果。' if zh else 'Some choices, amounts or evidence remain unknown; the known portion is not a complete result.','unknown')
        add('unassessed','资金承受能力与购买建议尚未评估：必要支出覆盖／流动性规则尚不足以支持判断。' if zh else 'Affordability and purchase advice are unassessed: approved essential-outflow coverage/liquidity rules do not yet support a verdict.','unassessed')
        if out['intent']=='judgment':claims=[x for x in claims if x['id']=='unassessed']+[x for x in claims if x['id']!='unassessed']
        # Compact default; full factor/source evidence remains in Details.
        chosen=[x for x in claims if x['kind'] in ('driver','offset','stale','unknown')][:3]
        if not chosen:chosen=[x for x in claims if x['kind']=='impact'][:3]
        # Unsupported advice is prominent only when asked; the grounded claim
        # and full boundary remain inspectable for ordinary arithmetic Why.
        if out['intent']=='judgment':chosen=[next(x for x in claims if x['id']=='unassessed')]+chosen[:2]
        elif f['known_unknowns'] and not any(x['id']=='unknown' for x in chosen):
            chosen=chosen[:2]+[next(x for x in claims if x['id']=='unknown')]
        out.update(claims=claims,narration=[x['text'] for x in chosen],narration_ids=[x['id'] for x in chosen],model_state='not_requested')
        return out


def narration_schema(ids):
    return dict(type='object',additionalProperties=False,required=['paragraphs'],properties=dict(paragraphs=dict(type='array',minItems=1,maxItems=3,items=dict(type='object',additionalProperties=False,required=['sentence_ids'],properties=dict(sentence_ids=dict(type='array',minItems=1,maxItems=5,items=dict(type='string',enum=ids)))))))

def model_input(out):
    chosen={x['id']:x['text'] for x in out['claims'] if x['id'] in out['narration_ids']}
    return json.dumps(dict(task='Group these approved sentences into a concise explanation. Use every ID exactly once; preserve all limitations.',sentences=chosen),ensure_ascii=False)

def model_narration(out,raw):
    if type(raw) is str:
        require(len(raw.encode())<=16384,'explanation_model_invalid');raw=json.loads(raw)
    require(type(raw) is dict and set(raw)=={'paragraphs'} and type(raw['paragraphs']) is list and 1<=len(raw['paragraphs'])<=3,'explanation_model_invalid')
    ids=[];groups=[];allowed={x['id']:x['text'] for x in out['claims'] if x['id'] in out['narration_ids']}
    for p in raw['paragraphs']:
        require(type(p) is dict and set(p)=={'sentence_ids'} and type(p['sentence_ids']) is list and 1<=len(p['sentence_ids'])<=5,'explanation_model_invalid')
        require(all(type(i) is str and i in allowed for i in p['sentence_ids']),'explanation_model_invalid')
        ids+=p['sentence_ids'];groups.append(' '.join(allowed[i] for i in p['sentence_ids']))
    require(len(ids)==len(set(ids)) and set(ids)==set(allowed),'explanation_model_invalid')
    return groups
