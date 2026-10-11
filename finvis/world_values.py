"""Immutable value contracts for Financial World Core Expansion Slice A.

This module is additive.  It does not alter the legacy Phase 2 records or
serializer, and it contains no account, plan, command, or mutation behavior.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from typing import Any, Dict, Mapping, Optional, Tuple

from .domain import (
    Confidence,
    ConfidenceAssessment,
    Currency,
    Money,
    RealitySource,
    Ref,
    aware,
    enum_value,
    nonempty,
    typed_tuple,
    whole,
)


WORLD_VALUE_SCHEMA_VERSION = "finvis.world-values.v0.1"


@dataclass(frozen=True)
class TypedRef:
    """A record-kind-qualified wrapper around the existing versioned Ref."""

    record_kind: str
    ref: Ref

    def __post_init__(self) -> None:
        nonempty(self.record_kind, "record_kind")
        if not isinstance(self.ref, Ref):
            raise ValueError("typed reference requires Ref")


def money_from_major_units(value: str, currency: Currency) -> Money:
    """Convert an exact decimal string to minor units without rounding."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("major-unit value must be an exact nonempty decimal string")
    enum_value(currency, Currency)
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("major-unit value must be a valid decimal string") from error
    if not decimal_value.is_finite():
        raise ValueError("major-unit value must be finite")
    minor_units = decimal_value * Decimal(100)
    if minor_units != minor_units.to_integral_value():
        raise ValueError("major-unit value has unsupported fractional minor units")
    return Money(int(minor_units), currency)


class AmountPrecision(Enum):
    EXACT_REPORTED = "exact_reported"
    APPROXIMATE = "approximate"
    RANGE = "range"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AmountEstimate:
    """Closed amount union that preserves precision rather than selecting a scalar."""

    precision: AmountPrecision
    amount: Optional[Money] = None
    lower_bound: Optional[Money] = None
    upper_bound: Optional[Money] = None
    unknown_currency: Optional[Currency] = None
    missing_reason: Optional[str] = None

    def __post_init__(self) -> None:
        enum_value(self.precision, AmountPrecision)
        monies = tuple(
            value
            for value in (self.amount, self.lower_bound, self.upper_bound)
            if value is not None
        )
        if any(not isinstance(value, Money) for value in monies):
            raise ValueError("amount estimate values must be Money")
        if any(value.minor_units < 0 for value in monies):
            raise ValueError("amount estimate values must be nonnegative")

        if self.precision is AmountPrecision.EXACT_REPORTED:
            if (
                self.amount is None
                or self.lower_bound is not None
                or self.upper_bound is not None
                or self.unknown_currency is not None
                or self.missing_reason is not None
            ):
                raise ValueError("exact_reported requires exactly one amount")
            return

        if self.precision is AmountPrecision.APPROXIMATE:
            if self.amount is None or self.unknown_currency is not None or self.missing_reason is not None:
                raise ValueError("approximate requires a nominal amount")
            if (self.lower_bound is None) is not (self.upper_bound is None):
                raise ValueError("approximate bounds must be supplied together")
            if self.lower_bound is not None:
                self._validate_bounds(self.lower_bound, self.upper_bound)
                if not self.lower_bound.minor_units <= self.amount.minor_units <= self.upper_bound.minor_units:
                    raise ValueError("approximate nominal amount must lie within explicit bounds")
                self.amount._check(self.lower_bound)
            return

        if self.precision is AmountPrecision.RANGE:
            if (
                self.amount is not None
                or self.lower_bound is None
                or self.upper_bound is None
                or self.unknown_currency is not None
                or self.missing_reason is not None
            ):
                raise ValueError("range requires only lower and upper bounds")
            self._validate_bounds(self.lower_bound, self.upper_bound)
            return

        if (
            self.amount is not None
            or self.lower_bound is not None
            or self.upper_bound is not None
            or self.missing_reason is None
        ):
            raise ValueError("unknown requires a missing reason and no scalar")
        if self.unknown_currency is not None:
            enum_value(self.unknown_currency, Currency)
        nonempty(self.missing_reason, "missing_reason")

    @staticmethod
    def _validate_bounds(lower: Money, upper: Money) -> None:
        lower._check(upper)
        if lower.minor_units > upper.minor_units:
            raise ValueError("amount range lower bound must not exceed upper bound")

    @classmethod
    def exact_reported(cls, amount: Money) -> "AmountEstimate":
        return cls(AmountPrecision.EXACT_REPORTED, amount=amount)

    @classmethod
    def approximate(
        cls,
        nominal: Money,
        lower_bound: Optional[Money] = None,
        upper_bound: Optional[Money] = None,
    ) -> "AmountEstimate":
        return cls(
            AmountPrecision.APPROXIMATE,
            amount=nominal,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

    @classmethod
    def range(cls, lower_bound: Money, upper_bound: Money) -> "AmountEstimate":
        return cls(AmountPrecision.RANGE, lower_bound=lower_bound, upper_bound=upper_bound)

    @classmethod
    def unknown(cls, missing_reason: str, currency: Optional[Currency] = None) -> "AmountEstimate":
        return cls(
            AmountPrecision.UNKNOWN,
            unknown_currency=currency,
            missing_reason=missing_reason,
        )

    @property
    def currency(self) -> Optional[Currency]:
        if self.amount is not None:
            return self.amount.currency
        if self.lower_bound is not None:
            return self.lower_bound.currency
        return self.unknown_currency


class TemporalKind(Enum):
    KNOWN_DATE = "known_date"
    DATE_WINDOW = "date_window"
    RELATIVE = "relative"
    UNKNOWN = "unknown"


class TemporalAnchorKind(Enum):
    ENTITY_ID = "entity_id"
    LITERAL = "literal"


class TemporalRelation(Enum):
    BEFORE = "before"
    AFTER = "after"
    AT = "at"


class TemporalOffsetUnit(Enum):
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


@dataclass(frozen=True)
class TemporalOffset:
    quantity: int
    unit: TemporalOffsetUnit

    def __post_init__(self) -> None:
        whole(self.quantity, "temporal offset quantity", 1)
        enum_value(self.unit, TemporalOffsetUnit)


@dataclass(frozen=True)
class TemporalSpec:
    """Tagged calendar semantics; it never resolves relative or unknown time."""

    kind: TemporalKind
    date_value: Optional[date] = None
    window_start: Optional[date] = None
    window_end: Optional[date] = None
    anchor_kind: Optional[TemporalAnchorKind] = None
    anchor: Optional[str] = None
    relation: Optional[TemporalRelation] = None
    offset: Optional[TemporalOffset] = None
    reason: Optional[str] = None
    source_text: Optional[str] = None

    def __post_init__(self) -> None:
        enum_value(self.kind, TemporalKind)
        if self.source_text is not None:
            nonempty(self.source_text, "source_text")

        if self.kind is TemporalKind.KNOWN_DATE:
            if type(self.date_value) is not date or self._other_fields("date_value"):
                raise ValueError("known_date requires exactly one calendar date")
            return

        if self.kind is TemporalKind.DATE_WINDOW:
            if (
                type(self.window_start) is not date
                or type(self.window_end) is not date
                or self._other_fields("window_start", "window_end")
            ):
                raise ValueError("date_window requires exactly two calendar dates")
            if self.window_start > self.window_end:
                raise ValueError("date window start must not exceed end")
            return

        if self.kind is TemporalKind.RELATIVE:
            if (
                self.anchor_kind is None
                or self.anchor is None
                or self.relation is None
                or self.source_text is None
                or self._other_fields("anchor_kind", "anchor", "relation", "offset")
            ):
                raise ValueError("relative time requires anchor, relation, and original source text")
            enum_value(self.anchor_kind, TemporalAnchorKind)
            enum_value(self.relation, TemporalRelation)
            nonempty(self.anchor, "relative anchor")
            if self.offset is not None and not isinstance(self.offset, TemporalOffset):
                raise ValueError("relative offset must be TemporalOffset")
            return

        if self.reason is None or self._other_fields("reason"):
            raise ValueError("unknown time requires only a reason")
        nonempty(self.reason, "unknown time reason")

    def _other_fields(self, *allowed: str) -> bool:
        fields = {
            "date_value": self.date_value,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "anchor_kind": self.anchor_kind,
            "anchor": self.anchor,
            "relation": self.relation,
            "offset": self.offset,
            "reason": self.reason,
        }
        return any(value is not None for name, value in fields.items() if name not in allowed)

    @classmethod
    def known_date(cls, value: date, source_text: Optional[str] = None) -> "TemporalSpec":
        return cls(TemporalKind.KNOWN_DATE, date_value=value, source_text=source_text)

    @classmethod
    def date_window(
        cls,
        start: date,
        end: date,
        source_text: Optional[str] = None,
    ) -> "TemporalSpec":
        return cls(
            TemporalKind.DATE_WINDOW,
            window_start=start,
            window_end=end,
            source_text=source_text,
        )

    @classmethod
    def relative(
        cls,
        anchor: str,
        relation: TemporalRelation,
        source_text: str,
        anchor_kind: TemporalAnchorKind = TemporalAnchorKind.LITERAL,
        offset: Optional[TemporalOffset] = None,
    ) -> "TemporalSpec":
        return cls(
            TemporalKind.RELATIVE,
            anchor_kind=anchor_kind,
            anchor=anchor,
            relation=relation,
            offset=offset,
            source_text=source_text,
        )

    @classmethod
    def unknown(cls, reason: str, source_text: Optional[str] = None) -> "TemporalSpec":
        return cls(TemporalKind.UNKNOWN, reason=reason, source_text=source_text)


class ObservationRole(Enum):
    CURRENT = "current"
    FUTURE = "future"
    PLANNED = "planned"
    CONTEMPLATED = "contemplated"
    ACTUAL = "actual"


class FreshnessStatus(Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FreshnessAssessment:
    status: FreshnessStatus
    rationale: str
    assessed_at: datetime

    def __post_init__(self) -> None:
        enum_value(self.status, FreshnessStatus)
        nonempty(self.rationale, "freshness rationale")
        aware(self.assessed_at, "freshness assessed_at")


@dataclass(frozen=True)
class ObservationMetadata:
    """Minimal provenance and time metadata, separate from the observed value."""

    source: RealitySource
    evidence_refs: Tuple[Ref, ...]
    assumption_refs: Tuple[Ref, ...]
    observed_at: Optional[datetime]
    as_of: TemporalSpec
    recorded_at: datetime
    role: ObservationRole
    confidence: Confidence
    freshness: FreshnessAssessment
    corrects_ref: Optional[Ref] = None
    correction_reason: Optional[str] = None

    def __post_init__(self) -> None:
        enum_value(self.source, RealitySource)
        typed_tuple(self.evidence_refs, Ref, "evidence_refs")
        typed_tuple(self.assumption_refs, Ref, "assumption_refs")
        if not self.evidence_refs and not self.assumption_refs:
            raise ValueError("observation metadata needs evidence or an explicit assumption")
        if self.observed_at is not None:
            aware(self.observed_at, "observed_at")
        if not isinstance(self.as_of, TemporalSpec):
            raise ValueError("as_of must be TemporalSpec")
        aware(self.recorded_at, "recorded_at")
        enum_value(self.role, ObservationRole)
        if not isinstance(self.confidence, Confidence):
            raise ValueError("confidence must be Confidence")
        if not isinstance(self.freshness, FreshnessAssessment):
            raise ValueError("freshness must be FreshnessAssessment")
        if self.corrects_ref is not None and not isinstance(self.corrects_ref, Ref):
            raise ValueError("corrects_ref must be Ref")
        if (self.corrects_ref is None) is not (self.correction_reason is None):
            raise ValueError("correction reference and reason must be supplied together")
        if self.correction_reason is not None:
            nonempty(self.correction_reason, "correction_reason")


def _money_data(value: Money) -> Dict[str, Any]:
    return {"minor_units": value.minor_units, "currency": value.currency.value}


def _money_from_data(data: Any) -> Money:
    data = _object(data, {"minor_units", "currency"}, "money")
    return Money(_integer(data["minor_units"], "minor_units"), Currency(data["currency"]))


def _ref_data(value: Ref) -> Dict[str, Any]:
    return {"id": value.id, "version": value.version}


def _ref_from_data(data: Any) -> Ref:
    data = _object(data, {"id", "version"}, "ref")
    return Ref(data["id"], _integer(data["version"], "version"))


def _typed_ref_data(value: TypedRef) -> Dict[str, Any]:
    return {"record_kind": value.record_kind, "ref": _ref_data(value.ref)}


def _typed_ref_from_data(data: Any) -> TypedRef:
    data = _object(data, {"record_kind", "ref"}, "typed ref")
    return TypedRef(data["record_kind"], _ref_from_data(data["ref"]))


def _confidence_data(value: Confidence) -> Dict[str, Any]:
    return {"assessment": value.assessment.value, "rationale": value.rationale}


def _confidence_from_data(data: Any) -> Confidence:
    data = _object(data, {"assessment", "rationale"}, "confidence")
    return Confidence(ConfidenceAssessment(data["assessment"]), data["rationale"])


def _offset_data(value: TemporalOffset) -> Dict[str, Any]:
    return {"quantity": value.quantity, "unit": value.unit.value}


def _offset_from_data(data: Any) -> TemporalOffset:
    data = _object(data, {"quantity", "unit"}, "temporal offset")
    return TemporalOffset(_integer(data["quantity"], "quantity"), TemporalOffsetUnit(data["unit"]))


def _temporal_data(value: TemporalSpec) -> Dict[str, Any]:
    if value.kind is TemporalKind.KNOWN_DATE:
        return {
            "kind": value.kind.value,
            "date": value.date_value.isoformat(),
            "source_text": value.source_text,
        }
    if value.kind is TemporalKind.DATE_WINDOW:
        return {
            "kind": value.kind.value,
            "start": value.window_start.isoformat(),
            "end": value.window_end.isoformat(),
            "source_text": value.source_text,
        }
    if value.kind is TemporalKind.RELATIVE:
        return {
            "kind": value.kind.value,
            "anchor_kind": value.anchor_kind.value,
            "anchor": value.anchor,
            "relation": value.relation.value,
            "offset": None if value.offset is None else _offset_data(value.offset),
            "source_text": value.source_text,
        }
    return {"kind": value.kind.value, "reason": value.reason, "source_text": value.source_text}


def _temporal_from_data(data: Any) -> TemporalSpec:
    if not isinstance(data, dict) or "kind" not in data:
        raise ValueError("temporal value must be an object with kind")
    kind = TemporalKind(data["kind"])
    if kind is TemporalKind.KNOWN_DATE:
        data = _object(data, {"kind", "date", "source_text"}, "known date")
        return TemporalSpec.known_date(_date(data["date"]), _optional_text(data["source_text"]))
    if kind is TemporalKind.DATE_WINDOW:
        data = _object(data, {"kind", "start", "end", "source_text"}, "date window")
        return TemporalSpec.date_window(
            _date(data["start"]),
            _date(data["end"]),
            _optional_text(data["source_text"]),
        )
    if kind is TemporalKind.RELATIVE:
        data = _object(
            data,
            {"kind", "anchor_kind", "anchor", "relation", "offset", "source_text"},
            "relative time",
        )
        return TemporalSpec.relative(
            data["anchor"],
            TemporalRelation(data["relation"]),
            data["source_text"],
            TemporalAnchorKind(data["anchor_kind"]),
            None if data["offset"] is None else _offset_from_data(data["offset"]),
        )
    data = _object(data, {"kind", "reason", "source_text"}, "unknown time")
    return TemporalSpec.unknown(data["reason"], _optional_text(data["source_text"]))


def _amount_data(value: AmountEstimate) -> Dict[str, Any]:
    if value.precision is AmountPrecision.EXACT_REPORTED:
        return {"precision": value.precision.value, "amount": _money_data(value.amount)}
    if value.precision is AmountPrecision.APPROXIMATE:
        bounds = None
        if value.lower_bound is not None:
            bounds = {
                "lower": _money_data(value.lower_bound),
                "upper": _money_data(value.upper_bound),
            }
        return {
            "precision": value.precision.value,
            "nominal": _money_data(value.amount),
            "bounds": bounds,
        }
    if value.precision is AmountPrecision.RANGE:
        return {
            "precision": value.precision.value,
            "lower": _money_data(value.lower_bound),
            "upper": _money_data(value.upper_bound),
        }
    return {
        "precision": value.precision.value,
        "currency": None if value.unknown_currency is None else value.unknown_currency.value,
        "missing_reason": value.missing_reason,
    }


def _amount_from_data(data: Any) -> AmountEstimate:
    if not isinstance(data, dict) or "precision" not in data:
        raise ValueError("amount estimate must be an object with precision")
    precision = AmountPrecision(data["precision"])
    if precision is AmountPrecision.EXACT_REPORTED:
        data = _object(data, {"precision", "amount"}, "exact amount")
        return AmountEstimate.exact_reported(_money_from_data(data["amount"]))
    if precision is AmountPrecision.APPROXIMATE:
        data = _object(data, {"precision", "nominal", "bounds"}, "approximate amount")
        if data["bounds"] is None:
            return AmountEstimate.approximate(_money_from_data(data["nominal"]))
        bounds = _object(data["bounds"], {"lower", "upper"}, "approximate bounds")
        return AmountEstimate.approximate(
            _money_from_data(data["nominal"]),
            _money_from_data(bounds["lower"]),
            _money_from_data(bounds["upper"]),
        )
    if precision is AmountPrecision.RANGE:
        data = _object(data, {"precision", "lower", "upper"}, "range amount")
        return AmountEstimate.range(_money_from_data(data["lower"]), _money_from_data(data["upper"]))
    data = _object(data, {"precision", "currency", "missing_reason"}, "unknown amount")
    currency = None if data["currency"] is None else Currency(data["currency"])
    return AmountEstimate.unknown(data["missing_reason"], currency)


def _freshness_data(value: FreshnessAssessment) -> Dict[str, Any]:
    return {
        "status": value.status.value,
        "rationale": value.rationale,
        "assessed_at": value.assessed_at.isoformat(),
    }


def _freshness_from_data(data: Any) -> FreshnessAssessment:
    data = _object(data, {"status", "rationale", "assessed_at"}, "freshness")
    return FreshnessAssessment(
        FreshnessStatus(data["status"]),
        data["rationale"],
        _datetime(data["assessed_at"]),
    )


def _observation_data(value: ObservationMetadata) -> Dict[str, Any]:
    return {
        "source": value.source.value,
        "evidence_refs": [_ref_data(ref) for ref in value.evidence_refs],
        "assumption_refs": [_ref_data(ref) for ref in value.assumption_refs],
        "observed_at": None if value.observed_at is None else value.observed_at.isoformat(),
        "as_of": _temporal_data(value.as_of),
        "recorded_at": value.recorded_at.isoformat(),
        "role": value.role.value,
        "confidence": _confidence_data(value.confidence),
        "freshness": _freshness_data(value.freshness),
        "corrects_ref": None if value.corrects_ref is None else _ref_data(value.corrects_ref),
        "correction_reason": value.correction_reason,
    }


def _observation_from_data(data: Any) -> ObservationMetadata:
    keys = {
        "source",
        "evidence_refs",
        "assumption_refs",
        "observed_at",
        "as_of",
        "recorded_at",
        "role",
        "confidence",
        "freshness",
        "corrects_ref",
        "correction_reason",
    }
    data = _object(data, keys, "observation metadata")
    evidence = _list(data["evidence_refs"], "evidence_refs")
    assumptions = _list(data["assumption_refs"], "assumption_refs")
    return ObservationMetadata(
        RealitySource(data["source"]),
        tuple(_ref_from_data(item) for item in evidence),
        tuple(_ref_from_data(item) for item in assumptions),
        None if data["observed_at"] is None else _datetime(data["observed_at"]),
        _temporal_from_data(data["as_of"]),
        _datetime(data["recorded_at"]),
        ObservationRole(data["role"]),
        _confidence_from_data(data["confidence"]),
        _freshness_from_data(data["freshness"]),
        None if data["corrects_ref"] is None else _ref_from_data(data["corrects_ref"]),
        _optional_text(data["correction_reason"]),
    )


def serialize_world_value(value: Any) -> Dict[str, Any]:
    """Return a versioned deterministic data envelope for one Slice A value."""

    encoders = (
        (AmountEstimate, "amount_estimate", _amount_data),
        (TemporalSpec, "temporal_spec", _temporal_data),
        (TemporalOffset, "temporal_offset", _offset_data),
        (FreshnessAssessment, "freshness_assessment", _freshness_data),
        (ObservationMetadata, "observation_metadata", _observation_data),
        (TypedRef, "typed_ref", _typed_ref_data),
        (Money, "money", _money_data),
        (Confidence, "confidence", _confidence_data),
        (Ref, "ref", _ref_data),
    )
    for value_type, type_name, encoder in encoders:
        if isinstance(value, value_type):
            return {
                "schema_version": WORLD_VALUE_SCHEMA_VERSION,
                "value_type": type_name,
                "value": encoder(value),
            }
    raise TypeError(f"Unsupported Slice A value: {type(value)}")


def deserialize_world_value(data: Mapping[str, Any]) -> Any:
    """Parse a strict Slice A envelope; unknown schema/type/fields fail closed."""

    envelope = _object(data, {"schema_version", "value_type", "value"}, "world value envelope")
    if envelope["schema_version"] != WORLD_VALUE_SCHEMA_VERSION:
        raise ValueError("unsupported world value schema version")
    decoders = {
        "amount_estimate": _amount_from_data,
        "temporal_spec": _temporal_from_data,
        "temporal_offset": _offset_from_data,
        "freshness_assessment": _freshness_from_data,
        "observation_metadata": _observation_from_data,
        "typed_ref": _typed_ref_from_data,
        "money": _money_from_data,
        "confidence": _confidence_from_data,
        "ref": _ref_from_data,
    }
    decoder = decoders.get(envelope["value_type"])
    if decoder is None:
        raise ValueError("unsupported world value type")
    return decoder(envelope["value"])


def dumps_world_value(value: Any) -> str:
    """Canonical compact JSON: Unicode preserved and object keys sorted."""

    return json.dumps(
        serialize_world_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def loads_world_value(payload: str) -> Any:
    if not isinstance(payload, str):
        raise ValueError("world value JSON must be text")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not supported: {value}")

    try:
        data = json.loads(payload, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("invalid world value JSON") from error
    return deserialize_world_value(data)


def _object(data: Any, keys: set, name: str) -> Dict[str, Any]:
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(keys))}")
    return data


def _integer(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text must be text or null")
    return value


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("date must be an ISO string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("date must be a valid ISO calendar date") from error
    return parsed


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("datetime must be a valid ISO timestamp") from error
    aware(parsed, "datetime")
    return parsed
