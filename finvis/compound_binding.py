"""Current bounded semantic binder; v1.1 module stays immutable for frozen replay.

Wire schema, model prompt and financial authority are unchanged. This version
evolves source support and repairs a false range-based eligibility shortcut. Never use parser acceptance as semantic proof.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
import json
import re
from .agent_providers import ProviderError
from .compound_meaning import (
    CompoundMeaningError, MAX_TEXT, ACTUAL_KINDS, VERSION, SYSTEM_PROMPT,
    bundle_schema, bundle_prompt, needs_compound as legacy_needs_compound, destructive_scope,
    _source, _target_label, _contains, _decimal, _RANGE,
    _APPROX, _default_currency, _CURRENCY, _shape, _json_pairs,
    _context, _error, _currencies, _RELATION_EVIDENCE as _LEGACY_RELATIONS, _ROLE_EVIDENCE,
    _REQUEST_EVIDENCE, _ACTUAL, _HYPOTHETICAL, _FUTURE, _NEGATED_ACTUAL)
from .compound_binding_support import (timing_support, refund_report,
    plan_support, request_cue, evidence_ledger, answer_supports_resolution,
    price_question, prospective_price)

from .calendar_literals import (money_numbers as _number_evidence,
    explicit_calendar, monetary_precision_source)

# Copy, never mutate the frozen v1.1 parser's relation vocabulary.
_RELATION_EVIDENCE = dict(_LEGACY_RELATIONS)
_RELATION_EVIDENCE['alternative'] = re.compile(
    _LEGACY_RELATIONS['alternative'].pattern +
    r'|\b(?:is|are)\s+(?:an?\s+)?alternative(?:s)?\s+to\b|是.{1,60}的替代(?:选项|方案)', re.I)

_RELATION_EVIDENCE['part_of'] = re.compile(_LEGACY_RELATIONS['part_of'].pattern + r'|\bbelongs? to\b|属于', re.I)

VALIDATOR_VERSION = 'compound-binding-reconciliation-1'

def _validate_money(claim, text, rows, path):
    from .compound_binding_support import coordination_support
    money, source = claim['money'], claim['source']
    precision = money['precision']
    values = {key: _decimal(money[key], path + '.money.' + key) if money[key] is not None else None
              for key in ('value', 'lower', 'upper')}
    if precision in ('exact', 'approximate'):
        if values['value'] is None or values['lower'] is not None or values['upper'] is not None:
            _error('money_point_shape', path + '.money')
        expected = [values['value']]
    elif precision == 'range':
        if values['value'] is not None or values['lower'] is None or values['upper'] is None or values['lower'] > values['upper']:
            _error('money_range_shape', path + '.money')
        expected = [values['lower'], values['upper']]
        if not _RANGE.search(source) and not re.search(r'\d\s*-\s*\d', source):
            _error('money_range_evidence', path + '.money')
    else:
        if any(value is not None for value in values.values()):
            _error('money_unknown_shape', path + '.money')
        expected = []
    evidence = _number_evidence(source)
    numbers = [value for value, _, _ in evidence]
    # Corrections may quote both the superseded and replacement number. Only an
    # explicit correction clause supports using the final number as the new one.
    if claim['kind'] == 'correction' and len(numbers) == 2 and _RELATION_EVIDENCE['correction'].search(source):
        numbers = numbers[-1:]
    if Counter(numbers) != Counter(expected):
        _error('money_source_binding', path + '.money')
    precision_source = monetary_precision_source(source, claim['timing'])
    if precision == 'exact' and _APPROX.search(precision_source):
        _error('money_precision_binding', path + '.money')
    if precision == 'approximate' and not _APPROX.search(precision_source):
        _error('money_precision_binding', path + '.money')
    stated = _currencies(source)
    currency = money['currency']
    target = rows.get(claim['target'], {})
    target_money = target.get('money') or target.get('amount') or {}
    inherited = target.get('currency') or (target_money.get('currency') if isinstance(target_money, dict) else None)
    support = coordination_support(text, claim)
    if not inherited and support: inherited = support['currency']
    if currency is not None and not re.fullmatch('[A-Z]{3}', currency):
        _error('currency_shape', path + '.money.currency')
    if len(stated) > 1 or (stated and stated != {currency}):
        _error('currency_source_binding', path + '.money.currency')
    if currency and not stated and currency != _default_currency(text) and currency != inherited:
        # Preserve any explicitly named ISO currency, even if the downstream
        # compiler does not support that native currency. Never infer from locale.
        if not re.search(r'(?<![A-Za-z])' + re.escape(currency) + r'(?![A-Za-z])', source):
            _error('currency_source_binding', path + '.money.currency')


def _normalize_timing(value):
    """Canonicalize only proven calendar spelling on a detached valid shape.

    Existing literal timing retains the source wording and lets the established
    Future compiler derive an absolute date. Never repair conflicting fields,
    incomplete dates, relative anchors, or a model's unsupported precision.
    """
    for index,claim in enumerate(value['claims']):
        timing=claim['timing']
        literal,iso,precision=timing['text'],timing['iso'],timing['precision']
        if literal is None or precision not in ('day','month'):continue
        proven=explicit_calendar(literal)
        if not proven:continue
        if proven[0]!=precision or iso not in (None,proven[1]):
            _error('timing_inferred_iso', f'$.claims[{index}].timing')
        # Preserve already valid native ISO representation byte-for-byte.
        if iso is not None and iso in literal:continue
        timing['precision']='literal'
        timing['iso']=None


def _validate_timing(claim, path, text=None):
    timing = claim['timing']
    literal, iso, precision = timing['text'], timing['iso'], timing['precision']
    if precision == 'unknown':
        if literal is not None or iso is not None:
            _error('timing_unknown_shape', path + '.timing')
        return
    if literal is None or (literal not in claim['source'] and not (text and timing_support(text, claim))):
        _error('timing_source_binding', path + '.timing')
    if precision == 'literal':
        if iso is not None:
            _error('timing_inferred_iso', path + '.timing')
        return
    pattern = r'\d{4}-\d{2}-\d{2}' if precision == 'day' else r'\d{4}-\d{2}'
    if iso is None or not re.fullmatch(pattern, iso) or iso not in literal:
        _error('timing_inferred_iso', path + '.timing')
    try:
        date.fromisoformat(iso if precision == 'day' else iso + '-01')
    except ValueError:
        _error('timing_invalid_date', path + '.timing')


def _validate_claim(claim, text, rows, path, bundle=None):
    source, target = claim['source'], claim['target']
    from .compound_binding_support import coordination_support
    from .compound_support import reference_ids
    _source(source, text, path + '.source')
    if target is not None and target not in rows:
        _error('target_unknown', path + '.target')
    if target is not None:
        target_label = str(_target_label(rows[target]))
        explicit = {ident for ident, row in rows.items() if _target_label(row) and _contains(source, str(_target_label(row)))}
        if explicit and target not in explicit:
            _error('target_explicit_conflict', path + '.target')
        if target_label and re.search(r'(?:\bnot\s+(?:the\s+)?|不是|并非)'+re.escape(target_label), source, re.I):
            _error('target_negated', path + '.target')
        if target not in reference_ids(source, rows):
            _error('target_unresolved_evidence', path + '.target')
    label_source = source + (' ' + str(_target_label(rows[target])) if target else '')
    if not _contains(label_source, claim['label']):
        _error('label_source_binding', path + '.label')
    if claim['account'] is not None and not _contains(source, claim['account']):
        _error('account_source_binding', path + '.account')
    _validate_money(claim, text, rows, path)
    _validate_timing(claim, path, text)
    role = _ROLE_EVIDENCE.get(claim['kind'])
    support = coordination_support(text, claim)
    role_source = source
    if support and support['role'] == claim['kind']:
        a,b = support['support']; role_source = text[a:b] + source
    refund = refund_report(role_source) if claim['kind'] in ('refund', 'actual_inflow') else False
    planned = plan_support(role_source) if claim['kind'] == 'planned_outflow' else None
    if not planned and bundle is not None and claim['kind']=='planned_outflow':
        from .compound_discourse import planned_membership_support
        planned = planned_membership_support(claim, bundle, text)
    if role is not None and not role.search(role_source) and not refund and not planned:
        _error('kind_source_binding', path + '.kind')
    if claim['kind'] in ACTUAL_KINDS:
        if (not (_ACTUAL.search(role_source) or refund) or _HYPOTHETICAL.search(role_source) or _FUTURE.search(role_source) or _NEGATED_ACTUAL.search(role_source) or re.search(r"\b(?:havent|hasnt|didnt)\b|(?:未|没|不|将|会|明天|准备).{0,8}退", role_source, re.I)):
            _error('actual_source_binding', path + '.kind')
        # Check left context as well: a clipped quote must not turn a conditional
        # subclause into an actual event. Punctuation is evidence scope only.
        for occurrence in re.finditer(re.escape(source), text):
            prefix = re.split(r'[。！？!?;；\n.]', text[:occurrence.start()])[-1]
            if _HYPOTHETICAL.search(prefix) or _NEGATED_ACTUAL.search(prefix) or re.search(r'\b(?:havent|hasnt|didnt)\b|不是|并非|不曾|没|未|明天|将会', prefix, re.I):
                _error('actual_conditional_context', path + '.kind')
    if claim['kind'] == 'planned_outflow' and (_HYPOTHETICAL.search(source) or (not planned and _NEGATED_ACTUAL.search(source)) or re.search(r"\b(?:not|never|didn.t|didnt)\b.{0,16}\b(?:decid\w*|plan\w*|intend\w*|order\w*|book\w*)\b|(?:没|未|不).{0,3}(?:决定|计划|打算|预订|下单)", source, re.I)):
        _error('planned_conditional_source', path + '.kind')
    if claim['kind'] == 'current_asset' and (_HYPOTHETICAL.search(source) or _FUTURE.search(source)):
        _error('current_source_binding', path + '.kind')
    if claim['kind'] in ('future_outflow', 'expected_inflow') and _ACTUAL.search(source) and not (_HYPOTHETICAL.search(source) or _FUTURE.search(source) or _NEGATED_ACTUAL.search(source)):
        _error('future_source_binding', path + '.kind')


def _parse_bundle(raw, text, context=(), validated_claims=None):
    """Parse and conservatively validate a detached, non-authoritative bundle."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        _error('source_text_size')
    if destructive_scope(text):
        _error('destructive_scope_requires_existing_review')
    if isinstance(raw, str):
        if len(raw) > 131072:
            _error('output_size')
        try:
            value = json.loads(raw, object_pairs_hook=_json_pairs,
                parse_constant=lambda value: _error('json_nonfinite'))
        except (json.JSONDecodeError, RecursionError):
            _error('malformed_json')
    elif isinstance(raw, dict):
        value = deepcopy(raw)
    else:
        _error('schema_type')
    _shape(value, bundle_schema())
    _normalize_timing(value)
    rows = _context(context)
    ids = [claim['id'] for claim in value['claims']]
    if len(ids) != len(set(ids)):
        _error('duplicate_claim_id', '$.claims')
    if set(ids) & set(rows):
        _error('claim_context_id_collision', '$.claims')
    claims = dict(zip(ids, value['claims']))
    duplicates = Counter()
    for index, claim in enumerate(value['claims']):
        if not validated_claims or validated_claims.get(claim['id']) != claim:
            _validate_claim(claim, text, rows, f'$.claims[{index}]', value)
        else:
            _source(claim['source'], text, f'$.claims[{index}].source')
        signature = json.dumps({key: child for key, child in claim.items() if key != 'id'}, sort_keys=True)
        duplicates[signature] += 1
        if duplicates[signature] > text.count(claim['source']):
            _error('duplicate_claim', f'$.claims[{index}]')
    links = set()
    dependencies = {}
    for index, relation in enumerate(value['relationships']):
        path = f'$.relationships[{index}]'
        _source(relation['source'], text, path + '.source')
        if relation['from'] == relation['to'] or relation['from'] not in claims.keys() | rows.keys() or relation['to'] not in claims.keys() | rows.keys():
            _error('relationship_endpoint', path)
        if relation['from'] not in claims and relation['to'] not in claims:
            _error('relationship_without_claim', path)
        if not _RELATION_EVIDENCE[relation['kind']].search(relation['source']):
            _error('relationship_source_binding', path)
        if relation['kind']=='alternative':
            for ident in (relation['from'],relation['to']):
                row=claims.get(ident) or rows[ident]
                label=row.get('label') or _target_label(row)
                if label and re.search(r'(?:\bnot\s+(?:the\s+)?|不是|而非|并非)'+re.escape(label)+r'(?![a-z0-9])',relation['source'],re.I):
                    _error('relationship_negated_endpoint',path)
        for endpoint in ('from', 'to'):
            ident = relation[endpoint]
            row = claims.get(ident) or rows[ident]
            label = row.get('label') or _target_label(row)
            if not label or not _contains(relation['source'], label):
                # A pronoun can point to explicitly supplied context; it cannot
                # invent a target or use an unrelated current focus silently.
                from .compound_support import endpoint_supported
                from .compound_discourse import membership_witness
                if not endpoint_supported(ident, relation, claims, rows) and not (endpoint=='to' and membership_witness(relation,claims,text)):
                    _error('relationship_endpoint_binding', path + '.' + endpoint)
        if relation['kind']=='part_of' and re.search(r"\b(?:not|never)\s+(?:a\s+)?part of\b|\b(?:does not|doesn't)\s+(?:include|belong)\b|不属于|不包括|不包含|不是.{0,20}的(?:部分|组成)",relation['source'],re.I):
            _error('relationship_negated_membership',path)
        if relation['kind']=='part_of' and any(ident in claims and claims[ident]['money']['precision']=='unknown' for ident in (relation['from'],relation['to'])):
            from .compound_premises import membership_supported
            membership_rows={**{ident:dict(label=row.get('label') or _target_label(row)) for ident,row in rows.items()},**claims}
            if not membership_supported(relation,membership_rows,text):_error('relationship_membership_direction',path)
        a, b = relation['from'], relation['to']
        if relation['kind'] in ('alternative', 'compatible'):
            a, b = sorted((a, b))
        signature = (relation['kind'], a, b)
        if signature in links:
            _error('duplicate_relationship', path)
        links.add(signature)
        if relation['kind'] in ('conditional_on', 'part_of', 'replacement', 'correction'):
            dependencies.setdefault(relation['from'], set()).add(relation['to'])
    def visit(ident, active, done):
        if ident in active:
            _error('relationship_cycle', '$.relationships')
        if ident not in done:
            for target in dependencies.get(ident, ()):
                visit(target, active | {ident}, done)
            done.add(ident)
    done = set()
    for ident in dependencies:
        visit(ident, set(), done)
    ambiguity_ids = set()
    for group in ('requests', 'ambiguities'):
        for index, item in enumerate(value[group]):
            path = f'$.{group}[{index}]'
            _source(item['source'], text, path + '.source')
            if len(item['claim_ids']) != len(set(item['claim_ids'])) or any(ident not in claims for ident in item['claim_ids']):
                _error('scope_unknown', path + '.claim_ids')
            if group == 'requests' and item['kind'] in _REQUEST_EVIDENCE and not request_cue(item['kind'], item['source']):
                _error('request_source_binding', path)
            if group == 'ambiguities':
                if item['id'] in ambiguity_ids or len(item['choices']) != len(set(item['choices'])):
                    _error('duplicate_ambiguity', path)
                ambiguity_ids.add(item['id'])
    return deepcopy(value)


def parse_bundle(raw, text, context=()):
    """Validate a new interpretation. Continued bundles also need answer lineage."""
    return _parse_bundle(raw, text, context)


def _clarified_claim(claim, previous, answer, rows, path):
    """Bind changed fields to this targeted answer; retain earlier evidence.

    The prior is already server-validated session state, not a client assertion.
    The coordinator must preserve the original and each targeted answer.
    """
    if claim['kind'] != previous['kind']:
        _error('clarification_changed_kind', path)
    if claim['label'] != previous['label'] and not _contains(answer, claim['label']):
        _error('clarification_label_binding', path)
    if claim['target'] != previous['target']:
        target = rows.get(claim['target'])
        if target is None or not _target_label(target) or not _contains(answer, str(_target_label(target))):
            _error('clarification_target_binding', path)
    if claim['account'] != previous['account'] and (claim['account'] is None or not _contains(answer, claim['account'])):
        _error('clarification_account_binding', path)
    if claim['money'] != previous['money']:
        current, old = claim['money'], previous['money']
        amount_changed = any(current[key] != old[key] for key in ('value', 'lower', 'upper'))
        probe = deepcopy(claim)
        probe['source'] = answer if amount_changed else previous['source'] + '\n' + answer
        if _RELATION_EVIDENCE['correction'].search(answer):
            probe['kind'] = 'correction'
        inherited = deepcopy(rows)
        inherited['__prior_currency__'] = {'id': '__prior_currency__', 'currency': old['currency']}
        probe['target'] = '__prior_currency__'
        _validate_money(probe, answer, inherited, path)
        if current['currency'] != old['currency'] and current['currency'] not in _currencies(answer):
            _error('clarification_currency_binding', path)
    if claim['timing'] != previous['timing']:
        probe = deepcopy(claim)
        probe['source'] = answer
        _validate_timing(probe, path)


def interpret_bundle(provider, text, context=(), prior=None, answer=None, coverage_question=None, prior_ledger=None):
    """One provider call; strict transport only, no fallback, repair loop or writes."""
    rows = _context(context)
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        _error('source_text_size')
    payload = {'text': text, 'context': list(rows.values())}
    evidence = text
    old = None
    selected = None
    if prior is not None:
        # This input is the coordinator's previously validated session bundle.
        # A later clarification may already rely on a preserved earlier answer.
        old = deepcopy(prior)
        _shape(old, bundle_schema())
        if isinstance(answer, dict):
            if set(answer) != {'ambiguity_id', 'text'}:
                _error('clarification_answer')
            selected = coverage_question or next((item for item in old['ambiguities'] if item['id'] == answer['ambiguity_id']), None)
            answer = answer['text']
        elif len(old['ambiguities']) == 1:
            selected = old['ambiguities'][0]
        if selected is None:
            _error('clarification_ambiguity')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2048:
            _error('clarification_answer')
        payload.update(prior=old, answer={'ambiguity_id': selected['id'], 'text': answer})
        if coverage_question:
            payload['coverage_review'] = coverage_question
            if answer.strip().casefold() in ('yes','ok','okay','sure','好','好的','是','嗯','可以'):
                _error('clarification_answer_unrelated')
        evidence = text + '\n' + answer
    elif answer is not None:
        _error('clarification_without_prior')
    complete = getattr(provider, 'complete_explanation', None)
    if not callable(complete):
        raise ProviderError('unsupported_structured_transport', 'The selected provider does not support structured interpretation')
    raw, metadata = complete(bundle_prompt(),
        json.dumps(payload, ensure_ascii=False), bundle_schema())
    if isinstance(raw, str):
        if len(raw) > 131072:
            _error('output_size')
        try:
            result = json.loads(raw, object_pairs_hook=_json_pairs,
                parse_constant=lambda value: _error('json_nonfinite'))
        except (json.JSONDecodeError, RecursionError):
            _error('malformed_json')
    else:
        result = deepcopy(raw)
    _shape(result, bundle_schema())
    _normalize_timing(result)
    if old is not None and coverage_question:
        # A source-coverage repair may add missing propositions/links. It cannot
        # rewrite already verified claims, erase requests or existing relations.
        previous = {c['id']: c for c in old['claims']}
        current = {c['id']: c for c in result['claims']}
        if not set(previous) <= set(current) or any(current[k] != v for k,v in previous.items()):
            _error('coverage_changed_existing')
        for group in ('requests','relationships','ambiguities'):
            if any(v not in result[group] for v in old[group]):
                _error('coverage_lost_existing')
        result = _parse_bundle(result, evidence, context, validated_claims=previous)
    elif old is not None:
        previous = {claim['id']: claim for claim in old['claims']}
        current = {claim['id']: claim for claim in result['claims']}
        affected = set(selected['claim_ids'])
        if price_question(selected, old):
            # Broad model question scope may cite the paid deposit as context.
            # It does not license rebinding that settled proposition.
            affected &= {c['id'] for c in old['claims'] if prospective_price(c)}
        if set(previous) != set(current):
            _error('clarification_claim_identity')
        if any(current[ident] != claim for ident, claim in previous.items() if ident not in affected):
            _error('clarification_changed_unaffected')
        for ident in affected:
            _clarified_claim(current[ident], previous[ident], answer, rows, '$.claims.' + ident)
        if result['requests'] != old['requests']:
            _error('clarification_lost_request')
        unaffected_relations = [relation for relation in old['relationships']
            if relation['from'] not in affected and relation['to'] not in affected]
        if any(relation not in result['relationships'] for relation in unaffected_relations):
            _error('clarification_lost_relationship')
        unanswered = [item for item in old['ambiguities'] if item['id'] != selected['id']]
        if any(item not in result['ambiguities'] for item in unanswered):
            _error('clarification_lost_ambiguity')
        from .compound_binding_support import answer_supports_resolution
        if not any(a['id'] == selected['id'] for a in result['ambiguities']) and not answer_supports_resolution(selected, answer, old, result):
            _error('clarification_answer_unrelated')
        result = _parse_bundle(result, evidence, context, validated_claims=current)
    else:
        result = parse_bundle(result, text, context)
    from .compound_binding_support import evidence_ledger
    metadata = deepcopy(metadata)
    bindings = deepcopy((prior_ledger or {}).get('answer_support',[]))
    if old is not None and not coverage_question:
        bindings.append(dict(start=len(text)+1,end=len(evidence),claim_ids=sorted(affected)))
    metadata['evidence_ledger'] = evidence_ledger(result, evidence, context, bindings)
    return result, metadata



def needs_compound(text):
    """Preserve fast paths, but a range word elsewhere is not a money range.

    This only repairs eligibility for two distinct propositions. It does not
    interpret their roles or bypass the reviewed provider/financial boundaries.
    """
    if legacy_needs_compound(text):return True
    if not isinstance(text,str):return False
    numbers=_number_evidence(text)
    if len(numbers)!=2 or not _RANGE.search(text):return False
    between=text[numbers[0][2]:numbers[1][1]]
    before=text[:numbers[0][1]]
    for pattern in _CURRENCY.values():
        between=re.sub(pattern,'',between,flags=re.I)
        before=re.sub(pattern,'',before,flags=re.I)
    plain=between.strip()
    direct=bool(re.fullmatch(r'(?:to|[–—~\-]|至|到)',plain,re.I))
    between_frame=bool(re.search(r'\bbetween\s*$|介于\s*$',before,re.I) and re.fullmatch(r'and|和',plain,re.I))
    return not (direct or between_frame)
