"""Application-only S4 adapter: targeted answers -> existing I3/G payload.

No financial mapping, provider call, authorization or mutation occurs here.
Answers are bound by I3 to its immutable I2 session and rechecked on each call.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from .review_preparation import prepare_review, PreparationStatus, _spans
from .compound_evidence import compound_binding, explicit_partition
from dataclasses import replace
from .user_first_review import understood_changes


@dataclass(frozen=True)
class InteractionReview:
    payload: dict
    questions: tuple
    blockers: tuple
    evidence: dict
    answer_keys: tuple


def wire_ref(ref):
    return {'kind': ref.record_kind, 'id': ref.ref.id, 'version': ref.ref.version}


def _advice_only_request(value, interpretation):
    """Only a separate read-only verdict may enter the explicit G subset path.

    Free-form unsupported actions are not proof of independence. This check does
    not grant an Engine capability or decide whether the proposed facts are true.
    """
    if 'evaluate_decision' not in interpretation.user_intents:
        return False
    request = value.strip().lower()
    if request in ('evaluate_decision', 'financial_verdict'):
        return True
    if re.search(r'\b(?:replace|delete|clear|remove|transfer|move|post|settle|buy|sell|execute|update|record)\b|替换|清空|删除|转账|转移|入账|结算|执行|更新|记录', request):
        return False
    question = (interpretation.user_question or '').strip().lower()
    return bool((question and request == question) or
                re.search(r'financial (?:assessment|advice|verdict|judg(?:e|ment))|affordab|financially (?:reasonable|safe)|财务(?:判断|评估|建议|上是否合理)|是否买得起|值不值得|能否负担', request))


def _conditional_update_scope(text):
    # A verdict that controls whether observations should be recorded is not an
    # independent request, even when its Candidate code is evaluate_decision.
    return bool(re.search(
        r'\b(?:only|just)\s+(?:record|update|add|apply|replace)\b[^.!?\n]*\bif\b|'
        r'\b(?:if|unless)\b[^.!?\n]*\b(?:record|update|add|apply|replace)\b|'
        r'\b(?:provided that|on condition that)\b|只有[^。！？\n]*才|除非|如果[^。！？\n]*才|若[^。！？\n]*才',
        text, re.I))


def prepare_interaction(candidate, text, read, answers=None):
    answers = answers or {}
    if type(answers) is not dict:
        raise ValueError('Answers must be a map')
    prepared = prepare_review(candidate, text, read)
    interpretation = candidate.interpretation
    prepared = replace(prepared, facts=tuple(compound_binding(next(f for f in interpretation.candidate_facts if f.id==item.fact_id), interpretation, text, item) for item in prepared.facts))
    if any(item.reason=='unique_compound_source_binding' for item in prepared.facts):
        prepared=replace(prepared,rule_version=prepared.rule_version+'+compound-1')
    facts = sorted(interpretation.candidate_facts, key=lambda row: row.id)
    by_id = {row.fact_id: row for row in prepared.facts}
    questions, blockers, keys, origins = [], [], [], []
    spans = [{'start': start, 'end': end, 'text': excerpt}
             for start, end, excerpt in _spans(text)]
    if len(spans) > 1:
        spans.append({'start': 0, 'end': len(text), 'text': text})
    snapshot = read.snapshot
    members = tuple(snapshot.world.members)
    payload = {'facts': [], 'accounts': [], 'positions': [],
               'coverage_note': '', 'uncertainties': [], 'mode': 'merge',
               'replacement': None, 'occurrence': None}

    def ask(key, kind, label, **details):
        keys.append(key)
        if key not in answers:
            if not any(row['id'] == key for row in questions):
                questions.append(dict(id=key, kind=kind, label=label, **details))
            return None
        origins.append({'field': key, 'origin': 'user_answered', 'answer': answers[key]})
        return answers[key]

    def option(key, kind, label, choices, multiple=False):
        answer = ask(key, kind, label, options=choices, multiple=multiple)
        if answer is None:
            return None
        valid = [item['value'] for item in choices]
        if multiple:
            if type(answer) is not list or not answer or any(item not in valid for item in answer) or len(set(answer)) != len(answer):
                raise ValueError('Invalid choices')
        elif type(answer) not in (str, int) or answer not in valid:
            raise ValueError('Invalid choice')
        return answer

    def source_for(fact):
        item = by_id[fact.id]
        if item.extraction_verified and item.sources:
            source = item.sources[0]
            origins.append({'field': 'source:' + fact.id, 'origin': 'source_verified',
                            'rule': prepared.rule_version, 'sha256': item.excerpt_sha256})
            return {'start': source.start, 'end': source.end}
        selected = option('source:' + fact.id, 'source', fact.label,
                          [{'label': part['text'], 'value': index} for index, part in enumerate(spans)])
        return {key: spans[selected][key] for key in ('start', 'end')} if selected is not None else None

    if interpretation.conflicts or interpretation.assumptions:
        blockers.append('revise_conflicting_interpretation')
    if any(item.reason in ('conditional_or_other_owner', 'negated_or_unresolved_timing',
                           'conflicting_temporal_evidence') for item in prepared.facts):
        blockers.append('revise_conflicting_interpretation')
    coupled_advice = _conditional_update_scope(text) and bool(interpretation.unsupported_requests)
    unsupported = [{'kind': 'request', 'label': value,
                    'reason': 'conditional_verdict_scope' if coupled_advice and _advice_only_request(value, interpretation)
                    else 'financial_verdict_unavailable' if _advice_only_request(value, interpretation)
                    else 'unsupported_operation'} for value in interpretation.unsupported_requests]
    unsupported += [{'kind': 'fact', 'label': next(f.label for f in facts if f.id == item.fact_id),
                     'reason': 'unsupported_currency' if item.reason == 'unsupported_currency'
                     else 'unsupported_fact'} for item in prepared.facts
                    if item.status is PreparationStatus.UNSUPPORTED]
    advice_notice = 'evaluate_decision' in interpretation.user_intents
    independent = bool(interpretation.unsupported_requests) and all(
        _advice_only_request(value, interpretation) for value in interpretation.unsupported_requests)
    independent = independent and not coupled_advice and bool(facts) and not any(
        fact.type in ('actual_expense', 'actual_inflow') for fact in facts) and not any(
        item.status is PreparationStatus.UNSUPPORTED for item in prepared.facts)
    evidence_base = {'preparation': _proof(prepared), 'unsupported': unsupported,
                     'understood': [{'id': fact.id, 'label': fact.label, 'type': fact.type}
                                    for fact in facts], 'advice_notice': advice_notice}
    if unsupported and not independent:
        blockers.append('revise_unsupported_interpretation')
    if not facts:
        blockers.append('no_supported_update')
    actual = [f for f in facts if f.type in ('actual_expense', 'actual_inflow')]
    if actual and len(facts) != 1:
        blockers.append('separate_actual_occurrence')
    if actual and (actual[0].account is None or interpretation.destructive_actions or
                   'replace_financial_state' in interpretation.user_intents):
        blockers.append('revise_actual_account_or_scope')
    if blockers:
        return InteractionReview(payload, (), tuple(dict.fromkeys(blockers)),
                                 dict(evidence_base, origins=[]), ())

    destructive = bool(interpretation.destructive_actions) or 'replace_financial_state' in interpretation.user_intents
    if independent and not destructive:
        answer = 'independent_merge'
        origins.append({'field':'independent_scope','origin':'deterministically_prepared','rule':'separate-read-only-verdict'})
        if answer is not None:
            payload['mode'] = answer
    if destructive:
        accounts = [{'label': snapshot.get(ref).label, 'value': wire_ref(ref)}
                    for ref in members if ref.record_kind == 'account']
        required_currencies = sorted({f.amount.currency or interpretation.default_currency for f in facts
                                      if f.type in ('current_asset', 'earmarked_current_asset') and f.amount})
        answer = ({'mode':'independent_merge','account_refs':[],'currencies':[]} if not members else ask('scope', 'scope', '', accounts=accounts,
                     required_currencies=required_currencies,
                     currencies=sorted({f.amount.currency or interpretation.default_currency
                                        for f in facts if f.amount} |
                                       {snapshot.get(ref).currency.value for ref in members
                                        if ref.record_kind == 'asset_position'})))
        if answer is not None:
            if type(answer) is not dict or set(answer) != {'mode', 'account_refs', 'currencies'}:
                raise ValueError('Exact scope answer required')
            if answer['mode'] == 'independent_merge' and not answer['account_refs'] and not answer['currencies']:
                payload['mode'] = 'independent_merge'
            elif answer['mode'] == 'replace' and independent:
                raise ValueError('A request left out of this Candidate cannot authorize replacement')
            elif answer['mode'] == 'replace' and answer['account_refs'] and answer['currencies']:
                if (not set(required_currencies) <= set(answer['currencies']) or
                        any(ref not in [item['value'] for item in accounts] for ref in answer['account_refs'])):
                    raise ValueError('Scope must include proposed position currencies and exact current accounts')
                payload['mode'] = 'replace'
                payload['replacement'] = {key: answer[key] for key in ('account_refs', 'currencies')}
            else:
                raise ValueError('Choose exact replacement or explicit add-only')

    if interpretation.uncertainties:
        choices = [{'label': value, 'value': value} for value in interpretation.uncertainties]
        answer = option('uncertainties', 'uncertainties', '', choices, True)
        if answer is not None:
            if set(answer) != set(interpretation.uncertainties):
                raise ValueError('Review all retained uncertainties')
            payload['uncertainties'] = answer

    sources = {fact.id: source_for(fact) for fact in facts}
    if actual:
        fact = actual[0]
        positions = [{'label': snapshot.get(snapshot.get(ref).account_ref).label + ' · ' + snapshot.get(ref).currency.value,
                      'value': wire_ref(ref)} for ref in members if ref.record_kind == 'asset_position']
        if not positions:
            blockers.append('record_balance_before_settlement')
        else:
            answer = ask('settlement', 'settlement', fact.label, positions=positions, excerpts=spans)
            if answer is not None:
                required = {'account_source', 'position_ref', 'basis', 'coverage_source',
                            'coverage_rationale', 'freshness_current'}
                if type(answer) is not dict or set(answer) != required:
                    raise ValueError('Complete settlement review required')
                if not all(answer.values()) or answer['freshness_current'] is not True:
                    raise ValueError('Unresolved settlement cannot proceed')
                payload['occurrence'] = dict(answer, fact_id=fact.id, source=sources[fact.id])
    else:
        for fact in facts:
            row = dict(id=fact.id, source=sources[fact.id], earmark_role='other', emergency_use=False,
                       decision_ids=[], funding_fact_id=None, expected_ref=None, correction_reason=None)
            if fact.type == 'context':
                choices = [{'label': item.label, 'value': item.id} for item in facts if item.type == 'contemplated_decision']
                if not choices:
                    blockers.append('context_needs_decision')
                else:
                    answer = option('decision:' + fact.id, 'decision', fact.label, choices, True)
                    if answer is not None:
                        row['decision_ids'] = answer
            if fact.type == 'earmarked_current_asset' and re.search(
                    r'emergency|紧急|应急|备用金', ' '.join(filter(None, (fact.label, fact.earmark, fact.purpose))), re.I):
                answer = option('role:' + fact.id, 'role', fact.label,
                                [{'label': key, 'value': key} for key in ('other', 'purchase', 'emergency')])
                if answer is not None:
                    row.update(earmark_role=answer, emergency_use=answer == 'emergency')
            monthly_existing = fact.type == 'monthly_essential_outflow' and any(
                ref.record_kind == 'monthly_essential_outflow' and snapshot.get(ref).amount.currency.value == (fact.amount.currency or interpretation.default_currency)
                for ref in members)
            if monthly_existing or ('correct_information' in interpretation.user_intents and fact.type not in ('current_asset', 'earmarked_current_asset')):
                kind = {'expected_future_inflow': 'future_inflow', 'planned_expense': 'planned_expense',
                        'monthly_essential_outflow': 'monthly_essential_outflow',
                        'contemplated_decision': 'world_decision', 'context': 'decision_context'}[fact.type]
                options = [{'label': getattr(snapshot.get(ref), 'purpose', None) or getattr(snapshot.get(ref), 'subject', None)
                            or getattr(snapshot.get(ref), 'statement', None) or getattr(snapshot.get(ref), 'label', None) or ref.ref.id,
                            'value': wire_ref(ref),
                            'currency': snapshot.get(ref).amount.currency.value if kind == 'monthly_essential_outflow' else None}
                           for ref in members if ref.record_kind == kind]
                answer = ask('correction:' + fact.id, 'correction', fact.label, targets=options)
                if answer is not None:
                    _correction(answer, options)
                    row.update(expected_ref=answer['target'], correction_reason=answer['reason'])
            payload['facts'].append(row)

        groups = {}
        for fact in facts:
            if fact.type in ('current_asset', 'earmarked_current_asset'):
                if not fact.account or fact.amount is None:
                    blockers.append('revise_missing_account_or_amount')
                    continue
                groups.setdefault((fact.account, fact.amount.currency or interpretation.default_currency), []).append(fact)
        for index, ((label, currency), group) in enumerate(sorted(groups.items())):
            key = 'account:' + label
            balance = [f.id for f in group if f.type == 'current_asset']
            allocations = [f.id for f in group if f.type == 'earmarked_current_asset']
            composition = 'total'
            if len(balance) != 1:
                partition = explicit_partition(group, text) and all(by_id[f.id].extraction_verified for f in group)
                answer = 'disjoint' if partition else option('composition:' + str(index), 'composition', label + ' · ' + currency,
                                [{'label': 'disjoint', 'value': 'disjoint'}])
                composition = answer or 'total'
                balance = balance or allocations
            # The account relationship uses exactly the reviewed fact evidence.
            source = next((sources[f.id] for f in group if sources[f.id] and
                           label in text[sources[f.id]['start']:sources[f.id]['end']]), None)
            if source is None and all(sources[f.id] for f in group):
                blockers.append('revise_account_evidence')
            existing = None
            candidates = [ref for ref in members if ref.record_kind == 'account' and snapshot.get(ref).label == label]
            all_accounts = [ref for ref in members if ref.record_kind == 'account']
            if all_accounts:
                choices = [{'label': snapshot.get(ref).label, 'value': str(i)} for i, ref in enumerate(candidates)]
                if not candidates:
                    choices.append({'label': 'new_account', 'value': 'new'})
                answer = option('account:' + label, 'account', label, choices)
                if answer is not None and answer != 'new':
                    existing = wire_ref(candidates[int(answer)])
            account = dict(key=key, label=label, source=source, existing_ref=existing)
            if not any(item['key'] == key for item in payload['accounts']):
                payload['accounts'].append(account)
            position = dict(key='position:' + str(index), account_key=key, balance_ids=balance,
                            allocation_ids=allocations, composition=composition, expected_ref=None, correction_reason=None)
            existing_positions = [ref for ref in members if ref.record_kind == 'asset_position'
                                  and wire_ref(snapshot.get(ref).account_ref) == existing
                                  and snapshot.get(ref).currency.value == currency]
            if existing_positions and payload['mode'] != 'replace':
                choices = [{'label': label + ' · ' + currency, 'value': wire_ref(ref)} for ref in existing_positions]
                answer = ask('position:' + str(index), 'correction', label + ' · ' + currency, targets=choices)
                if answer is not None:
                    _correction(answer, choices)
                    position.update(expected_ref=answer['target'], correction_reason=answer['reason'])
            payload['positions'].append(position)
        if groups:
            payload['coverage_note'] = ('S4 source-bound reported observations only: ' +
                '; '.join(label + ' / ' + currency for label, currency in sorted(groups)) +
                '. No completeness, unreported asset, outflow, access or freshness assertion.')
            origins.append({'field': 'coverage_note', 'origin': 'source_verified', 'rule': 'reported-subset-only-v1'})
        if payload['mode']=='replace' and not groups:
            coverage=ask('coverage','coverage','')
            if coverage is not None:
                if type(coverage) is not str or not coverage.strip():
                    raise ValueError('Explicit replacement coverage required')
                payload['coverage_note']=coverage
        if 'correct_information' in interpretation.user_intents and not any(
                item['expected_ref'] for item in payload['facts'] + payload['positions']) and not questions:
            blockers.append('revise_missing_correction_target')

    if set(answers) - set(keys):
        raise ValueError('Answer does not belong to this preparation')
    evidence = dict(evidence_base, origins=origins,
                    fact_preview=understood_changes(candidate,payload,questions),
                    retained_unknowns=list(interpretation.clarification_questions),
                    answers_sha256=hashlib.sha256(json.dumps(answers, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    return InteractionReview(payload, tuple(questions), tuple(dict.fromkeys(blockers)), evidence, tuple(keys))


def _correction(answer, options):
    if (type(answer) is not dict or set(answer) != {'target', 'reason'} or
            answer['target'] not in [item['value'] for item in options] or
            type(answer['reason']) is not str or not answer['reason'].strip()):
        raise ValueError('Exact correction target and reason required')


def _proof(prepared):
    return {'candidate_id': prepared.candidate_id, 'rule_version': prepared.rule_version,
            'source_sha256': prepared.source_sha256,
            'facts': [{'id': item.fact_id, 'status': item.status.value, 'reason': item.reason,
                       'source': {'start': item.sources[0].start, 'end': item.sources[0].end} if item.sources else None,
                       'excerpt': item.excerpt, 'sha256': item.excerpt_sha256,
                       'extraction_verified': item.extraction_verified} for item in prepared.facts]}
