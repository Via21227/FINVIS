"""D2 session-derived, explicitly authored, baseline-relative cost scenarios.

No World command, settlement, financial verdict, model, or durable write port.
Caller serializes access using the existing I4 workflow lock.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from .decision_baseline import CATEGORIES, amount, digest, fields, strings, text
from .domain import Currency, Ref
from .if_interpretation import interpret
from .reviewed_proposals import _wire
from .world_values import AmountEstimate, AmountPrecision
from .world_planning import FutureInflowStatus


class IfError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def effect(terms, currency):
    """Signed native minor units. Unchanged shared inputs cancel before this.

    Bounds are conservative outer bounds, not probabilities or a midpoint.
    An unbounded approximate input mixed with a range stays unknown.
    """
    rows = [(a, sign) for a, sign in terms if a.currency == currency]
    out = dict(currency=currency.value, precision='exact_reported', value_minor=0,
               lower_minor=None, upper_minor=None)
    if any(a.precision is AmountPrecision.UNKNOWN for a, _ in rows):
        return dict(out, precision='unknown', value_minor=None)
    if any(a.precision is AmountPrecision.RANGE for a, _ in rows):
        if any(a.precision is AmountPrecision.APPROXIMATE and a.lower_bound is None for a, _ in rows):
            return dict(out, precision='unknown', value_minor=None)
        lo = hi = 0
        for a, sign in rows:
            lower = (a.lower_bound or a.amount).minor_units
            upper = (a.upper_bound or a.amount).minor_units
            lo += lower if sign == 1 else -upper
            hi += upper if sign == 1 else -lower
        return dict(out, precision='range', value_minor=None, lower_minor=lo, upper_minor=hi)
    return dict(out, value_minor=sum(a.amount.minor_units * sign for a, sign in rows),
                precision='approximate' if any(a.precision is AmountPrecision.APPROXIMATE for a, _ in rows) else 'exact_reported')


def bucket(terms, currencies=None):
    terms = tuple(terms)
    currencies = currencies or {a.currency for a, _ in terms}
    return [effect(terms, c) for c in sorted(currencies, key=lambda c: c.value)]


@dataclass(frozen=True)
class UserIfLine:
    id: str
    baseline: object
    source_text: str
    interpretation: dict
    clarification: dict
    kind: str
    label: str
    category: str
    target: object
    observed_amount: object
    planning_amount: object
    parameter: object
    assumptions: tuple
    depends_on: tuple
    extra_dependencies: tuple
    inflows: tuple
    created_at: datetime


class IfLineService:
    def __init__(self, baselines):
        self.baselines = baselines
        self.proposals = {}
        self.lines = {}
        self.combinations = {}
        self.created = {}
        self.unresolved = {}
        self.evaluations = {}

    def record(self, identity, result, revision=None):
        previous = self.evaluations.get(identity, ())
        self.evaluations[identity] = (*previous, dict(version=len(previous)+1,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            source_revision=revision if revision is not None else self.baselines.authority.read().snapshot.revision,
            result=deepcopy(result)))

    def audit(self, identity):
        rows = self.evaluations.get(identity, ())
        return dict(evaluation_version=len(rows), evaluation_history=deepcopy(rows))

    def baseline(self, raw):
        fields(raw, 'id version')
        ref = Ref(text(raw['id']), raw['version'])
        baseline = next((b for versions in self.baselines.versions.values() for b in versions if b.ref == ref), None)
        if baseline is None: raise IfError('baseline_missing')
        view = self.baselines.view(baseline)
        if view['status'] not in ('estimate_available', 'partially_available'):
            raise IfError('baseline_not_current')
        return baseline

    def targets(self, baseline):
        snapshot = self.baselines.authority.read().snapshot
        components = [dict(key='component:'+c.id, label=c.label, kind='cost', amount=_wire(c.planning_amount)) for c in baseline.components]
        inflows = [dict(key='inflow:'+r.ref.id, label=snapshot.get(r).purpose, kind='expected_inflow',
                        ref=_wire(r), amount=_wire(snapshot.get(r).expected_amount)) for r in snapshot.world.members
                   if r.record_kind == 'future_inflow' and snapshot.get(r).status is FutureInflowStatus.EXPECTED]
        return components + inflows

    def propose(self, request):
        fields(request, 'baseline_ref text')
        baseline = self.baseline(request['baseline_ref'])
        if len(self.proposals) >= 256: raise IfError('session_capacity')
        source = text(request['text'])
        result = interpret(source, self.targets(baseline), baseline.context)
        pid = 'if-proposal/'+uuid4().hex
        self.proposals[pid] = (baseline, source, deepcopy(result))
        return dict(id=pid, baseline_ref=_wire(baseline.ref), **deepcopy(result))

    def create(self, request):
        fields(request, 'proposal_id clarification')
        pid = text(request['proposal_id'])
        if pid not in self.proposals: raise IfError('proposal_missing')
        fingerprint = digest(request)
        if pid in self.created:
            old_hash, identity = self.created[pid]
            if fingerprint != old_hash: raise IfError('proposal_already_used')
            return self.view(self.lines[identity])
        baseline, source, proposed = self.proposals[pid]
        self.baseline(_wire(baseline.ref))
        if proposed['status'] == 'not_hypothetical': raise IfError('hypothetical_only')
        if len(self.lines) >= 128: raise IfError('session_capacity')
        raw = deepcopy(request['clarification'])
        fields(raw, 'kind label category target amount parameter depends_on assumptions')
        kind = raw['kind']
        if kind not in ('add','remove','replace','parameter'): raise IfError('choose_kind')
        if raw['category'] not in CATEGORIES: raise IfError('category_invalid')
        assumptions = strings(raw['assumptions'])
        if not assumptions: raise IfError('assumption_required')
        target = text(raw['target'], True)
        available = {r['key']: r for r in self.targets(baseline)}
        if kind == 'add':
            if target is not None or raw['parameter'] is not None: raise IfError('target_invalid')
        elif target not in available:
            raise IfError('choose_target')
        if kind in ('replace','parameter') and not target.startswith('component:'):
            raise IfError('cost_target_required')
        if kind == 'remove' and raw['amount'] is not None: raise IfError('remove_amount_forbidden')
        observed = amount(raw['amount']) if kind != 'remove' else None
        planning = (AmountEstimate.approximate(observed.amount)
                    if observed and observed.precision is AmountPrecision.EXACT_REPORTED else observed)
        if kind in ('replace','parameter'):
            original = next(c for c in baseline.components if 'component:'+c.id == target)
            if observed.currency != original.planning_amount.currency:
                raise IfError('replacement_currency_mismatch')
        parameter = raw['parameter']
        if kind == 'parameter':
            fields(parameter, 'name before after')
            name = parameter['name']
            if name not in ('duration_days','nights','party_size','quantity'): raise IfError('parameter_invalid')
            if any(type(parameter[k]) is not int or not (0 if name=='nights' else 1) <= parameter[k] <= 10000 for k in ('before','after')):
                raise IfError('parameter_invalid')
            if parameter['before'] == parameter['after']: raise IfError('parameter_invalid')
            if name != 'quantity' and getattr(baseline.context, name) != parameter['before']:
                raise IfError('parameter_before_mismatch')
        elif parameter is not None:
            raise IfError('parameter_invalid')
        dependencies = strings(raw['depends_on'])
        if len(set(dependencies)) != len(dependencies) or any(k not in available or not k.startswith('inflow:') for k in dependencies):
            raise IfError('dependency_invalid')
        if kind=='remove' and target in dependencies: raise IfError('removed_dependency')
        snapshot = self.baselines.authority.read().snapshot
        inflow_keys = set(dependencies) | ({target} if target and target.startswith('inflow:') else set())
        refs = tuple(r for r in snapshot.world.members if r.record_kind=='future_inflow' and 'inflow:'+r.ref.id in inflow_keys)
        extra = tuple((r,digest(snapshot.get(r))) for r in refs)
        inflows = tuple(('inflow:'+r.ref.id, snapshot.get(r).expected_amount) for r in refs)
        line = UserIfLine('if/'+uuid4().hex, baseline, source, deepcopy(proposed), raw, kind,
            text(raw['label']), raw['category'], target, observed, planning, deepcopy(parameter), assumptions,
            dependencies, extra, inflows, datetime.now(timezone.utc))
        result = self.view(line)
        if result['status'] != 'valid': raise IfError('baseline_not_current')
        self.lines[line.id] = line
        self.created[pid] = (fingerprint,line.id)
        self.record(line.id, self.view(line, audit=False))
        return self.view(line)

    def inspect(self, request):
        """Validate/display a draft with the exact create algorithm, unpublished."""
        from copy import copy
        staged = copy(self)
        for name in ('proposals','lines','combinations','created','unresolved','evaluations'):
            setattr(staged,name,dict(getattr(self,name)))
        return staged.create(request)

    def materialization(self, line):
        try:
            snapshot = self.baselines.authority.read().snapshot
        except Exception:
            return None  # validity already exposes unverified source as stale.
        for ref in snapshot.world.members:
            record = snapshot.get(ref)
            proof = getattr(getattr(record, 'lineage', None), 'if_evidence', None)
            if proof and (proof.if_id, proof.source_generation) == (line.id, line.baseline.generation):
                return _wire(ref)
        return None

    def validity(self, line):
        # Reuse D1's exact dependency/freshness semantics, including source failure.
        try:
            b = replace(line.baseline, dependencies=tuple(dict((*line.baseline.dependencies,*line.extra_dependencies)).items()))
            view = self.baselines.view(b)
            reality = self.materialization(line)
            if reality:
                view['stale_reasons'] = list(dict.fromkeys([*view['stale_reasons'], 'hypothesis_materialized']))
                view['status'] = 'stale'
            if line.id in self.unresolved:
                view['stale_reasons'] = list(dict.fromkeys([*view['stale_reasons'], self.unresolved[line.id]]))
                view['status'] = 'stale'
            return view['status'] in ('estimate_available','partially_available'), view
        except Exception:
            return False, dict(status='stale', stale_reasons=['binding_cannot_be_verified'])

    def terms(self, line):
        costs, inflows = [], []
        if line.target and line.target.startswith('component:'):
            old = next(c for c in line.baseline.components if 'component:'+c.id == line.target)
            costs.append((old.planning_amount, -1))
        if line.planning_amount: costs.append((line.planning_amount, 1))
        if line.kind=='remove' and line.target.startswith('inflow:'):
            inflows.append((dict(line.inflows)[line.target], -1))
        return costs, inflows

    def evaluate(self, rows):
        baseline = rows[0].baseline
        removed = {r.target for r in rows if r.target and r.target.startswith('component:')}
        unchanged = [(c.planning_amount,1) for c in baseline.components if 'component:'+c.id not in removed]
        scenario = unchanged + [(r.planning_amount,1) for r in rows if r.planning_amount]
        delta, inflow_delta = [], []
        for row in rows:
            c,i = self.terms(row); delta.extend(c); inflow_delta.extend(i)
        currencies = {c.planning_amount.currency for c in baseline.components} | {a.currency for a,_ in scenario}
        return dict(baseline_cost=bucket([(c.planning_amount,1) for c in baseline.components],currencies),
            scenario_cost=bucket(scenario,currencies), cost_delta=bucket(delta,currencies),
            expected_inflow_delta=bucket(inflow_delta), coverage='reported_components_only',
            parameters=[deepcopy(r.parameter) for r in rows if r.parameter],
            metrics='unavailable_policy_required')

    def view(self, line, audit=True):
        valid, b = self.validity(line)
        return dict(id=line.id, baseline_ref=_wire(line.baseline.ref), decision_ref=_wire(line.baseline.decision_ref),
            status='realized' if self.materialization(line) else 'valid' if valid else 'stale', materialized_ref=self.materialization(line), stale_reasons=b.get('stale_reasons',[]),
            dependency_details=b.get('dependency_details',[]),
            label=line.label, category=line.category, kind=line.kind, target=line.target,
            source_text=line.source_text, interpretation=deepcopy(line.interpretation),
            clarification=deepcopy(line.clarification), assumptions=list(line.assumptions),
            dependencies=_wire((*line.baseline.dependencies,*line.extra_dependencies)),
            parameter=deepcopy(line.parameter), observed_amount=_wire(line.observed_amount),
            effects=self.evaluate([line]) if valid else None, created_at=line.created_at.isoformat(),
            retention='session_derived', financial_authority=False, **(self.audit(line.id) if audit else {}))

    def selected(self, request):
        fields(request, 'line_ids')
        ids = strings(request['line_ids'])
        if not ids or len(set(ids)) != len(ids): raise IfError('selection_required')
        if any(i not in self.lines for i in ids): raise IfError('line_missing')
        return [self.lines[i] for i in sorted(ids)]

    def conflicts(self, rows):
        reasons = set()
        for row in rows:
            if not self.validity(row)[0]: reasons.add('baseline_not_current')
            if row.baseline.ref != rows[0].baseline.ref: reasons.add('different_baselines')
        for index, a in enumerate(rows):
            for b in rows[index+1:]:
                if a.target and a.target == b.target: reasons.add('competing_target_changes')
                if a.parameter and b.parameter and a.parameter['name']==b.parameter['name']:
                    reasons.add('competing_parameters')
                if (a.kind=='remove' and a.target in b.depends_on or b.kind=='remove' and b.target in a.depends_on):
                    reasons.add('removed_dependency')
                if a.kind==b.kind=='add' and (a.source_text.casefold()==b.source_text.casefold() or a.label.casefold()==b.label.casefold()):
                    reasons.add('duplicate_assumption')
        return sorted(reasons)

    def preview(self, request):
        rows = self.selected(request)
        conflicts = self.conflicts(rows)
        costs, inflows = [], []
        if not conflicts:
            for row in rows:
                c,i = self.terms(row); costs.extend(c); inflows.extend(i)
        return dict(status='conflict' if conflicts else 'selection_only', conflicts=conflicts,
            labels=[r.label for r in rows], line_ids=[r.id for r in rows],
            cost_delta=bucket(costs), expected_inflow_delta=bucket(inflows),
            scenario_created=False, financial_authority=False)

    def confirm(self, request):
        rows = self.selected(request)
        conflicts = self.conflicts(rows)
        if conflicts: raise IfError(conflicts[0])
        key = tuple(r.id for r in rows)
        existing = next((c for c in self.combinations.values() if c['line_ids']==key),None)
        if existing: return self.combination_view(existing)
        if len(self.combinations)>=128: raise IfError('session_capacity')
        result = dict(id='combined/'+uuid4().hex,line_ids=key,created_at=datetime.now(timezone.utc).isoformat())
        # Only explicit confirmation reaches complete combined cost evaluation.
        view = self.combination_view(result)
        self.combinations[result['id']] = result
        self.record(result['id'], self.combination_view(result, audit=False))
        return self.combination_view(result)

    def combination_view(self, value, audit=True):
        rows = [self.lines[i] for i in value['line_ids']]
        conflicts = self.conflicts(rows)
        return dict(**value, baseline_ref=_wire(rows[0].baseline.ref), labels=[r.label for r in rows],
                    status='stale' if conflicts else 'confirmed', conflicts=conflicts,
                    effects=None if conflicts else self.evaluate(rows), retention='session_derived',financial_authority=False,
                    **(self.audit(value['id']) if audit else {}))

    def listing(self, decision_id):
        versions = self.baselines.versions.get(decision_id,())
        current = versions[-1] if versions else None
        return dict(lines=[self.view(r) for r in self.lines.values() if r.baseline.decision_ref.ref.id==decision_id],
            combinations=[self.combination_view(c) for c in self.combinations.values()
                          if self.lines[c['line_ids'][0]].baseline.decision_ref.ref.id==decision_id],
            targets=self.targets(current) if current else [], retention='session_derived')
