"""Closed, versioned Slice D transport; decoding does not grant authority."""

from dataclasses import fields
from datetime import datetime
from enum import Enum
import hashlib
import json

from .domain import Currency
from . import world_commands as protocol
from .world_domain import serialize_world_asset, deserialize_world_asset
from .world_planning import serialize_world_planning, deserialize_world_planning
from .world_values import serialize_world_value, deserialize_world_value
from .world_settlement import BalanceBasis, BalanceCoverage, BalanceEffect, SettleOccurrence, SettlementReconciliation

SETTLEMENT_SCHEMA = 'finvis.world-settlement.v0.1'
EXPANDED_SETTLEMENT_SCHEMA = 'finvis.world-settlement.v0.2'
SETTLEMENT_TYPES = {kind.__name__: kind for kind in (
    BalanceCoverage, BalanceEffect, SettleOccurrence, SettlementReconciliation,
)}

SCHEMA = 'finvis.world-mutation.v0.1'
ADOPTION_SCHEMA = 'finvis.world-mutation.v0.2'
HISTORY_SCHEMA = 'finvis.world-history-action.v0.1'
TYPES = {kind.__name__: kind for kind in (
    protocol.MonthlyEssentialOutflow,
    protocol.LegacyPlanAnchor, protocol.ReconciliationIssue, protocol.ReplacementScope, protocol.PutRecord,
    protocol.RetireRecord, protocol.CandidateChangeSet, protocol.FinancialWorld,
    protocol.RecordChange, protocol.MutationPreview, protocol.WorldAuthorization,
    protocol.FinancialWorldCommand, protocol.WorldMutationEvent, protocol.WorldSnapshot,
)}
ENUMS = {kind.__name__: kind for kind in (Currency, protocol.ChangeMode, protocol.IssueDisposition)}


def _encode(value):
    if type(value) is protocol.HistoryAction:
        return {'schema_version': HISTORY_SCHEMA, 'record_type': 'HistoryAction',
                'value': {field.name: _encode(getattr(value, field.name)) for field in fields(value)}}
    if type(value) in SETTLEMENT_TYPES.values():
        expanded = type(value) is SettleOccurrence and value.complete_plan
        return {'schema_version': EXPANDED_SETTLEMENT_SCHEMA if expanded else SETTLEMENT_SCHEMA,
                'record_type': type(value).__name__,
                'value': {field.name: _encode(getattr(value, field.name)) for field in fields(value)
                          if not (type(value) is SettleOccurrence and field.name == 'complete_plan' and not expanded)}}
    if type(value) is BalanceBasis:
        return {'schema_version': SETTLEMENT_SCHEMA, 'basis': value.value}
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is tuple:
        return [_encode(item) for item in value]
    if type(value) is datetime:
        return {'datetime': value.isoformat()}
    if isinstance(value, Enum) and type(value).__name__ in ENUMS:
        return {'enum': type(value).__name__, 'value': value.value}
    if type(value) in TYPES.values():
        expanded = type(value) is protocol.CandidateChangeSet and value.future_origin is not None
        return {'schema_version': ADOPTION_SCHEMA if expanded else SCHEMA, 'record_type': type(value).__name__,
                'value': {field.name: _encode(getattr(value, field.name)) for field in fields(value)
                          if not (type(value) is protocol.CandidateChangeSet and field.name == 'future_origin' and not expanded)}}
    for serializer in (serialize_world_value, serialize_world_asset, serialize_world_planning):
        try:
            return serializer(value)
        except TypeError:
            pass
    raise ValueError('Unsupported mutation value')


def _decode(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is list:
        return tuple(_decode(item) for item in value)
    if type(value) is not dict:
        raise ValueError('Unsupported JSON value')
    if set(value) == {'datetime'}:
        return datetime.fromisoformat(value['datetime'])
    if set(value) == {'enum', 'value'}:
        if type(value['enum']) is not str or value['enum'] not in ENUMS:
            raise ValueError('Unknown enum')
        return ENUMS[value['enum']](value['value'])
    schema = value.get('schema_version')
    if schema == HISTORY_SCHEMA:
        if set(value) != {'schema_version', 'record_type', 'value'} or value['record_type'] != 'HistoryAction':
            raise ValueError('Invalid history action envelope')
        data = value['value']
        if type(data) is not dict or set(data) != {field.name for field in fields(protocol.HistoryAction)}:
            raise ValueError('Invalid history action fields')
        return protocol.HistoryAction(**{name: _decode(item) for name, item in data.items()})
    if schema in (SETTLEMENT_SCHEMA, EXPANDED_SETTLEMENT_SCHEMA):
        if schema == SETTLEMENT_SCHEMA and set(value) == {'schema_version', 'basis'}:
            return BalanceBasis(value['basis'])
        if set(value) != {'schema_version', 'record_type', 'value'} or type(value['record_type']) is not str:
            raise ValueError('Invalid settlement envelope')
        kind = SETTLEMENT_TYPES.get(value['record_type'])
        data = value['value']
        expected = {field.name for field in fields(kind)} if kind else set()
        if kind is SettleOccurrence and schema == SETTLEMENT_SCHEMA: expected.remove('complete_plan')
        if (kind is None or type(data) is not dict or set(data) != expected
                or schema == EXPANDED_SETTLEMENT_SCHEMA and (kind is not SettleOccurrence or data.get('complete_plan') is not True)):
            raise ValueError('Invalid settlement schema fields/type')
        return kind(**{name: _decode(item) for name, item in data.items()})
    for expected, decoder in (
        ('finvis.world-values.v0.1', deserialize_world_value),
        ('finvis.world-assets.v0.1', deserialize_world_asset),
        ('finvis.world-planning.v0.1', deserialize_world_planning),
        ('finvis.world-planning.v0.2', deserialize_world_planning),
    ):
        if schema == expected:
            return decoder(value)
    if schema not in (SCHEMA, ADOPTION_SCHEMA) or set(value) != {'schema_version', 'record_type', 'value'}:
        raise ValueError('Unknown mutation schema/envelope')
    name, data = value['record_type'], value['value']
    if type(name) is not str or name not in TYPES:
        raise ValueError('Unknown mutation record type')
    kind = TYPES[name]
    expected = {field.name for field in fields(kind)}
    if kind is protocol.CandidateChangeSet and schema == SCHEMA: expected.remove('future_origin')
    if (type(data) is not dict or set(data) != expected or
            schema == ADOPTION_SCHEMA and (kind is not protocol.CandidateChangeSet or type(data.get('future_origin')) is not str)):
        raise ValueError('Mutation fields must match exact schema')
    return kind(**{name: _decode(item) for name, item in data.items()})


def serialize_world_mutation(value):
    if type(value) not in (*TYPES.values(), *SETTLEMENT_TYPES.values()):
        raise ValueError('Expected Slice D record')
    return _encode(value)


def deserialize_world_mutation(value):
    try:
        result = _decode(value)
        if type(result) not in (*TYPES.values(), *SETTLEMENT_TYPES.values()):
            raise ValueError('Expected Slice D envelope')
        return result
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError('Invalid mutation payload') from error


def dumps_world_mutation(value):
    return json.dumps(serialize_world_mutation(value), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def loads_world_mutation(payload):
    if type(payload) is not str:
        raise ValueError('JSON must be text')

    def strict_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result

    def reject_number(value):
        raise ValueError('Floats and nonfinite numbers are unsupported')

    return deserialize_world_mutation(json.loads(payload, object_pairs_hook=strict_pairs,
                                                parse_float=reject_number, parse_constant=reject_number))


def payload_digest(value):
    return hashlib.sha256(dumps_world_mutation(value).encode('utf-8')).hexdigest()
