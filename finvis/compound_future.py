"""Compound proposals compiled into existing FS1 records, never financial writes.

The caller supplies an already validated Meaning bundle and exact scoped Future
references. Preparing is an explicit action. Retention remains the existing
FutureExperience.confirm / IfInteraction.keep boundary. Pending work is local.
"""
from dataclasses import replace
from hashlib import sha256
import json
import re
from uuid import uuid4

from .decision_baseline import amount
from .future_experience import ref, wire_ref
from .future_space import (FutureCatalog, FutureNode, FutureVariable, FutureOption,
    FutureEffect, FutureRef, FutureError, MoneyTiming, require)
from .persistence_evidence import canonical
from .world_values import AmountEstimate, AmountPrecision
from .future_timing import parse_timing
from .compound_payments import payment_basis_for, payment_claim_identity
from .payment_amounts import payment_planning


FUTURE_KINDS = frozenset(('future_outflow', 'planned_outflow', 'expected_inflow'))
EVIDENCE_SCHEMA = 'finvis.compound-future-evidence.v1'


def _digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def _ambiguities(bundle):
    return {cid for a in bundle.get('ambiguities', ()) for cid in a['claim_ids']}


def capabilities(bundle):
    """Pure classification; no World read, draft, inference, save or consent."""
    blocked = _ambiguities(bundle)
    return dict(supported_claim_ids=[c['id'] for c in bundle['claims']
                    if c['kind'] in FUTURE_KINDS and c['id'] not in blocked],
        blocked=[dict(claim_id=c['id'], reason='material_clarification')
                 for c in bundle['claims'] if c['id'] in blocked],
        unsupported=[dict(claim_id=c['id'], reason='different_review_required')
                     for c in bundle['claims']
                     if c['kind'] not in FUTURE_KINDS and c['kind'] != 'correction'],
        financial_authority=False)


def _current(catalog, value):
    r = value if type(value) is FutureRef else ref(value)
    row = catalog.get(r)
    require(catalog.latest[(r.kind, r.id)] == row and not row.meta.archived,
            'future_version_changed')
    return row


def _target(catalog, value, context_refs):
    """Model strings can only name a caller-captured exact context entry."""
    require(type(value) is str and value in context_refs, 'compound_target_unresolved')
    row = _current(catalog, context_refs[value])
    if type(row) is FutureOption:
        variable = _current(catalog, row.variable)
        return catalog.get(variable.node), variable, row
    if type(row) is FutureVariable:
        return catalog.get(row.node), row, None
    require(type(row) is FutureNode, 'compound_target_unresolved')
    variables = [v for v in catalog.latest.values() if type(v) is FutureVariable
                 and v.node == row.meta.ref and not v.meta.archived]
    require(len(variables) == 1, 'compound_target_unresolved')
    return row, variables[0], None


def _money(claim):
    m = claim['money']
    require(m['currency'] in ('CNY', 'SGD', 'EUR'), 'compound_currency_unresolved')
    return amount(dict(currency=m['currency'], precision=m['precision'],
        value=m['value'], range_min=m['lower'], range_max=m['upper']))


def _timing(claim, source_text=None):
    t = claim.get('timing')
    if not t or not t.get('text'):
        return None
    from .compound_binding_support import timing_support
    require(t['text'] in claim['source'] or (source_text and timing_support(source_text, claim)), 'compound_timing_source')
    if t['precision'] in ('day', 'month'):
        # Only an explicit canonical literal is accepted as a computed date.
        # Other literal/relative descriptions stay unknown, with wording intact.
        if t['iso'] not in t['text']:
            try: parsed = parse_timing(t['text'])
            except (FutureError, ValueError): return MoneyTiming('unknown', None, t['text'])
            if parsed.precision != t['precision'] or parsed.value != t['iso']:
                return MoneyTiming('unknown', None, t['text'])
            return parsed
        return MoneyTiming(t['precision'], t['iso'], t['text'])
    # Normalize only an explicit absolute calendar literal. Relative wording
    # remains unknown here: do not infer an anchor from the execution date.
    from .calendar_literals import explicit_calendar
    named = explicit_calendar(t['text'])
    if named: return MoneyTiming(named[0], named[1], t['text'])
    return MoneyTiming('unknown', None, t['text'])


def _conditions(catalog, option):
    variable = catalog.get(option.variable)
    result = list(variable.active_when)
    node = catalog.get(variable.node)
    while node is not None:
        result.extend(node.active_when)
        node = catalog.get(node.parent) if node.parent else None
    return tuple(result)


def complete_selection(catalog, roots, options, *, automatic=(), managed=None):
    """Complete only specified single-option components whose conditions hold.

    Alternative variables remain unchosen. A missing antecedent never activates
    a fee. Explicitly chosen incompatible alternatives remain validation errors.
    """
    roots = tuple(r if type(r) is FutureRef else ref(r) for r in roots)
    picked = list(r if type(r) is FutureRef else ref(r) for r in options)
    automatic = tuple(r if type(r) is FutureRef else ref(r) for r in automatic)
    require(len(set(picked)) == len(picked), 'future_request_invalid')
    require(all(type(catalog.get(r)) is FutureOption for r in picked), 'future_request_invalid')
    # Duplicate variables are explicit conflicting choices, never last-one-wins.
    require(len({catalog.get(r).variable.id for r in picked}) == len(picked), 'exclusive_options')
    for _ in range(len(automatic) + 1):
        chosen = {catalog.get(r).variable.id:r for r in picked}
        kept = []
        for r in picked:
            conditions = _conditions(catalog, catalog.get(r))
            if ((managed is not None and r not in managed) or
                    all(chosen.get(catalog.get(c).variable.id) == c for c in conditions)):
                kept.append(r)
        picked = kept
        chosen = {catalog.get(r).variable.id:r for r in picked}
        added = False
        for r in automatic:
            o = catalog.get(r)
            if o.variable.id in chosen:
                continue
            if all(chosen.get(catalog.get(c).variable.id) == c for c in _conditions(catalog, o)):
                picked.append(r); chosen[o.variable.id] = r; added = True
        if not added:
            break
    selection = catalog.select(roots, tuple(picked))
    return dict(roots=[wire_ref(r) for r in selection.roots],
                options=[wire_ref(r) for r in selection.options])


def complete_conditionals(catalog, roots, options):
    """Complete already understood conditional costs within selected roots.

    Only this compiler's recorded sole-option components qualify. Ordinary
    independent unchecked events and arbitrary historical Future records never
    gain a selection. This is a transient selection adapter, not a calculation.
    """
    typed_roots = tuple(r if type(r) is FutureRef else ref(r) for r in roots)
    automatic = []; managed = set()
    for row in catalog.latest.values():
        if type(row) is not FutureOption or row.meta.archived or not _conditions(catalog, row):
            continue
        node = catalog.get(row.variable).node
        if not any(catalog.is_descendant(node, root) for root in typed_roots):
            continue
        for e in row.meta.evidence:
            if not e.payload: continue
            try: tag = json.loads(e.payload)
            except (TypeError, ValueError): continue
            if type(tag) is dict and tag.get('schema') == EVIDENCE_SCHEMA:
                managed.add(row.meta.ref)
                if wire_ref(row.meta.ref) in tag.get('result', {}).get('automatic_options', []):
                    automatic.append(row.meta.ref)
                break
    if not managed:
        return dict(roots=[wire_ref(r) for r in typed_roots],
                    options=[wire_ref(r) if type(r) is FutureRef else r for r in options])
    return complete_selection(catalog, typed_roots, options, automatic=automatic, managed=managed)


def select(interaction, proposal_id, claim_options, selected_claim_ids,
           *, roots=None, automatic_options=()):
    """Apply explicit alternative choices to an existing transient preview."""
    require(type(selected_claim_ids) in (list, tuple) and
            set(selected_claim_ids) <= set(claim_options), 'compound_choice_invalid')
    with interaction.x.memory.store._lock:
        c, _, _, _ = interaction._catalog(proposal_id)
        previous = interaction.selections.get(proposal_id, {})
        target_roots = roots if roots is not None else previous.get('roots', [])
        replacements = [ref(claim_options[cid]) for cid in selected_claim_ids]
        owners = {c.get(r).variable.id for r in replacements}
        prior = [ref(r) for r in previous.get('options', [])
                 if c.get(ref(r)).variable.id not in owners]
        selection = complete_selection(c, target_roots, (*prior, *replacements),
                                       automatic=automatic_options)
        return interaction._view(proposal_id, selection)


def plan(bundle, text, interaction, *, claim_ids=None, operation_id=None,
         roots=(), options=(), context_refs=None, proposal_id=None):
    """Explicitly prepare a typed Future proposal, with stable-token retries.

    A caller must reuse operation_id for retries of the same pending action.
    Without one each call is a new report; statement text is never deduplication.
    Whole-bundle retention uses confirm({proposal_id}), preserving all understood
    options. A selected-scenario keep deliberately has narrower existing scope.
    """
    require(type(text) is str and 0 < len(text) <= 20000, 'compound_source_required')
    require(type(bundle) is dict and 0 < len(bundle.get('claims', ())) <= 32,
            'compound_bundle_required')
    claims = {c['id']:c for c in bundle['claims']}
    require(len(claims) == len(bundle['claims']), 'compound_claim_identity')
    require(all(type(c.get('source')) is str and c['source'] and c['source'] in text
                for c in claims.values()), 'compound_source_required')
    requested = tuple(claim_ids) if claim_ids is not None else tuple(claims)
    require(len(set(requested)) == len(requested) and set(requested) <= set(claims),
            'compound_claim_identity')
    context_refs = context_refs or {}
    identity = operation_id or 'compound/future/' + uuid4().hex
    require(type(identity) is str and 0 < len(identity) <= 200, 'future_request_invalid')
    from .compound_premises import external_premise
    premises=[p for c in bundle['claims'] for p in [external_premise(c,text)] if p]
    require(type(roots) in (list,tuple) and type(options) in (list,tuple) and
            len(roots)<=64 and len(options)<=128, 'future_request_invalid')
    binding = dict(schema=EVIDENCE_SCHEMA, original_text=text, bundle=bundle,
                   claim_ids=list(requested), context_refs=context_refs,
                   roots=list(roots), options=list(options))
    if proposal_id is not None:
        require(type(proposal_id) is str and proposal_id and proposal_id != identity, 'future_request_invalid')
        binding['proposal_id']=proposal_id
    if premises: binding['external_premises']=premises
    signature = _digest(binding)
    with interaction.x.memory.store._lock:
        return _plan(bundle, text, interaction, requested, identity, context_refs,
                     roots, options, binding, signature, proposal_id)


def _plan(bundle, text, interaction, requested, identity, context_refs, roots,
          options, binding, signature, proposal_id):
    x = interaction.x
    catalog, _, _ = x.memory.read()
    found = x.memory.lookup(identity)
    existing = x.pending.get(identity)
    if found or existing:
        saved = ([catalog.get(ref(r)) for r in found['record_refs']] if found else existing.records)
        tags = [json.loads(e.payload) for row in saved for e in row.meta.evidence
                if e.identity == identity and e.payload and json.loads(e.payload).get('schema') == EVIDENCE_SCHEMA]
        # Recovery may acknowledge a pre-premise retained operation unchanged.
        # Match its exact former binding, never reinterpret or rewrite its rows.
        legacy_binding = dict(binding)
        legacy_binding.pop('external_premises', None)
        prior_signature = _digest(legacy_binding)
        require(tags and all(t.get('signature') in (signature, prior_signature) for t in tags), 'future_idempotency_conflict')
        meta = tags[0]['result']
        if found:
            return dict(meta, stage='retained', proposal_id=identity,
                        acknowledgement=found, space=x.listing(), financial_authority=False)
        view = interaction._view(identity, interaction.selections[identity])
        return dict(view, **meta)

    # Existing If layering validates the exact prior pending operation.
    # It is a catalog overlay, never a retention or financial mutation.
    base_op=None
    if proposal_id is not None:
        catalog, _, _, base_op = interaction._catalog(proposal_id)
    claims = {c['id']:c for c in bundle['claims']}
    blocked = {cid:'material_clarification' for cid in _ambiguities(bundle) if cid in requested}
    unsupported = {}
    candidates = {}
    targets = {}
    for cid in requested:
        claim = claims[cid]
        if cid in blocked:
            continue
        if claim['kind'] not in FUTURE_KINDS and claim['kind'] != 'correction':
            unsupported[cid] = 'different_review_required'; continue
        if claim.get('target'):
            try:
                targets[cid] = _target(catalog, claim['target'], context_refs)
            except FutureError:
                blocked[cid] = 'exact_future_target_required'; continue
        elif claim['kind'] == 'correction':
            links = [r['to'] for r in bundle.get('relationships', ())
                     if r['from'] == cid and r['kind'] == 'correction' and r['to'] in context_refs]
            if len(links) != 1:
                blocked[cid] = 'exact_future_target_required'; continue
            try: targets[cid] = _target(catalog, links[0], context_refs)
            except FutureError:
                blocked[cid] = 'exact_future_target_required'; continue
        candidates[cid] = claim

    # Revalidate typed payment meaning at compilation, before any relaxation of
    # the ordinary Future/Reality relationship boundary. Historical descriptors
    # supply arithmetic evidence; they never become new Future cost rows.
    # Recheck source premises at the compiler boundary as well as the ledger.
    from .compound_premises import external_premise, CONDITION, outcome_conflict, existing_selection_refund
    for cid, c in candidates.items():
        if c['kind']=='expected_inflow' and (outcome_conflict(c,text) or
                (CONDITION.search(c['source']) and not external_premise(c,text) and
                 not existing_selection_refund(bundle,c,text) and
                 not any(rel['kind']=='conditional_on' and rel['from']==cid for rel in bundle['relationships']))):
            blocked[cid]='external_condition_requires_review'
    payment_bases = {}
    for cid, claim in candidates.items():
        try:
            payment_bases[cid] = payment_basis_for(bundle, cid, text)
            if payment_bases[cid] is not None:
                payment_planning(_money(claim), payment_bases[cid])
        except (FutureError, ValueError, TypeError, KeyError):
            blocked[cid] = 'payment_basis_requires_review'
            payment_bases[cid] = None

    def historical_replacement(future_id, paid_id):
        basis = payment_bases.get(future_id)
        paid = claims.get(paid_id)
        return (basis is not None and paid is not None and
                paid['kind'] == 'actual_expense' and
                any(p.role == 'paid' and p.identity == payment_claim_identity(paid,bundle.get('payment_scope',''))
                    for p in basis.parts))

    relationships = bundle.get('relationships', ())
    parent = {}
    conditional = {}
    grouped = {cid:cid for cid in candidates}
    def owner(cid):
        while grouped[cid] != cid:
            cid = grouped[cid]
        return cid
    for rel in relationships:
        a, b, kind = rel['from'], rel['to'], rel['kind']
        # A requested subset cannot flatten either endpoint of an exclusive
        # relation into a new independent event. Retained claims need an exact
        # existing-target continuation; absent endpoints stay locally blocked.
        if kind in ('alternative', 'replacement') and a not in candidates and b in candidates:
            blocked[b] = 'alternative_requires_review'
        # An omitted/retained component still makes this a structural parent.
        # Do not turn it into a fresh standalone amount after partial retention.
        if kind == 'part_of' and a not in candidates and b in candidates:
            blocked[b] = 'part_requires_review'
        if a not in candidates:
            continue
        if kind == 'part_of':
            if b in candidates:
                if a in parent and parent[a] != b:
                    blocked[a] = 'containment_ambiguous'
                parent[a] = b
            else:
                blocked[a] = 'parent_requires_review'
        elif kind == 'conditional_on':
            if re.search(r'\b(?:not|unless|without|never)\b|不|没|除非', rel['source'], re.I):
                blocked[a] = 'condition_polarity_requires_review'
            elif b in candidates or b in context_refs:
                conditional.setdefault(a, []).append(b)
            else:
                blocked[a] = 'condition_requires_review'
        elif kind in ('alternative', 'replacement'):
            if b in candidates:
                grouped[owner(a)] = owner(b)
            elif b in context_refs:
                try: targets[a] = _target(catalog, b, context_refs)
                except FutureError: blocked[a] = 'exact_future_target_required'
            elif kind == 'replacement' and historical_replacement(a, b):
                # A proposed replacement of an already-paid item is not an
                # exclusive choice against past Reality. Its independently
                # validated basis determines only the prospective cash effect.
                pass
            else:
                blocked[a] = 'alternative_requires_review'
        elif kind in ('correction', 'reference') and claims[a]['kind'] == 'correction' and a not in targets:
            blocked[a] = 'exact_future_target_required'
    # Parent observations with a total cannot be added to their own parts. A
    # structural unknown parent remains understood without becoming zero.
    structural = set(parent.values())
    for cid in structural:
        if claims[cid]['money']['precision'] != 'unknown' or cid in targets:
            blocked[cid] = 'total_and_parts_require_review'
    for cid in conditional:
        if cid in targets:
            blocked[cid] = 'conditional_existing_target_requires_review'
    for cid, target in targets.items():
        # A common exact existing variable grounds an alternative group; no
        # second independent event or first/last choice is inferred for it.
        for other, peer in targets.items():
            if target[1].meta.ref == peer[1].meta.ref:
                grouped[owner(cid)] = owner(other)
    for cid in candidates:
        try:
            if cid not in structural:
                _money(claims[cid]); _timing(claims[cid], text)
        except (FutureError, ValueError, TypeError, KeyError):
            blocked[cid] = 'amount_currency_or_timing_requires_review'
    for cid, (n, v, old) in targets.items():
        if cid in blocked: continue
        old = old or (catalog.get(v.reference_option) if v.reference_option else None)
        if old is None or len(old.effects) != 1:
            blocked[cid] = 'exact_future_effect_required'; continue
        effect = old.effects[0]
        if effect.payment_basis is not None and payment_bases.get(cid) is None:
            blocked[cid] = 'payment_basis_requires_review'; continue
        typ = effect.effect_type if claims[cid]['kind'] == 'correction' else (
            'expected_inflow' if claims[cid]['kind'] == 'expected_inflow' else 'cost')
        if effect.effect_type != typ or effect.observed.currency != _money(claims[cid]).currency:
            blocked[cid] = 'target_direction_currency_requires_review'
    all_groups = {}
    for cid in candidates: all_groups.setdefault(owner(cid), []).append(cid)
    for members in all_groups.values():
        directions = {('expected_inflow' if claims[cid]['kind'] == 'expected_inflow' else 'cost')
                      for cid in members if claims[cid]['kind'] != 'correction'}
        if len(directions) > 1:
            for cid in members: blocked[cid] = 'alternative_direction_requires_review'
        if len({parent.get(cid) for cid in members}) != 1 or len({tuple(conditional.get(cid, ())) for cid in members}) != 1:
            for cid in members: blocked[cid] = 'alternative_structure_requires_review'
        if len(members) > 1 and any(cid in structural for cid in members):
            for cid in members: blocked[cid] = 'alternative_structure_requires_review'
    # Detect proposal-local dependency cycles before invoking the FS1 catalog,
    # so an unrelated supported component can still be prepared.
    edges = {cid:tuple(q for q in (parent.get(cid), *conditional.get(cid, ()))
                      if q in candidates) for cid in candidates}
    for start in candidates:
        pending = list(edges[start]); seen = set()
        while pending:
            cid = pending.pop()
            if cid == start:
                blocked[start] = 'dependency_cycle_requires_review'; break
            if cid in seen: continue
            seen.add(cid); pending.extend(edges[cid])
    for cid, antecedents in conditional.items():
        if any(q in candidates and owner(q) == owner(cid) for q in antecedents):
            blocked[cid] = 'dependency_cycle_requires_review'
    # Conditions and structural dependencies cannot escape a blocked unit.
    for _ in range(len(candidates) + 1):
        before = dict(blocked)
        for cid in candidates:
            if parent.get(cid) in blocked or any(q in blocked for q in conditional.get(cid, ())):
                blocked[cid] = 'dependency_requires_review'
        groups = {}
        for cid in candidates: groups.setdefault(owner(cid), []).append(cid)
        for members in groups.values():
            if any(cid in blocked for cid in members):
                for cid in members: blocked.setdefault(cid, 'alternative_requires_review')
        if before == blocked: break
    good = {cid:c for cid,c in candidates.items() if cid not in blocked}
    result = dict(supported_claim_ids=list(good),
        unsupported=[dict(claim_id=k, reason=v) for k,v in unsupported.items()],
        blocked=[dict(claim_id=k, reason=v) for k,v in blocked.items()],
        claim_options={}, automatic_options=[], financial_authority=False)
    if not good:
        return dict(result, stage='blocked')

    groups = {}
    for cid in good: groups.setdefault(owner(cid), []).append(cid)
    rows = []
    nodes = {}; variables = {}; option_refs = {}; amounts = {}
    def meta(kind, cid):
        claim = good[cid]
        m = x.meta(kind, claim['source'][:1000])
        return m
    # Allocate every identity before linking conditions; FS1 validates cycles.
    for key, members in groups.items():
        exact = [targets[cid] for cid in members if cid in targets]
        if exact:
            require(all(t[1].meta.ref == exact[0][1].meta.ref for t in exact), 'compound_target_conflict')
            n, v, _ = exact[0]
            for cid in members:
                nodes[cid] = n; variables[cid] = v
                targets.setdefault(cid, exact[0])
        else:
            cid = members[0]
            require(not (len(members) > 1 and any(m in structural for m in members)), 'compound_structure_ambiguous')
            title = ' / '.join(good[m]['label'] for m in members)[:1000]
            n = FutureNode(meta('node', cid), title, 'event', uuid4().hex)
            for member in members: nodes[member] = n
            if cid not in structural:
                control = 'external_outcome' if good[cid]['kind'] == 'expected_inflow' else 'user_choice'
                v = FutureVariable(meta('variable', cid), n.meta.ref, title, control)
                for member in members: variables[member] = v
        for cid in members:
            if cid not in structural:
                option_refs[cid] = meta('option', cid)
                amounts[cid] = _money(good[cid])

    def option_for(endpoint):
        if endpoint in option_refs: return option_refs[endpoint].ref
        require(endpoint in context_refs, 'compound_condition_unresolved')
        row = _current(catalog, context_refs[endpoint])
        require(type(row) is FutureOption, 'compound_condition_unresolved')
        return row.meta.ref

    emitted_nodes = set(); emitted_variables = set()
    automatic = []
    for key, members in groups.items():
        cid = members[0]; n = nodes[cid]
        if not any(m in targets for m in members):
            parents = {parent.get(m) for m in members}
            require(len(parents) == 1, 'compound_structure_ambiguous')
            p = next(iter(parents))
            condition_sets = {tuple(conditional.get(m, ())) for m in members}
            require(len(condition_sets) == 1, 'compound_alternative_condition_ambiguous')
            conditions = tuple(option_for(q) for q in next(iter(condition_sets)))
            n = replace(n, node_kind='component' if p else 'event',
                        parent=nodes[p].meta.ref if p else None, active_when=conditions)
            for m in members: nodes[m] = n
            if n.meta.ref not in emitted_nodes:
                rows.append(n); emitted_nodes.add(n.meta.ref)
        if cid in structural: continue
        v = variables[cid]
        if not any(m in targets for m in members) and v.meta.ref not in emitted_variables:
            # One unambiguous observation can be its own reference; multiple
            # alternatives have no inferred reference or inferred preference.
            v = replace(v, reference_option=option_refs[cid].ref if len(members) == 1 else None)
            rows.append(v); emitted_variables.add(v.meta.ref)
        for member in members:
            claim = good[member]; a = amounts[member]
            typ = 'expected_inflow' if claim['kind'] == 'expected_inflow' else 'cost'
            if member in targets:
                target_node, target_var, old = targets[member]
                old = old or (catalog.get(target_var.reference_option) if target_var.reference_option else None)
                require(old is not None and len(old.effects) == 1, 'compound_target_effect_requires_review')
                basis = old.effects[0]
                if claim['kind'] == 'correction': typ = basis.effect_type
                require(typ == basis.effect_type and a.currency == basis.observed.currency,
                        'compound_target_direction_currency_conflict')
                slot, coverage, time_basis = basis.slot, basis.coverage, basis.time_basis
                timing = _timing(claim, text) or basis.money_timing
            else:
                slot, coverage, time_basis = n.meta.ref.id, (n.meta.ref.id,), 'one_time'
                timing = _timing(claim, text)
            payment_basis = payment_bases.get(member)
            planning = (payment_planning(a, payment_basis) if payment_basis is not None else
                        AmountEstimate.approximate(a.amount) if a.precision is AmountPrecision.EXACT_REPORTED else a)
            effect = FutureEffect(slot, typ, a, planning, time_basis, coverage,
                                  money_timing=timing, payment_basis=payment_basis)
            option = FutureOption(option_refs[member], v.meta.ref, claim['label'],
                                  (effect,), (claim['source'][:1000],))
            rows.append(option)
            result['claim_options'][member] = wire_ref(option.meta.ref)
            if len(members) == 1: automatic.append(option.meta.ref)

    result['automatic_options'] = [wire_ref(r) for r in automatic]
    evidence = canonical(dict(schema=EVIDENCE_SCHEMA, signature=signature,
                              result=result, source=binding))
    # One existing evidence payload carries the complete source/bundle; all
    # records retain their own exact source excerpt and the same operation tag.
    for index, row in enumerate(rows):
        payload = evidence if index == 0 else canonical(dict(schema=EVIDENCE_SCHEMA,
                     signature=signature, result=result, source_ref=identity))
        rows[index] = replace(row, meta=replace(row.meta,
            evidence=tuple(replace(e, identity=identity, payload=payload) for e in row.meta.evidence)))
    augmented = FutureCatalog(catalog.scope, (*catalog.records, *rows))
    selected_roots = [ref(r) for r in roots]
    for n in nodes.values():
        while n.parent: n = augmented.get(n.parent)
        if n.meta.ref not in selected_roots: selected_roots.append(n.meta.ref)
    # Explicit corrections replace only this variable's transient selection.
    old_options = [ref(r) for r in options]
    correction_owners = {variables[cid].meta.ref.id for cid in good if good[cid]['kind'] == 'correction'}
    old_options = [r for r in old_options if augmented.get(r).variable.id not in correction_owners]
    selection = complete_selection(augmented, selected_roots, old_options, automatic=automatic)
    require(len(x.pending) < 256, 'future_draft_capacity')
    operation = x.memory.prepare(identity, (*base_op.records,*rows) if base_op else rows, 'keep')
    x.pending[identity] = operation
    interaction.parents[identity]=proposal_id
    try:
        view = interaction._view(identity, selection)
        return dict(view, **result)
    except Exception:
        x.pending.pop(identity, None)
        interaction.selections.pop(identity, None)
        interaction.parents.pop(identity, None)
        raise
