"""I6.2 read-only binding and dependency-aware validity for User evaluations.

The binding is metadata, not an evaluated number, a financial fact or consent.
"""

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Optional, Tuple

from .domain import Currency, Ref
from .engine_inputs import (Capability, EngineInputManifest, GateStatus, InputKind,
                            InputStatus, ManifestInput, read_engine_inputs)
from .world_application import ContextKind, ReadVersion, UserWorldAuthority
from .world_values import AmountPrecision, TypedRef


class Validity(Enum):
    CURRENT = 'current'
    STALE = 'stale'


class InvalidationCode(Enum):
    DEPENDENCY_CHANGED = 'dependency_changed'
    DEPENDENCY_REMOVED = 'dependency_removed'
    DEPENDENCY_BECAME_UNKNOWN = 'dependency_became_unknown'
    PRECISION_CHANGED = 'precision_changed'
    CURRENCY_SCOPE_CHANGED = 'currency_scope_changed'
    POLICY_INPUT_CHANGED = 'policy_input_changed'
    AUTHORITATIVE_WORLD_MISMATCH = 'authoritative_world_mismatch'
    REQUIRED_INPUT_UNAVAILABLE = 'required_input_unavailable'
    CAPABILITY_BLOCKED = 'capability_blocked'
    MODEL_VERSION_CHANGED = 'model_version_changed'
    BINDING_CANNOT_BE_VERIFIED = 'binding_cannot_be_verified'


@dataclass(frozen=True)
class InputKey:
    kind: InputKind
    currency: Optional[Currency]
    identity: Optional[str] = None


@dataclass(frozen=True)
class InputFingerprint:
    key: InputKey
    digest: str
    refs: Tuple[TypedRef, ...]
    status: InputStatus
    precision: Optional[AmountPrecision]


@dataclass(frozen=True)
class EvaluationBinding:
    evaluation_ref: Ref
    capability: Capability
    currency: Optional[Currency]
    world_version: ReadVersion
    model_version: str
    evaluated_at: datetime
    dependencies: Tuple[InputFingerprint, ...]
    digest: str


@dataclass(frozen=True)
class InvalidationReason:
    code: InvalidationCode
    key: Optional[InputKey] = None
    before_refs: Tuple[TypedRef, ...] = ()
    current_refs: Tuple[TypedRef, ...] = ()
    detail: str = ''


@dataclass(frozen=True)
class DependencyCheck:
    key: InputKey
    current_refs: Tuple[TypedRef, ...]
    current_status: Optional[InputStatus]
    current_precision: Optional[AmountPrecision]


@dataclass(frozen=True)
class EvaluationValidity:
    status: Validity
    binding: EvaluationBinding
    current_version: Optional[ReadVersion]
    dependencies: Tuple[DependencyCheck, ...]
    reasons: Tuple[InvalidationReason, ...]


_RECORD_KINDS = frozenset((InputKind.ASSET_POSITION, InputKind.EARMARK,
    InputKind.FUTURE_INFLOW, InputKind.PLANNED_EXPENSE, InputKind.DECISION,
    InputKind.ACTUAL_OCCURRENCE, InputKind.PLAN))
_RECORD_REF_KIND = {
    InputKind.ASSET_POSITION: 'asset_position', InputKind.EARMARK: 'earmark',
    InputKind.FUTURE_INFLOW: 'future_inflow', InputKind.PLANNED_EXPENSE: 'planned_expense',
    InputKind.DECISION: 'world_decision', InputKind.ACTUAL_OCCURRENCE: 'actual_occurrence',
    InputKind.PLAN: 'world_plan',
}
_BASE_INPUTS = {
    Capability.REPORTED_ASSETS: (InputKind.CURRENT_ASSETS,),
    Capability.RUNWAY: (InputKind.CURRENT_ASSETS, InputKind.MONTHLY_ESSENTIALS,
                        InputKind.EMERGENCY_LIQUIDITY),
}


def _key(item: ManifestInput) -> InputKey:
    own = tuple(ref for ref in item.refs if ref.record_kind == _RECORD_REF_KIND.get(item.kind))
    identity = own[0].ref.id if len(own) == 1 else None
    if item.kind in _RECORD_KINDS and identity is None:
        raise ValueError('Record input lacks a stable identity')
    return InputKey(item.kind, item.currency, identity)


def _sort_key(key):
    return (key.kind.value, key.currency.value if key.currency else '', key.identity or '')


def _index(manifest: EngineInputManifest):
    if type(manifest) is not EngineInputManifest or type(manifest.version) is not ReadVersion:
        raise ValueError('Initialized I6.1 manifest required')
    result = {}
    for item in manifest.inputs:
        key = _key(item)
        if key in result:
            raise ValueError('Ambiguous input identity')
        result[key] = item
    return result


def _wire(value):
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if type(value) is tuple:
        return [_wire(part) for part in value]
    if is_dataclass(value):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    raise ValueError('Unsupported binding value')


def _digest(value) -> str:
    payload = json.dumps(_wire(value), ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return sha256(payload.encode('utf-8')).hexdigest()


def _fingerprint(key, item):
    return InputFingerprint(key, _digest(item), item.refs, item.status,
                            item.value.precision if item.value else None)


def _gate(manifest, capability, currency):
    matches = tuple(g for g in manifest.gates if g.capability is capability and g.currency is currency)
    if len(matches) != 1:
        raise ValueError('Capability/scope gate is absent or ambiguous')
    return matches[0]


def _binding_digest(binding):
    return _digest(replace(binding, digest=''))


def _bind_from_manifest(manifest: EngineInputManifest, evaluation_ref: Ref,
                        capability: Capability, currency: Optional[Currency],
                        model_version: str, evaluated_at: datetime,
                        extra_inputs: Tuple[InputKey, ...] = ()) -> EvaluationBinding:
    """Bind metadata only after I6.1 says READY; no result is created."""
    if (type(evaluation_ref) is not Ref or type(capability) is not Capability
            or (currency is not None and type(currency) is not Currency)
            or type(model_version) is not str or not model_version.strip()
            or type(evaluated_at) is not datetime or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None or type(extra_inputs) is not tuple
            or any(type(key) is not InputKey for key in extra_inputs)):
        raise ValueError('Exact evaluation identity, scope, model version and aware time required')
    index = _index(manifest)
    gate = _gate(manifest, capability, currency)
    if gate.status is not GateStatus.READY:
        raise ValueError('Blocked capability cannot acquire an evaluation binding')
    # Only declared dependency shapes can bind. Currently only observed assets
    # can pass a real I6.1 gate; the runway shape prepares future reviewed inputs.
    if capability not in _BASE_INPUTS or currency is None:
        raise ValueError('No approved binding selection for this capability')
    selected = (*(InputKey(kind, currency) for kind in _BASE_INPUTS[capability]), *extra_inputs)
    if len(set(selected)) != len(selected) or any(key not in index for key in selected):
        raise ValueError('Selected inputs must be unique current I6.1 inputs')
    if any(key.currency not in (currency, None) for key in selected):
        raise ValueError('An evaluation cannot silently expand its native currency scope')
    if any(index[key].status is not InputStatus.AVAILABLE for key in selected):
        raise ValueError('Unknown or insufficient inputs cannot support a current evaluation')
    covered = {ref for key in selected for ref in index[key].refs}
    if not set(gate.dependencies).issubset(covered):
        raise ValueError('Evaluation omitted a required gate dependency')
    dependencies = tuple(_fingerprint(key, index[key]) for key in sorted(selected, key=_sort_key))
    binding = EvaluationBinding(evaluation_ref, capability, currency, manifest.version,
                                model_version, evaluated_at, dependencies, '')
    return replace(binding, digest=_binding_digest(binding))


def bind_evaluation(authority: UserWorldAuthority, evaluation_ref: Ref,
                    capability: Capability, currency: Optional[Currency],
                    model_version: str, evaluated_at: datetime,
                    extra_inputs: Tuple[InputKey, ...] = ()) -> EvaluationBinding:
    """Small application API: one fresh authoritative read, no calculation."""
    if not isinstance(authority, UserWorldAuthority):
        raise ValueError('Explicit User authority required')
    return _bind_from_manifest(read_engine_inputs(authority), evaluation_ref, capability,
                               currency, model_version, evaluated_at, extra_inputs)


def _reason(code, key=None, old=(), new=(), detail=''):
    return InvalidationReason(code, key, old, new, detail)


def _stale(binding, version, checks, reasons):
    return EvaluationValidity(Validity.STALE, binding, version, tuple(checks), tuple(reasons))


def validity_from_manifest(binding: EvaluationBinding, manifest: EngineInputManifest,
                           active_model_version: str) -> EvaluationValidity:
    """Compare exact declared inputs, never output equality or world revision alone."""
    if type(binding) is not EvaluationBinding:
        raise ValueError('EvaluationBinding required')
    if type(active_model_version) is not str or not active_model_version.strip():
        return _stale(binding, manifest.version if type(manifest) is EngineInputManifest else None,
                      (), (_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED, detail='active_model_version_missing'),))
    if type(manifest) is not EngineInputManifest or type(manifest.version) is not ReadVersion:
        return _stale(binding, None, (), (_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED, detail='current_read_unavailable'),))
    current = manifest.version
    try:
        if (binding.digest != _binding_digest(binding) or not binding.dependencies
                or any(type(dep) is not InputFingerprint or type(dep.key) is not InputKey
                       for dep in binding.dependencies)
                or len({dep.key for dep in binding.dependencies}) != len(binding.dependencies)):
            raise ValueError('Invalid binding digest/dependencies')
        index = _index(manifest)
    except (ValueError, TypeError, AttributeError):
        return _stale(binding, current, (), (_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED),))
    before = binding.world_version
    if (type(before) is not ReadVersion or before.context.kind is not ContextKind.USER
            or type(before.world_ref) is not Ref or type(current.world_ref) is not Ref
            or before.context != current.context or before.session_generation != current.session_generation
            or before.world_ref.id != current.world_ref.id):
        return _stale(binding, current, (), (_reason(InvalidationCode.AUTHORITATIVE_WORLD_MISMATCH),))
    if current.revision < before.revision:
        return _stale(binding, current, (), (_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED, detail='revision_regressed'),))
    reasons = []
    checks = []
    if active_model_version != binding.model_version:
        reasons.append(_reason(InvalidationCode.MODEL_VERSION_CHANGED))
    try:
        gate = _gate(manifest, binding.capability, binding.currency)
        if gate.status is not GateStatus.READY:
            reasons.append(_reason(InvalidationCode.CAPABILITY_BLOCKED,
                                   detail=','.join(r.code for r in gate.reasons)))
    except (ValueError, TypeError, AttributeError):
        scope_gone = (binding.currency is not None
                      and not any(key.kind is InputKind.CURRENT_ASSETS
                                  and key.currency is binding.currency for key in index))
        if scope_gone:
            reasons.append(_reason(InvalidationCode.CAPABILITY_BLOCKED,
                                   detail='currency_scope_absent'))
        else:
            reasons.append(_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED,
                                   detail='gate_missing'))
    for saved in binding.dependencies:
        item = index.get(saved.key)
        checks.append(DependencyCheck(saved.key, item.refs if item else (),
                                      item.status if item else None,
                                      item.value.precision if item and item.value else None))
        if item is None:
            old_ids = {(ref.record_kind, ref.ref.id) for ref in saved.refs}
            same_identity = tuple(candidate for key, candidate in index.items()
                                  if key.kind is saved.key.kind and key.currency != saved.key.currency
                                  and (key.identity == saved.key.identity if saved.key.identity is not None
                                       else bool(old_ids & {(ref.record_kind, ref.ref.id) for ref in candidate.refs})))
            scope_disappeared = (saved.key.kind is InputKind.CURRENT_ASSETS
                                 and not any(key.kind is InputKind.CURRENT_ASSETS
                                             and key.currency is saved.key.currency for key in index))
            if same_identity or scope_disappeared:
                reasons.append(_reason(InvalidationCode.CURRENCY_SCOPE_CHANGED, saved.key,
                                       saved.refs, tuple(ref for candidate in same_identity for ref in candidate.refs)))
            reasons.append(_reason(InvalidationCode.DEPENDENCY_REMOVED, saved.key, saved.refs))
            continue
        actual = _fingerprint(saved.key, item)
        if actual.digest != saved.digest or actual.refs != saved.refs or actual.status != saved.status:
            reasons.append(_reason(InvalidationCode.DEPENDENCY_CHANGED, saved.key, saved.refs, item.refs))
        if actual.precision != saved.precision:
            reasons.append(_reason(InvalidationCode.PRECISION_CHANGED, saved.key, saved.refs, item.refs))
        if item.status is InputStatus.UNKNOWN:
            reasons.append(_reason(InvalidationCode.DEPENDENCY_BECAME_UNKNOWN, saved.key, saved.refs, item.refs))
            reasons.append(_reason(InvalidationCode.REQUIRED_INPUT_UNAVAILABLE, saved.key, saved.refs, item.refs))
        elif item.status is not InputStatus.AVAILABLE:
            reasons.append(_reason(InvalidationCode.REQUIRED_INPUT_UNAVAILABLE, saved.key, saved.refs, item.refs))
        if saved.key.kind is InputKind.EMERGENCY_LIQUIDITY and actual.digest != saved.digest:
            reasons.append(_reason(InvalidationCode.POLICY_INPUT_CHANGED, saved.key, saved.refs, item.refs))
    if reasons:
        return _stale(binding, current, checks, reasons)
    return EvaluationValidity(Validity.CURRENT, binding, current, tuple(checks), ())


def read_evaluation_validity(authority: UserWorldAuthority, binding: EvaluationBinding,
                             active_model_version: str) -> EvaluationValidity:
    """One current I1 read; source failure means validity cannot be proved."""
    if not isinstance(authority, UserWorldAuthority) or type(binding) is not EvaluationBinding:
        raise ValueError('Explicit User authority and EvaluationBinding required')
    try:
        manifest = read_engine_inputs(authority)
    except Exception:
        return _stale(binding, None, (), (_reason(InvalidationCode.BINDING_CANNOT_BE_VERIFIED, detail='user_read_failed'),))
    return validity_from_manifest(binding, manifest, active_model_version)
