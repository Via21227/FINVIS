"""Fixed semantic fixtures only. No local/cloud inference or evaluator changes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
import json
import unittest

from finvis.agent_schema import CandidateInterpretation, SCHEMA_VERSION
from finvis.agent_understanding import AgentUnderstandingService
from finvis.agent_reconciliation import (AgentWorldBridge, AccountReview, FactReview,
    PositionReview, ReviewRequest, SourceSelection, OccurrenceReview, reconcile, review_json)
from finvis.domain import Currency, Ref, DecisionStatus
from finvis.store import Rejected, StaleVersion
from finvis.world_commands import PutRecord, ReplacementScope, record_ref
from finvis.world_domain import EarmarkRole, amount_for_currency
from finvis.world_planning import OccurrenceKind
from finvis.world_settlement import BalanceBasis, SettleOccurrence
from finvis.world_store import current_assets
from finvis.world_values import AmountPrecision, TemporalSpec, TemporalRelation, FreshnessAssessment, FreshnessStatus
from finvis.world_replay import export_world_replay, replay_world
from tests.test_agent_understanding import realistic_output, SequenceProvider
from tests.test_world_assets import NOW, OWNER, exact
from tests.test_world_mutation import store, future_add, prepared
from tests.test_world_planning import canonical_bundle
from tests.test_world_settlement import current


AT = NOW + timedelta(minutes=10)
TEXT = ('清空所有的财务状态，我将给你我最新的财务状况，以下默认货币单位为人民币：'
    '我目前工商银行卡里有9000人民币，支付宝里有5000+20000，前者是留着做备用金，'
    '后者用来买我和我妈的新手机，大概要花1.8万左右。毕业以后我租房房租会退给我1.3万左右，'
    '我奶奶还会给我1万。我在新加坡练拳击练了一年，我正在考虑买一个6000左右的winning16oz拳套回国打实战，你觉得怎么样？')


def source(text, excerpt, field='fact'):
    start = text.index(excerpt)
    return (SourceSelection(field, start, start + len(excerpt)),)


def setup(raw=None, text=TEXT, target=None, provider_name=None):
    target = target or store()
    provider = SequenceProvider([raw or realistic_output()])
    if provider_name:
        provider.name = provider_name
    service = AgentUnderstandingService(provider)
    service.interpret(text, target.session_generation, target.snapshot.revision)
    bridge = AgentWorldBridge(service, target)
    bridge.attach(text)
    return service, bridge, target


def canonical_review(target):
    old = current_assets(target.snapshot)
    scope = ReplacementScope(old.state.account_refs, (Currency.CNY,), old.state.position_refs, old.state.earmark_refs)
    return ReviewRequest((
        FactReview('icbc', source(TEXT, '我目前工商银行卡里有9000人民币')),
        FactReview('emergency', source(TEXT, '支付宝里有5000+20000，前者是留着做备用金'),
                   earmark_role=EarmarkRole.EMERGENCY, emergency_use_designated=True),
        FactReview('phones-fund', source(TEXT, '支付宝里有5000+20000，前者是留着做备用金，后者用来买我和我妈的新手机'), earmark_role=EarmarkRole.PURCHASE),
        FactReview('rent-refund', source(TEXT, '毕业以后我租房房租会退给我1.3万左右'),
                   timing=TemporalSpec.relative('graduation', TemporalRelation.AFTER, '毕业后')),
        FactReview('gift', source(TEXT, '我奶奶还会给我1万')),
        FactReview('phones', source(TEXT, '后者用来买我和我妈的新手机，大概要花1.8万左右'), funding_fact_id='phones-fund'),
        FactReview('gloves', source(TEXT, '我正在考虑买一个6000左右的winning16oz拳套回国打实战')),
        FactReview('boxing', source(TEXT, '我在新加坡练拳击练了一年'), decision_ids=('gloves',)),
    ), (
        AccountReview('bank', '工商银行', OWNER, source(TEXT, '我目前工商银行卡里有9000人民币', 'account')),
        AccountReview('wallet', '支付宝', OWNER, source(TEXT, '支付宝里有5000+20000', 'account')),
    ), (
        PositionReview('bank-pool', 'bank', ('icbc',)),
        PositionReview('wallet-pool', 'wallet', ('emergency', 'phones-fund'), ('emergency', 'phones-fund'), 'disjoint'),
    ), scope, coverage_note='Explicit current CNY account subset; essential outflow not supplied')


def finish(bridge, review):
    preview = bridge.preview(OWNER, review)
    grant = bridge.authorize(OWNER, review, preview, AT)
    return bridge.execute(OWNER, review, preview, grant, AT), preview, grant


def records(review, kind):
    return [op.record for op in review.change_set.operations if type(op) is PutRecord and record_ref(op.record).record_kind == kind]


def synthetic(currency='SGD', bank='Payroll jar', category='Roof repair', factor=1):
    # A total plus a non-additive allocation is structurally different from
    # canonical disjoint partitions. No persona-specific rules exist.
    raw = realistic_output()
    raw.update(language='en', default_currency=currency, user_intents=['update_financial_state'],
               destructive_actions=[], clarification_questions=[], user_question=None)
    specs = [('salary-bank', 'current_asset', bank, 4200, 'exact', bank, None),
             ('savings', 'earmarked_current_asset', 'Savings', 800, 'exact', bank, 'Education'),
             ('invoice', 'expected_future_inflow', 'Illustration invoice', 1350, 'approximate', None, None),
             ('repair', 'planned_expense', category, 470, 'approximate', None, None),
             ('course', 'contemplated_decision', 'Weekend course', 220, 'approximate', None, None),
             ('household', 'context', 'Two adults share household costs', None, 'unknown', None, None)]
    facts, lines = [], []
    for fid, kind, label, value, precision, account, earmark in specs:
        facts.append(dict(id=fid, type=kind, label=label, account=account, earmark=earmark, purpose=None,
            temporal_status={'current_asset':'current', 'earmarked_current_asset':'current',
                'expected_future_inflow':'future', 'planned_expense':'planned', 'contemplated_decision':'contemplated', 'context':'context'}[kind],
            amount=None if value is None else dict(currency=currency, value=str(value * factor), precision=precision, range_min=None, range_max=None),
            timing_note='after delivery' if fid == 'invoice' else None, source='user_report'))
        lines.append('{}: {} {}'.format(fid, label, '' if value is None else str(value * factor) + ' ' + currency))
    raw['candidate_facts'] = facts
    text = '\n'.join(lines) + '\nI own the reported account. PRIVATE UNRELATED NOTE'
    reviewed = tuple(FactReview(f['id'], source(text, line), decision_ids=('course',) if f['id'] == 'household' else ()) for f, line in zip(facts, lines))
    request = ReviewRequest(reviewed,
        (AccountReview('bank', bank, OWNER, source(text, 'I own the reported account.', 'account')),),
        (PositionReview('cash', 'bank', ('salary-bank',), ('savings',)),), coverage_note='Reported account only')
    return raw, text, request


class AgentBridgeTests(unittest.TestCase):
    def test_canonical_preview_then_one_atomic_commit(self):
        service, bridge, target = setup()
        before = target.snapshot
        review = bridge.review(canonical_review(target), AT)
        self.assertEqual(review.issues, ())
        self.assertEqual(review.committable_count, 8)
        self.assertIs(target.snapshot, before)
        receipt, preview, grant = finish(bridge, review)
        self.assertTrue(preview.destructive)
        self.assertIn('audit_history', preview.preserved_categories)
        self.assertEqual(len([c for c in preview.changes if c.leaves_current_scope]), 2)
        assets = current_assets(target.snapshot)
        self.assertEqual(len(assets.accounts), 2)
        self.assertEqual(len(assets.positions), 2)
        self.assertEqual(len(assets.earmarks), 2)
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.CNY), exact(34000))
        self.assertEqual(amount_for_currency(assets.earmarked_assets_by_currency(), Currency.CNY), exact(25000))
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(target.snapshot.records[:len(before.records)], before.records)
        self.assertEqual(service.candidate.status, 'committed')
        self.assertEqual(receipt.authorization_ref, grant.ref)

    def test_semantic_roles_precision_and_no_verdict(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        future = records(review, 'future_inflow')
        self.assertEqual(sorted(f.expected_amount.amount.minor_units for f in future), [1000000, 1300000])
        self.assertTrue(all(f.expected_amount.precision is AmountPrecision.APPROXIMATE for f in future))
        planned = records(review, 'planned_expense')[0]
        self.assertEqual(planned.expected_amount.amount.minor_units, 1800000)
        self.assertEqual(planned.funding_links[0].source_ref.record_kind, 'earmark')
        decision = records(review, 'world_decision')[0]
        self.assertEqual(decision.amount.amount.minor_units, 600000)
        self.assertIs(decision.status, DecisionStatus.EXPLORING)
        self.assertEqual(records(review, 'decision_context')[0].decision_refs, (record_ref(decision),))
        self.assertFalse(records(review, 'actual_occurrence'))
        self.assertFalse(any(word in review_json(review).lower() for word in ('runway', 'affordable', 'resilience')))

    def test_temporal_unknown_relative_and_provenance(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        future = records(review, 'future_inflow')
        self.assertEqual({r.timing.kind.value for r in future}, {'relative', 'unknown'})
        links = {binding.evidence.ref for binding in review.bindings}
        for op in review.change_set.operations:
            if type(op) is PutRecord:
                self.assertTrue(set(op.record.observation.evidence_refs) <= links)
                self.assertEqual(op.record.observation.confidence.assessment.value, 'unassessed')
        self.assertEqual({r.timing.source_text for r in future}, {'毕业后', '未来'})

    def test_missing_scope_does_not_clear(self):
        _, bridge, target = setup()
        before = target.snapshot
        review = bridge.review(replace(canonical_review(target), replacement_scope=None), AT)
        self.assertIsNone(review.change_set)
        self.assertIn('replacement_scope_required', {i.code for i in review.issues})
        with self.assertRaises(Rejected): bridge.preview(OWNER, review)
        self.assertIs(target.snapshot, before)

    def test_replacement_must_not_silently_use_subset(self):
        _, bridge, target = setup()
        request = canonical_review(target)
        review = bridge.review(replace(request, facts=request.facts[:1]), AT)
        self.assertIn('replacement_subset', {i.code for i in review.issues})
        self.assertIsNone(review.change_set)

    def test_explicit_independent_merge_without_account_invention(self):
        _, bridge, target = setup()
        one = canonical_review(target).facts[4]
        review = bridge.review(ReviewRequest((one,), independent_merge=True), AT)
        self.assertEqual(review.committable_count, 1)
        finish(bridge, review)
        self.assertEqual(len(current_assets(target.snapshot).positions), 1)

    def test_missing_account_and_ownership_require_clarification(self):
        for mutation in ('missing', 'other-owner', 'wrong-label'):
            with self.subTest(mutation=mutation):
                _, bridge, target = setup()
                request = canonical_review(target)
                accounts = () if mutation == 'missing' else (replace(request.accounts[0], **({'owner_id':'someone-else'} if mutation == 'other-owner' else {'label':'Unknown account'})), request.accounts[1])
                review = bridge.review(replace(request, accounts=accounts), AT)
                self.assertIsNone(review.change_set)
                self.assertTrue(review.issues)

    def test_cancel_after_authorization_leaves_bytes_unchanged(self):
        _, bridge, target = setup()
        before = target.snapshot
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        grant = bridge.authorize(OWNER, review, preview, AT)
        bridge.cancel(OWNER)
        with self.assertRaises(Rejected): bridge.execute(OWNER, review, preview, grant, AT)
        self.assertIs(target.snapshot, before)

    def test_no_execution_without_registered_consent(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        with self.assertRaises(Rejected): bridge.execute(OWNER, review, preview, None, AT)
        self.assertEqual(target.snapshot.revision, 0)

    def test_tampered_review_preview_and_grant_rejected(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        grant = bridge.authorize(OWNER, review, preview, AT)
        for r, p, g in ((replace(review, candidate_id='forged'), preview, grant),
                        (review, replace(preview, destructive=False), grant),
                        (review, preview, replace(grant, actor='forged'))):
            with self.subTest(r=r.candidate_id, g=g.actor):
                with self.assertRaises(Rejected): bridge.execute(OWNER, r, p, g, AT)
        self.assertEqual(target.snapshot.revision, 0)

    def test_candidate_changed_same_id_invalidates_authorization(self):
        service, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        grant = bridge.authorize(OWNER, review, preview, AT)
        raw = realistic_output(); raw['candidate_facts'][0]['amount']['value'] = '123'
        service.candidate = replace(service.candidate, interpretation=CandidateInterpretation.parse(raw))
        with self.assertRaises(StaleVersion): bridge.execute(OWNER, review, preview, grant, AT)
        self.assertEqual(target.snapshot.revision, 0)

    def test_reset_revision_generation_and_closed_candidate_rejected(self):
        for field, value in (('generation','new-session'), ('revision', 99), ('status','confirmed_no_commit'), ('id','new-candidate')):
            with self.subTest(field=field):
                service, bridge, target = setup()
                service.candidate = replace(service.candidate, **{field:value})
                with self.assertRaises(StaleVersion): bridge.review(canonical_review(target), AT)
        service, bridge, target = setup(); service.reset()
        with self.assertRaises(StaleVersion): bridge.review(canonical_review(target), AT)

    def test_stale_world_preview_rejected(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        grant = bridge.authorize(OWNER, review, preview, AT)
        _, _, command = prepared(target, future_add(target))
        target.execute(command)
        before = target.snapshot
        with self.assertRaises(StaleVersion): bridge.execute(OWNER, review, preview, grant, AT)
        self.assertIs(target.snapshot, before)

    def test_new_review_invalidates_old_preview_and_consent(self):
        _, bridge, target = setup()
        request = canonical_review(target)
        old = bridge.review(request, AT)
        preview = bridge.preview(OWNER, old)
        grant = bridge.authorize(OWNER, old, preview, AT)
        revised = bridge.review(replace(request, coverage_note='Revised reviewed coverage'), AT)
        with self.assertRaises(Rejected): bridge.execute(OWNER, old, preview, grant, AT)
        with self.assertRaises(Rejected): target.authorize(OWNER, preview, Ref('extra',1), AT)
        self.assertNotEqual(old.ref, revised.ref)

    def test_wrong_earmark_is_not_silently_repaired(self):
        raw = realistic_output(); raw['candidate_facts'][1]['earmark'] = 'Travel'
        _, bridge, target = setup(raw)
        review = bridge.review(canonical_review(target), AT)
        self.assertIn('Travel', {m.purpose for m in records(review, 'earmark')})
        bridge.cancel(OWNER)
        self.assertEqual(target.snapshot.revision, 0)

    def test_conflicts_and_unsupported_do_not_auto_commit(self):
        for key in ('conflicts', 'assumptions', 'unsupported_requests'):
            with self.subTest(key=key):
                raw = realistic_output(); raw[key] = ['Requires explicit resolution']
                _, bridge, target = setup(raw)
                review = bridge.review(canonical_review(target), AT)
                self.assertIsNone(review.change_set)

    def test_unsupported_currency_missing_currency_and_fractional_minor_units(self):
        for currency, value in (('USD','9'), (None,'9'), ('CNY','0.001')):
            with self.subTest(currency=currency, value=value):
                raw, text, request = synthetic()
                raw['default_currency'] = currency
                raw['candidate_facts'][2]['amount'].update(currency=currency, value=value)
                _, bridge, _ = setup(raw, text)
                review = bridge.review(replace(request, facts=(request.facts[2],), accounts=(), positions=(), coverage_note=None, independent_merge=True), AT)
                self.assertIsNone(review.change_set)

    def test_range_and_unknown_preserved_without_question(self):
        for amount in (dict(currency='SGD', value=None, range_min='1100', range_max='1600', precision='range'),
                       dict(currency='SGD', value=None, range_min=None, range_max=None, precision='unknown')):
            raw, text, request = synthetic(); raw['candidate_facts'][2]['amount'] = amount
            _, bridge, _ = setup(raw, text)
            review = bridge.review(replace(request, facts=(request.facts[2],), accounts=(), positions=(), coverage_note=None, independent_merge=True), AT)
            self.assertEqual(records(review, 'future_inflow')[0].expected_amount.precision.value, amount['precision'])
            self.assertEqual(review.issues, ())

    def test_no_evidence_invalid_offsets_and_wrong_time_rejected(self):
        for sources, timing in (((), None), ((SourceSelection('amount',0,99999),), None),
                              (source(TEXT, '我奶奶还会给我1万'), TemporalSpec.known_date(date(2027,1,1)))):
            _, bridge, target = setup()
            request = canonical_review(target)
            fact = replace(request.facts[4], sources=sources, timing=timing)
            review = bridge.review(ReviewRequest((fact,), independent_merge=True), AT)
            self.assertIsNone(review.change_set)

    def test_context_without_relationship_is_context_only(self):
        _, bridge, target = setup()
        context = replace(canonical_review(target).facts[-1], decision_ids=())
        review = bridge.review(ReviewRequest((context,), independent_merge=True), AT)
        outcome = next(o for o in review.outcomes if o.fact_id == 'boxing')
        self.assertEqual(outcome.disposition, 'context_only')
        self.assertIsNone(review.change_set)

    def test_synthetic_salaried_freelancer_household_sgd(self):
        raw, text, request = synthetic()
        _, bridge, target = setup(raw, text)
        review = bridge.review(request, AT)
        self.assertEqual(review.committable_count, 6)
        finish(bridge, review)
        assets = current_assets(target.snapshot)
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.SGD), exact(4200, Currency.SGD))
        self.assertEqual(amount_for_currency(assets.earmarked_assets_by_currency(), Currency.SGD), exact(800, Currency.SGD))
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.CNY), exact(700))

    def test_minimum_necessary_context_export_and_audit(self):
        raw, text, request = synthetic()
        _, bridge, _ = setup(raw, text)
        review = bridge.review(request, AT)
        finish(bridge, review)
        encoded = review_json(bridge.audit)
        self.assertNotIn('PRIVATE UNRELATED NOTE', encoded)
        self.assertNotIn('submitted_text', encoded)
        self.assertIn('Illustration invoice', encoded)
        self.assertIsNone(bridge._text)

    def test_repeated_mapping_is_deterministic(self):
        service, _, target = setup()
        request = canonical_review(target)
        args = (service.candidate, TEXT, request, target.snapshot, target.session_generation, Ref('repeat',1), AT)
        self.assertEqual(reconcile(*args), reconcile(*args))
        self.assertEqual(reconcile(*args).digest, reconcile(*args).digest)

    def test_renaming_categories_scaling_and_reordering(self):
        results = []
        for bank, category, factor, reverse in (('Payroll jar','Roof repair',1,False),
                                               ('Union account','Laptop service',3,True)):
            raw, text, request = synthetic(bank=bank, category=category, factor=factor)
            if reverse:
                raw['candidate_facts'].reverse()
                request = replace(request, facts=tuple(reversed(request.facts)))
            _, bridge, target = setup(raw,text)
            review = bridge.review(request,AT)
            finish(bridge, review)
            results.append((sorted(record_ref(op.record).record_kind for op in review.change_set.operations),
                amount_for_currency(current_assets(target.snapshot).current_assets_by_currency(),Currency.SGD).amount.minor_units // factor))
        self.assertEqual(results[0], results[1])

    def test_fact_order_does_not_change_operations(self):
        raw, text, request = synthetic()
        first, _, target = setup(raw,text)
        args = (text, request, target.snapshot, target.session_generation, Ref('same',1), AT)
        one = reconcile(first.candidate, *args)
        raw['candidate_facts'].reverse()
        reordered = replace(first.candidate, interpretation=CandidateInterpretation.parse(raw))
        two = reconcile(reordered, *args)
        self.assertEqual(replace(one.change_set,candidate_refs=()), replace(two.change_set,candidate_refs=()))
        self.assertEqual(one.outcomes, two.outcomes)

    def test_provider_names_do_not_change_privileges(self):
        digests = []
        for name in ('fixed-local-provider', 'fixed-cloud-provider'):
            service, _, target = setup(provider_name=name)
            result = reconcile(service.candidate, TEXT, canonical_review(target), target.snapshot,
                               target.session_generation, Ref('same',1), AT)
            digests.append(replace(result.change_set,candidate_refs=()))
            self.assertEqual(target.snapshot.revision, 0)
        self.assertEqual(digests[0], digests[1])

    def test_concurrent_exact_retry_is_one_publication(self):
        _, bridge, target = setup()
        review = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, review)
        grant = bridge.authorize(OWNER, review, preview, AT)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: bridge.execute(OWNER,review,preview,grant,AT),range(2)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(target.snapshot.revision,1)
        self.assertEqual(len(bridge.audit),1)

    def test_bridge_commit_replays_through_unchanged_slice_f(self):
        _, bridge, target = setup()
        finish(bridge, bridge.review(canonical_review(target), AT))
        package = export_world_replay(target.snapshot, target.session_generation)
        self.assertEqual(replay_world(package), target.snapshot)

    def test_conservation_failure_leaves_world_unchanged(self):
        raw,text,request=synthetic(); raw['candidate_facts'][1]['amount']['value']='5000'
        _,bridge,target=setup(raw,text)
        before=target.snapshot
        review=bridge.review(request,AT)
        with self.assertRaises((Rejected,ValueError)): bridge.preview(OWNER,review)
        self.assertIs(target.snapshot,before)

    def test_user_revision_corrects_meaning_and_requires_new_consent(self):
        raw = realistic_output(); raw['candidate_facts'][1]['earmark'] = 'Travel'
        service, bridge, target = setup(raw)
        first = bridge.review(canonical_review(target), AT)
        preview = bridge.preview(OWNER, first)
        grant = bridge.authorize(OWNER, first, preview, AT)
        old = service.candidate
        revised = service.revise(old.id, old.generation, old.revision,
                                 CandidateInterpretation.parse(realistic_output()))
        self.assertNotEqual(revised.id, old.id)
        self.assertEqual(service.provider.calls, 1)
        with self.assertRaises(StaleVersion): bridge.execute(OWNER, first, preview, grant, AT)
        bridge.attach(TEXT)
        review = bridge.review(canonical_review(target), AT)
        self.assertIn('备用金', {m.purpose for m in records(review, 'earmark')})
        finish(bridge, review)
        self.assertEqual(target.snapshot.revision, 1)

    def test_explicit_current_balance_correction_preserves_identity(self):
        raw, text, request = synthetic(currency='CNY', bank='Old wallet')
        raw['user_intents'] = ['correct_information']
        service, bridge, target = setup(raw, text)
        assets = current_assets(target.snapshot)
        request = replace(request, facts=(request.facts[0],), independent_merge=True,
            accounts=(replace(request.accounts[0], existing_ref=record_ref(assets.accounts[0])),),
            positions=(replace(request.positions[0], allocation_ids=(), expected_ref=record_ref(assets.positions[0]), correction_reason='User refresh of current balance'),))
        review = bridge.review(request, AT)
        finish(bridge, review)
        pos = current_assets(target.snapshot).positions[0]
        self.assertEqual(pos.ref.id, assets.positions[0].ref.id)
        self.assertEqual(pos.ref.version, 2)
        self.assertEqual(pos.observation.corrects_ref, assets.positions[0].ref)
        self.assertEqual(pos.balance, exact(4200))

    def test_unknown_correction_target_does_not_create_new_balance(self):
        raw,text,request=synthetic(); raw['user_intents']=['correct_information']
        _,bridge,target=setup(raw,text)
        review=bridge.review(request,AT)
        self.assertIsNone(review.change_set)
        self.assertIn('correction_target_required',{i.code for i in review.issues})

    def test_existing_account_identity_is_not_matched_by_label(self):
        raw,text,request=synthetic(bank='Old wallet')
        _,bridge,target=setup(raw,text)
        review=bridge.review(request,AT)
        self.assertIsNone(review.change_set)
        self.assertIn('account_review',{i.code for i in review.issues})

    def test_current_approximate_and_unknown_stay_nonexact(self):
        for precision,value in (('approximate','4200'),('unknown',None)):
            raw,text,request=synthetic()
            raw['candidate_facts'][0]['amount'].update(precision=precision,value=value)
            _,bridge,target=setup(raw,text)
            request=replace(request,facts=(request.facts[0],),independent_merge=True,
                            positions=(replace(request.positions[0],allocation_ids=()),))
            review=bridge.review(request,AT)
            finish(bridge,review)
            pos=next(p for p in current_assets(target.snapshot).positions if p.currency is Currency.SGD)
            self.assertEqual(pos.balance.precision.value,precision)

    def test_parent_total_and_allocation_never_double_count(self):
        raw,text,request=synthetic()
        _,bridge,target=setup(raw,text)
        review=bridge.review(request,AT)
        self.assertEqual(records(review,'asset_position')[0].balance,exact(4200,Currency.SGD))
        self.assertEqual(records(review,'earmark')[0].allocation,exact(800,Currency.SGD))

    def test_ambiguous_or_duplicate_position_composition_fails_closed(self):
        raw,text,request=synthetic()
        with self.assertRaises(ValueError):
            replace(request.positions[0],balance_ids=('salary-bank','savings'))
        with self.assertRaises(ValueError):
            replace(request.positions[0],balance_ids=('salary-bank','salary-bank'),composition='disjoint')
        _,bridge,target=setup(raw,text)
        request=replace(request,positions=(*request.positions,replace(request.positions[0],key='duplicate')))
        review=bridge.review(request,AT)
        self.assertIsNone(review.change_set)

    def test_replacement_preserves_unmentioned_future_records(self):
        target=store(canonical_bundle())
        _,bridge,target=setup(target=target)
        original={r for r in target.snapshot.world.members if r.record_kind in ('future_inflow','planned_expense','world_decision','decision_context')}
        review=bridge.review(canonical_review(target),AT)
        finish(bridge,review)
        self.assertTrue(original <= set(target.snapshot.world.members))
        self.assertTrue(original <= set(review.change_set.retained_refs))

    def test_uncertainty_requires_review_but_missing_essentials_is_not_fabricated(self):
        raw,text,request=synthetic(); raw['uncertainties']=['Invoice date unknown']
        raw['clarification_questions']=['Monthly essentials?']
        _,bridge,_=setup(raw,text)
        self.assertIsNone(bridge.review(request,AT).change_set)
        review=bridge.review(replace(request,acknowledged_uncertainties=('Invoice date unknown',)),AT)
        self.assertIsNotNone(review.change_set)
        self.assertNotIn('essential_outflow',review_json(review))

    def test_field_specific_evidence_and_minimal_export(self):
        raw,text,request=synthetic()
        _,bridge,target=setup(raw,text)
        fact=replace(request.facts[2],sources=(
            *source(text,'Illustration invoice','fact'), *source(text,'1350 SGD','amount')))
        request=replace(request,facts=(fact,),accounts=(),positions=(),coverage_note=None,independent_merge=True)
        review=bridge.review(request,AT)
        finish(bridge,review)
        exported=bridge.export()
        encoded=json.dumps(exported)
        self.assertEqual({b.field for b in review.bindings},{'fact','amount'})
        self.assertIn('world_replay',exported)
        self.assertNotIn('PRIVATE UNRELATED NOTE',encoded)
        self.assertEqual(len(exported['review_audit']['value']),1)

    def test_fault_before_publication_has_no_receipt_or_review_audit(self):
        def fail(_): raise RuntimeError('injected staging failure')
        target=store(hook=fail)
        _,bridge,target=setup(target=target)
        before=target.snapshot
        review=bridge.review(canonical_review(target),AT)
        preview=bridge.preview(OWNER,review)
        grant=bridge.authorize(OWNER,review,preview,AT)
        with self.assertRaises(RuntimeError): bridge.execute(OWNER,review,preview,grant,AT)
        self.assertIs(target.snapshot,before)
        self.assertEqual(bridge.audit,())
        self.assertIsNotNone(bridge._text)

    def test_wrong_actor_and_changed_retry_timestamp_rejected(self):
        _,bridge,target=setup()
        review=bridge.review(canonical_review(target),AT)
        with self.assertRaises(Rejected): bridge.preview('other-owner',review)
        receipt,preview,grant=finish(bridge,review)
        with self.assertRaises(Rejected): bridge.execute('other-owner',review,preview,grant,AT)
        with self.assertRaises(Rejected): bridge.execute(OWNER,review,preview,grant,AT+timedelta(seconds=1))
        self.assertEqual(target.snapshot.revision,1)

    def test_extra_relationship_fields_are_not_silently_ignored(self):
        raw,text,request=synthetic()
        _,bridge,_=setup(raw,text)
        first=replace(request.facts[0],funding_fact_id='savings')
        review=bridge.review(replace(request,facts=(first,*request.facts[1:])),AT)
        self.assertIsNone(review.change_set)

    def test_minimal_evidence_content_is_bound_to_authorization(self):
        _,bridge,target=setup()
        review=bridge.review(canonical_review(target),AT)
        preview=bridge.preview(OWNER,review)
        grant=bridge.authorize(OWNER,review,preview,AT)
        changed=replace(review.bindings[0],evidence=replace(review.bindings[0].evidence,
                                                        decision_relevant_excerpt='tampered source'))
        tampered=replace(review,bindings=(changed,*review.bindings[1:]))
        self.assertNotEqual(review.binding_digest,tampered.binding_digest)
        self.assertEqual(review.change_set.candidate_refs,(Ref('reviewed-interpretation/'+review.binding_digest,1),))
        with self.assertRaises(Rejected): bridge.execute(OWNER,tampered,preview,grant,AT)
        self.assertEqual(target.snapshot.revision,0)

    def test_unknown_currency_does_not_block_independent_nonmonetary_context(self):
        raw,text,request=synthetic()
        raw['default_currency']=None
        raw['candidate_facts'][4]['amount']=None
        _,bridge,target=setup(raw,text)
        review=bridge.review(ReviewRequest(request.facts[4:],independent_merge=True),AT)
        self.assertEqual(review.committable_count,2)
        finish(bridge,review)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,exact(700))

    def test_cny_replacement_cannot_erase_existing_sgd_positions(self):
        raw,text,request=synthetic()
        _,bridge,target=setup(raw,text)
        finish(bridge,bridge.review(request,AT))
        assets=current_assets(target.snapshot)
        sgd={record_ref(p) for p in assets.positions if p.currency is Currency.SGD}
        _,next_bridge,target=setup(target=target)
        request=canonical_review(target)
        scope=replace(request.replacement_scope,
            position_refs=tuple(r for r in request.replacement_scope.position_refs if r not in sgd),
            earmark_refs=tuple(r for r in request.replacement_scope.earmark_refs if target.snapshot.get(r).position_ref not in sgd))
        finish(next_bridge,next_bridge.review(replace(request,replacement_scope=scope),AT))
        self.assertTrue(sgd <= set(target.snapshot.world.members))
        self.assertEqual(amount_for_currency(current_assets(target.snapshot).current_assets_by_currency(),Currency.SGD),exact(4200,Currency.SGD))


class AgentOccurrenceBridgeTests(unittest.TestCase):
    def prepare(self, kind=OccurrenceKind.EXPENSE, source_known=True, basis=BalanceBasis.PRIOR):
        event = ('I paid 125 for bicycle repairs from my old wallet.' if kind is OccurrenceKind.EXPENSE
                 else 'I received 125 for design work into my old wallet.')
        coverage = ('The reported 700 balance already includes this occurrence.' if basis is BalanceBasis.INCLUDED
                    else 'The reported 700 balance was before this occurrence.')
        text = event + ' ' + coverage
        raw=realistic_output(); raw.update(candidate_facts=[],destructive_actions=[],user_intents=['update_financial_state'],unsupported_requests=['Actual occurrence requires structured user review'])
        _,bridge,target=setup(raw,text)
        pos=current_assets(target.snapshot).positions[0]
        request=OccurrenceReview(kind,exact(125),TemporalSpec.known_date(date(2026,9,20)),
            source(text,event),
            record_ref(pos) if source_known else None,basis,
            source(text,coverage,'coverage'),
            coverage,
            freshness=FreshnessAssessment(FreshnessStatus.CURRENT, 'User reviewed current occurrence and coverage', AT))
        return bridge,target,request

    def test_actual_routes_to_slice_e_then_exact_consent(self):
        bridge,target,request=self.prepare()
        before=target.snapshot
        review=bridge.review_occurrence(request,AT)
        self.assertIs(type(review.change_set.operations[0]),SettleOccurrence)
        self.assertIs(target.snapshot,before)
        finish(bridge,review)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,exact(575))
        self.assertEqual(replay_world(export_world_replay(target.snapshot,target.session_generation)),target.snapshot)

    def test_unknown_source_destination_and_coverage_remain_unresolved(self):
        for kind,known,basis in ((OccurrenceKind.EXPENSE,False,BalanceBasis.PRIOR),
                                 (OccurrenceKind.INFLOW,False,BalanceBasis.PRIOR),
                                 (OccurrenceKind.EXPENSE,True,None)):
            with self.subTest(kind=kind,known=known):
                bridge,target,request=self.prepare(kind,known,basis)
                review=bridge.review_occurrence(request,AT)
                self.assertIsNone(review.change_set)
                self.assertTrue(review.issues)
                self.assertEqual(target.snapshot.revision,0)

    def test_inclusive_balance_has_no_second_posting(self):
        bridge,target,request=self.prepare(basis=BalanceBasis.INCLUDED)
        finish(bridge,bridge.review_occurrence(request,AT))
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,exact(700))

    def test_actual_coverage_needs_its_own_reviewed_source(self):
        bridge,target,request=self.prepare()
        with self.assertRaises(ValueError): bridge.review_occurrence(replace(request,coverage_sources=()),AT)
        self.assertEqual(target.snapshot.revision,0)

    def test_expected_amount_cannot_become_actual_implicitly(self):
        _,bridge,target=setup()
        review=bridge.review(canonical_review(target),AT)
        self.assertFalse(any(type(op) is SettleOccurrence for op in review.change_set.operations))
        self.assertEqual(target.snapshot.revision,0)

    def test_generic_actual_inflow_uses_explicit_destination(self):
        bridge,target,request=self.prepare(kind=OccurrenceKind.INFLOW)
        finish(bridge,bridge.review_occurrence(request,AT))
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,exact(825))

    def test_unassessed_actual_freshness_cannot_settle(self):
        bridge,target,request=self.prepare()
        review=bridge.review_occurrence(replace(request,freshness=None),AT)
        self.assertIsNone(review.change_set)
        self.assertIn('settlement_conflict',{i.code for i in review.issues})

    def test_canonical_planned_to_actual_uses_exact_slice_e_relationships(self):
        target=store(canonical_bundle())
        text='Phones purchased for CNY 17600 from Alipay phone reserve. The 25000 balance predates this purchase.'
        raw=realistic_output(); raw.update(candidate_facts=[],destructive_actions=[],user_intents=['update_financial_state'])
        _,bridge,target=setup(raw,text,target)
        request=OccurrenceReview(OccurrenceKind.EXPENSE,exact(17600),TemporalSpec.known_date(date(2026,9,20)),
            source(text,'Phones purchased for CNY 17600 from Alipay phone reserve.'),
            record_ref(current(target,'asset_position','alipay-cny')),BalanceBasis.PRIOR,
            source(text,'The 25000 balance predates this purchase.','coverage'),'Explicit prior baseline',
            record_ref(current(target,'planned_expense','phones')),record_ref(current(target,'earmark','phone')),
            freshness=FreshnessAssessment(FreshnessStatus.CURRENT,'Reviewed current report',AT))
        finish(bridge,bridge.review_occurrence(request,AT))
        self.assertEqual(current(target,'asset_position','alipay-cny').balance,exact(7400))
        self.assertEqual(current(target,'earmark','phone').allocation,exact(2400))
        self.assertEqual(current(target,'earmark','emergency').allocation,exact(5000))


if __name__ == '__main__':
    unittest.main()
