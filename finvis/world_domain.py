"""Current-asset records and conservation rules for Financial World Slice B.

The module is additive and deliberately stops before future inflows, expenses,
occurrences, decisions, commands, persistence, or a FinancialWorld root.
"""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .domain import Currency, Money, Ref, enum_value, nonempty, typed_tuple
from .world_values import (
    AmountEstimate,
    AmountPrecision,
    ObservationMetadata,
    ObservationRole,
    TypedRef,
    deserialize_world_value,
    serialize_world_value,
)


WORLD_ASSET_SCHEMA_VERSION = "finvis.world-assets.v0.1"


class WorldRecordKind(Enum):
    ACCOUNT = "account"
    ASSET_POSITION = "asset_position"
    EARMARK = "earmark"
    WORLD_STATE = "world_state"


class RecordLifecycle(Enum):
    ACTIVE = "active"
    RETIRED = "retired"


class AccountKind(Enum):
    CASH_CONTAINER = "cash_container"


class AssetPositionKind(Enum):
    CASH = "cash"


class AccessStatus(Enum):
    ACCESSIBLE = "accessible"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class EarmarkRole(Enum):
    EMERGENCY = "emergency"
    GOAL = "goal"
    PURCHASE = "purchase"
    OTHER = "other"


class EarmarkLifecycle(Enum):
    ACTIVE = "active"
    RELEASED = "released"


@dataclass(frozen=True)
class AccessAssessment:
    status: AccessStatus
    rationale: str
    observation: ObservationMetadata

    def __post_init__(self) -> None:
        enum_value(self.status, AccessStatus)
        nonempty(self.rationale, "access rationale")
        _current_observation(self.observation, "access observation")


@dataclass(frozen=True)
class Account:
    """Owned location/container identity. It intentionally has no balance."""

    ref: Ref
    owner_id: str
    world_id: str
    label: str
    institution_label: Optional[str]
    kind: AccountKind
    observation: ObservationMetadata
    lifecycle: RecordLifecycle = RecordLifecycle.ACTIVE

    def __post_init__(self) -> None:
        _entity_identity(self.ref, self.owner_id, self.world_id)
        nonempty(self.label, "account label")
        if self.institution_label is not None:
            nonempty(self.institution_label, "institution_label")
        enum_value(self.kind, AccountKind)
        _current_observation(self.observation, "account observation")
        enum_value(self.lifecycle, RecordLifecycle)


@dataclass(frozen=True)
class AssetPosition:
    """One current owned monetary pool; the balance is the asset truth."""

    ref: Ref
    owner_id: str
    world_id: str
    account_ref: TypedRef
    currency: Currency
    kind: AssetPositionKind
    partition_key: str
    balance: AmountEstimate
    observation: ObservationMetadata
    access: AccessAssessment
    lifecycle: RecordLifecycle = RecordLifecycle.ACTIVE

    def __post_init__(self) -> None:
        _entity_identity(self.ref, self.owner_id, self.world_id)
        _typed_kind(self.account_ref, WorldRecordKind.ACCOUNT)
        enum_value(self.currency, Currency)
        enum_value(self.kind, AssetPositionKind)
        nonempty(self.partition_key, "partition_key")
        _estimate_currency(self.balance, self.currency, "position balance")
        _current_observation(self.observation, "position observation")
        if not isinstance(self.access, AccessAssessment):
            raise ValueError("position access must be AccessAssessment")
        enum_value(self.lifecycle, RecordLifecycle)


@dataclass(frozen=True)
class Earmark:
    """A purpose allocation over an existing position, never another asset."""

    ref: Ref
    owner_id: str
    world_id: str
    position_ref: TypedRef
    allocation: AmountEstimate
    purpose: str
    role: EarmarkRole
    emergency_use_designated: bool
    observation: ObservationMetadata
    lifecycle: EarmarkLifecycle = EarmarkLifecycle.ACTIVE

    def __post_init__(self) -> None:
        _entity_identity(self.ref, self.owner_id, self.world_id)
        _typed_kind(self.position_ref, WorldRecordKind.ASSET_POSITION)
        if not isinstance(self.allocation, AmountEstimate):
            raise ValueError("earmark allocation must be AmountEstimate")
        nonempty(self.purpose, "earmark purpose")
        enum_value(self.role, EarmarkRole)
        if type(self.emergency_use_designated) is not bool:
            raise ValueError("emergency_use_designated must be boolean")
        _current_observation(self.observation, "earmark observation")
        enum_value(self.lifecycle, EarmarkLifecycle)


@dataclass(frozen=True)
class WorldState:
    """Current-asset membership by exact typed record version.

    This is not the FinancialWorld root and stores no aggregate balances.
    """

    ref: Ref
    owner_id: str
    world_id: str
    account_refs: Tuple[TypedRef, ...]
    position_refs: Tuple[TypedRef, ...]
    earmark_refs: Tuple[TypedRef, ...]
    snapshot_observation: ObservationMetadata
    coverage_note: str

    def __post_init__(self) -> None:
        _entity_identity(self.ref, self.owner_id, self.world_id)
        _current_observation(self.snapshot_observation, "state snapshot observation")
        nonempty(self.coverage_note, "coverage_note")
        for name, values, kind in (
            ("account_refs", self.account_refs, WorldRecordKind.ACCOUNT),
            ("position_refs", self.position_refs, WorldRecordKind.ASSET_POSITION),
            ("earmark_refs", self.earmark_refs, WorldRecordKind.EARMARK),
        ):
            typed_tuple(values, TypedRef, name)
            for value in values:
                _typed_kind(value, kind)
            _unique_stable_ids(values, name, lambda value: value.ref.id)
            object.__setattr__(self, name, tuple(sorted(values, key=_typed_ref_key)))


@dataclass(frozen=True)
class CurrencyAmount:
    """A derived per-currency value; it is never stored as asset authority."""

    currency: Currency
    amount: AmountEstimate

    def __post_init__(self) -> None:
        enum_value(self.currency, Currency)
        _estimate_currency(self.amount, self.currency, "derived amount")


@dataclass(frozen=True)
class WorldAssetBundle:
    """Validated current records for one WorldState.

    The bundle is an immutable validation/export boundary, not a mutable store or
    FinancialWorld root. WorldState remains a manifest of exact typed refs.
    """

    state: WorldState
    accounts: Tuple[Account, ...]
    positions: Tuple[AssetPosition, ...]
    earmarks: Tuple[Earmark, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, WorldState):
            raise ValueError("bundle state must be WorldState")
        for name, values, item_type in (
            ("accounts", self.accounts, Account),
            ("positions", self.positions, AssetPosition),
            ("earmarks", self.earmarks, Earmark),
        ):
            typed_tuple(values, item_type, name)
            _unique_stable_ids(values, name, lambda value: value.ref.id)
            object.__setattr__(self, name, tuple(sorted(values, key=lambda value: _ref_key(value.ref))))
        self._validate_membership()

    def _validate_membership(self) -> None:
        for record in (*self.accounts, *self.positions, *self.earmarks):
            if record.owner_id != self.state.owner_id or record.world_id != self.state.world_id:
                raise ValueError("all current records must share the state owner and world")

        for account in self.accounts:
            if account.lifecycle is not RecordLifecycle.ACTIVE:
                raise ValueError("current WorldState cannot include a retired account")
        for position in self.positions:
            if position.lifecycle is not RecordLifecycle.ACTIVE:
                raise ValueError("current WorldState cannot include a retired position")
        for earmark in self.earmarks:
            if earmark.lifecycle is not EarmarkLifecycle.ACTIVE:
                raise ValueError("current WorldState cannot include a released earmark")

        expected_accounts = tuple(typed_ref(WorldRecordKind.ACCOUNT, item.ref) for item in self.accounts)
        expected_positions = tuple(typed_ref(WorldRecordKind.ASSET_POSITION, item.ref) for item in self.positions)
        expected_earmarks = tuple(typed_ref(WorldRecordKind.EARMARK, item.ref) for item in self.earmarks)
        if self.state.account_refs != expected_accounts:
            raise ValueError("WorldState account membership does not match supplied records")
        if self.state.position_refs != expected_positions:
            raise ValueError("WorldState position membership does not match supplied records")
        if self.state.earmark_refs != expected_earmarks:
            raise ValueError("WorldState earmark membership does not match supplied records")

        account_index = {item.ref: item for item in self.accounts}
        position_index = {item.ref: item for item in self.positions}
        partitions = set()
        for position in self.positions:
            if position.account_ref.ref not in account_index:
                raise ValueError("asset position references a nonexistent account")
            partition = (position.account_ref.ref.id, position.currency, position.partition_key)
            if partition in partitions:
                raise ValueError("active positions duplicate one account/currency/partition")
            partitions.add(partition)

        allocations: Dict[Ref, list] = {position.ref: [] for position in self.positions}
        for earmark in self.earmarks:
            position = position_index.get(earmark.position_ref.ref)
            if position is None:
                raise ValueError("earmark references a nonexistent asset position")
            _estimate_currency(earmark.allocation, position.currency, "earmark allocation")
            allocations[position.ref].append(earmark.allocation)
        for position in self.positions:
            _validate_allocation_capacity(position.balance, allocations[position.ref], position.ref)

    def current_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return self._group_positions(lambda position: position.balance)

    def earmarked_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        values: Dict[Currency, list] = {position.currency: [] for position in self.positions}
        position_index = {position.ref: position for position in self.positions}
        for earmark in self.earmarks:
            position = position_index[earmark.position_ref.ref]
            values[position.currency].append(earmark.allocation)
        return _currency_amounts(values)

    def unallocated_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        values: Dict[Currency, list] = {}
        for position in self.positions:
            values.setdefault(position.currency, []).append(self.position_unallocated(position.ref))
        return _currency_amounts(values)

    def account_assets_by_currency(self, account_ref: Ref) -> Tuple[CurrencyAmount, ...]:
        if account_ref not in {account.ref for account in self.accounts}:
            raise ValueError("unknown account reference")
        return self._group_positions(
            lambda position: position.balance,
            lambda position: position.account_ref.ref == account_ref,
        )

    def position_earmarked(self, position_ref: Ref) -> AmountEstimate:
        position = self._position(position_ref)
        values = [
            earmark.allocation
            for earmark in self.earmarks
            if earmark.position_ref.ref == position_ref
        ]
        return _sum_estimates(values, position.currency)

    def position_unallocated(self, position_ref: Ref) -> AmountEstimate:
        position = self._position(position_ref)
        allocations = [
            earmark.allocation
            for earmark in self.earmarks
            if earmark.position_ref.ref == position_ref
        ]
        if not allocations:
            return position.balance
        return _subtract_estimates(position.balance, _sum_estimates(allocations, position.currency), position.currency)

    def _position(self, position_ref: Ref) -> AssetPosition:
        for position in self.positions:
            if position.ref == position_ref:
                return position
        raise ValueError("unknown position reference")

    def _group_positions(self, selector, predicate=lambda position: True) -> Tuple[CurrencyAmount, ...]:
        values: Dict[Currency, list] = {}
        for position in self.positions:
            if predicate(position):
                values.setdefault(position.currency, []).append(selector(position))
        return _currency_amounts(values)


def typed_ref(kind: WorldRecordKind, ref: Ref) -> TypedRef:
    enum_value(kind, WorldRecordKind)
    if not isinstance(ref, Ref):
        raise ValueError("typed_ref requires Ref")
    return TypedRef(kind.value, ref)


def amount_for_currency(values: Tuple[CurrencyAmount, ...], currency: Currency) -> AmountEstimate:
    for value in values:
        if value.currency is currency:
            return value.amount
    raise ValueError(f"no derived amount for {currency.value}")


def sum_amount_estimates(values: Iterable[AmountEstimate], currency: Currency) -> AmountEstimate:
    """Public precision-preserving sum for derived same-currency views."""

    enum_value(currency, Currency)
    return _sum_estimates(values, currency)


def _entity_identity(ref: Ref, owner_id: str, world_id: str) -> None:
    if not isinstance(ref, Ref):
        raise ValueError("entity ref must be Ref")
    nonempty(owner_id, "owner_id")
    nonempty(world_id, "world_id")


def _current_observation(value: ObservationMetadata, name: str) -> None:
    if not isinstance(value, ObservationMetadata):
        raise ValueError(f"{name} must be ObservationMetadata")
    if value.role is not ObservationRole.CURRENT:
        raise ValueError(f"{name} must describe current facts")


def _typed_kind(value: TypedRef, kind: WorldRecordKind) -> None:
    if not isinstance(value, TypedRef) or value.record_kind != kind.value:
        raise ValueError(f"expected typed {kind.value} reference")


def _estimate_currency(value: AmountEstimate, currency: Currency, name: str) -> None:
    if not isinstance(value, AmountEstimate):
        raise ValueError(f"{name} must be AmountEstimate")
    if value.currency is not None and value.currency is not currency:
        raise ValueError(f"{name} currency must match {currency.value}")


def _unique_stable_ids(values: Iterable[Any], name: str, identity) -> None:
    seen = set()
    for value in values:
        key = identity(value)
        if key in seen:
            raise ValueError(f"{name} contains duplicate stable ID: {key}")
        seen.add(key)


def _ref_key(value: Ref) -> tuple:
    return value.id, value.version


def _typed_ref_key(value: TypedRef) -> tuple:
    return value.record_kind, value.ref.id, value.ref.version


def _lower(value: AmountEstimate) -> Optional[Money]:
    if value.precision is AmountPrecision.EXACT_REPORTED:
        return value.amount
    if value.precision in (AmountPrecision.APPROXIMATE, AmountPrecision.RANGE):
        return value.lower_bound
    return None


def _upper(value: AmountEstimate) -> Optional[Money]:
    if value.precision is AmountPrecision.EXACT_REPORTED:
        return value.amount
    if value.precision in (AmountPrecision.APPROXIMATE, AmountPrecision.RANGE):
        return value.upper_bound
    return None


def _nominal(value: AmountEstimate) -> Optional[Money]:
    if value.precision in (AmountPrecision.EXACT_REPORTED, AmountPrecision.APPROXIMATE):
        return value.amount
    return None


def _validate_allocation_capacity(
    balance: AmountEstimate,
    allocations: Iterable[AmountEstimate],
    position_ref: Ref,
) -> None:
    allocations = tuple(allocations)
    if not allocations:
        return
    balance_lower = _lower(balance)
    allocation_uppers = tuple(_upper(value) for value in allocations)
    if balance_lower is None or any(value is None for value in allocation_uppers):
        raise ValueError(
            f"allocation conservation is unresolved for {position_ref.id}; supported bounds are required"
        )
    total_upper = sum(value.minor_units for value in allocation_uppers)
    if total_upper > balance_lower.minor_units:
        raise ValueError(
            f"active earmark upper bounds exceed supported owned balance for {position_ref.id}"
        )


def _sum_estimates(values: Iterable[AmountEstimate], currency: Currency) -> AmountEstimate:
    values = tuple(values)
    if not values:
        return AmountEstimate.exact_reported(Money(0, currency))
    for value in values:
        _estimate_currency(value, currency, "derived input")
    if any(value.precision is AmountPrecision.UNKNOWN for value in values):
        return AmountEstimate.unknown("Derived total depends on an unknown amount", currency)

    lowers = tuple(_lower(value) for value in values)
    uppers = tuple(_upper(value) for value in values)
    nominals = tuple(_nominal(value) for value in values)
    all_bounded = all(value is not None for value in lowers + uppers)
    all_nominal = all(value is not None for value in nominals)
    has_range = any(value.precision is AmountPrecision.RANGE for value in values)
    has_approximate = any(value.precision is AmountPrecision.APPROXIMATE for value in values)

    if has_range:
        if not all_bounded:
            return AmountEstimate.unknown("Derived range lacks supported bounds", currency)
        return AmountEstimate.range(
            Money(sum(value.minor_units for value in lowers), currency),
            Money(sum(value.minor_units for value in uppers), currency),
        )
    if has_approximate:
        if not all_nominal:
            return AmountEstimate.unknown("Derived approximate total lacks a nominal amount", currency)
        nominal = Money(sum(value.minor_units for value in nominals), currency)
        if all_bounded:
            return AmountEstimate.approximate(
                nominal,
                Money(sum(value.minor_units for value in lowers), currency),
                Money(sum(value.minor_units for value in uppers), currency),
            )
        return AmountEstimate.approximate(nominal)
    return AmountEstimate.exact_reported(Money(sum(value.amount.minor_units for value in values), currency))


def _subtract_estimates(
    balance: AmountEstimate,
    allocated: AmountEstimate,
    currency: Currency,
) -> AmountEstimate:
    if balance.precision is AmountPrecision.UNKNOWN or allocated.precision is AmountPrecision.UNKNOWN:
        return AmountEstimate.unknown("Unallocated amount depends on an unknown value", currency)
    balance_lower, balance_upper = _lower(balance), _upper(balance)
    allocated_lower, allocated_upper = _lower(allocated), _upper(allocated)
    if None in (balance_lower, balance_upper, allocated_lower, allocated_upper):
        return AmountEstimate.unknown("Unallocated amount lacks supported bounds", currency)
    lower = Money(balance_lower.minor_units - allocated_upper.minor_units, currency)
    upper = Money(balance_upper.minor_units - allocated_lower.minor_units, currency)
    if lower.minor_units < 0:
        raise ValueError("unallocated lower bound cannot be negative")
    if (
        balance.precision is AmountPrecision.EXACT_REPORTED
        and allocated.precision is AmountPrecision.EXACT_REPORTED
    ):
        return AmountEstimate.exact_reported(lower)
    if balance.precision is AmountPrecision.RANGE or allocated.precision is AmountPrecision.RANGE:
        return AmountEstimate.range(lower, upper)
    balance_nominal, allocated_nominal = _nominal(balance), _nominal(allocated)
    if balance_nominal is None or allocated_nominal is None:
        return AmountEstimate.range(lower, upper)
    return AmountEstimate.approximate(
        Money(balance_nominal.minor_units - allocated_nominal.minor_units, currency),
        lower,
        upper,
    )


def _currency_amounts(values: Mapping[Currency, Iterable[AmountEstimate]]) -> Tuple[CurrencyAmount, ...]:
    return tuple(
        CurrencyAmount(currency, _sum_estimates(amounts, currency))
        for currency, amounts in sorted(values.items(), key=lambda item: item[0].value)
    )


def _slice_a(value: Any) -> Dict[str, Any]:
    return serialize_world_value(value)


def _slice_a_value(data: Any, expected_type: type, name: str) -> Any:
    value = deserialize_world_value(data)
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} has wrong Slice A value type")
    return value


def _account_data(value: Account) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref),
        "owner_id": value.owner_id,
        "world_id": value.world_id,
        "label": value.label,
        "institution_label": value.institution_label,
        "kind": value.kind.value,
        "observation": _slice_a(value.observation),
        "lifecycle": value.lifecycle.value,
    }


def _account_from_data(data: Any) -> Account:
    data = _object(data, {"ref", "owner_id", "world_id", "label", "institution_label", "kind", "observation", "lifecycle"}, "account")
    return Account(
        _slice_a_value(data["ref"], Ref, "account ref"),
        data["owner_id"],
        data["world_id"],
        data["label"],
        _optional_text(data["institution_label"]),
        AccountKind(data["kind"]),
        _slice_a_value(data["observation"], ObservationMetadata, "account observation"),
        RecordLifecycle(data["lifecycle"]),
    )


def _access_data(value: AccessAssessment) -> Dict[str, Any]:
    return {"status": value.status.value, "rationale": value.rationale, "observation": _slice_a(value.observation)}


def _access_from_data(data: Any) -> AccessAssessment:
    data = _object(data, {"status", "rationale", "observation"}, "access assessment")
    return AccessAssessment(
        AccessStatus(data["status"]),
        data["rationale"],
        _slice_a_value(data["observation"], ObservationMetadata, "access observation"),
    )


def _position_data(value: AssetPosition) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref),
        "owner_id": value.owner_id,
        "world_id": value.world_id,
        "account_ref": _slice_a(value.account_ref),
        "currency": value.currency.value,
        "kind": value.kind.value,
        "partition_key": value.partition_key,
        "balance": _slice_a(value.balance),
        "observation": _slice_a(value.observation),
        "access": _access_data(value.access),
        "lifecycle": value.lifecycle.value,
    }


def _position_from_data(data: Any) -> AssetPosition:
    keys = {"ref", "owner_id", "world_id", "account_ref", "currency", "kind", "partition_key", "balance", "observation", "access", "lifecycle"}
    data = _object(data, keys, "asset position")
    return AssetPosition(
        _slice_a_value(data["ref"], Ref, "position ref"),
        data["owner_id"],
        data["world_id"],
        _slice_a_value(data["account_ref"], TypedRef, "account ref"),
        Currency(data["currency"]),
        AssetPositionKind(data["kind"]),
        data["partition_key"],
        _slice_a_value(data["balance"], AmountEstimate, "position balance"),
        _slice_a_value(data["observation"], ObservationMetadata, "position observation"),
        _access_from_data(data["access"]),
        RecordLifecycle(data["lifecycle"]),
    )


def _earmark_data(value: Earmark) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref),
        "owner_id": value.owner_id,
        "world_id": value.world_id,
        "position_ref": _slice_a(value.position_ref),
        "allocation": _slice_a(value.allocation),
        "purpose": value.purpose,
        "role": value.role.value,
        "emergency_use_designated": value.emergency_use_designated,
        "observation": _slice_a(value.observation),
        "lifecycle": value.lifecycle.value,
    }


def _earmark_from_data(data: Any) -> Earmark:
    keys = {"ref", "owner_id", "world_id", "position_ref", "allocation", "purpose", "role", "emergency_use_designated", "observation", "lifecycle"}
    data = _object(data, keys, "earmark")
    return Earmark(
        _slice_a_value(data["ref"], Ref, "earmark ref"),
        data["owner_id"],
        data["world_id"],
        _slice_a_value(data["position_ref"], TypedRef, "position ref"),
        _slice_a_value(data["allocation"], AmountEstimate, "earmark allocation"),
        data["purpose"],
        EarmarkRole(data["role"]),
        data["emergency_use_designated"],
        _slice_a_value(data["observation"], ObservationMetadata, "earmark observation"),
        EarmarkLifecycle(data["lifecycle"]),
    )


def _state_data(value: WorldState) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref),
        "owner_id": value.owner_id,
        "world_id": value.world_id,
        "account_refs": [_slice_a(item) for item in value.account_refs],
        "position_refs": [_slice_a(item) for item in value.position_refs],
        "earmark_refs": [_slice_a(item) for item in value.earmark_refs],
        "snapshot_observation": _slice_a(value.snapshot_observation),
        "coverage_note": value.coverage_note,
    }


def _state_from_data(data: Any) -> WorldState:
    keys = {"ref", "owner_id", "world_id", "account_refs", "position_refs", "earmark_refs", "snapshot_observation", "coverage_note"}
    data = _object(data, keys, "world state")
    return WorldState(
        _slice_a_value(data["ref"], Ref, "state ref"),
        data["owner_id"],
        data["world_id"],
        tuple(_slice_a_value(item, TypedRef, "account membership") for item in _list(data["account_refs"], "account_refs")),
        tuple(_slice_a_value(item, TypedRef, "position membership") for item in _list(data["position_refs"], "position_refs")),
        tuple(_slice_a_value(item, TypedRef, "earmark membership") for item in _list(data["earmark_refs"], "earmark_refs")),
        _slice_a_value(data["snapshot_observation"], ObservationMetadata, "state observation"),
        data["coverage_note"],
    )


def _bundle_data(value: WorldAssetBundle) -> Dict[str, Any]:
    return {
        "state": _state_data(value.state),
        "accounts": [_account_data(item) for item in value.accounts],
        "positions": [_position_data(item) for item in value.positions],
        "earmarks": [_earmark_data(item) for item in value.earmarks],
    }


def _bundle_from_data(data: Any) -> WorldAssetBundle:
    data = _object(data, {"state", "accounts", "positions", "earmarks"}, "world asset bundle")
    return WorldAssetBundle(
        _state_from_data(data["state"]),
        tuple(_account_from_data(item) for item in _list(data["accounts"], "accounts")),
        tuple(_position_from_data(item) for item in _list(data["positions"], "positions")),
        tuple(_earmark_from_data(item) for item in _list(data["earmarks"], "earmarks")),
    )


def _currency_amount_data(value: CurrencyAmount) -> Dict[str, Any]:
    return {"currency": value.currency.value, "amount": _slice_a(value.amount)}


def _currency_amount_from_data(data: Any) -> CurrencyAmount:
    data = _object(data, {"currency", "amount"}, "currency amount")
    return CurrencyAmount(Currency(data["currency"]), _slice_a_value(data["amount"], AmountEstimate, "derived amount"))


def serialize_world_asset(value: Any) -> Dict[str, Any]:
    encoders = (
        (Account, "account", _account_data),
        (AssetPosition, "asset_position", _position_data),
        (Earmark, "earmark", _earmark_data),
        (WorldState, "world_state", _state_data),
        (WorldAssetBundle, "world_asset_bundle", _bundle_data),
        (CurrencyAmount, "currency_amount", _currency_amount_data),
        (AccessAssessment, "access_assessment", _access_data),
    )
    for value_type, type_name, encoder in encoders:
        if isinstance(value, value_type):
            return {"schema_version": WORLD_ASSET_SCHEMA_VERSION, "value_type": type_name, "value": encoder(value)}
    raise TypeError(f"Unsupported Slice B value: {type(value)}")


def deserialize_world_asset(data: Mapping[str, Any]) -> Any:
    envelope = _object(data, {"schema_version", "value_type", "value"}, "world asset envelope")
    if envelope["schema_version"] != WORLD_ASSET_SCHEMA_VERSION:
        raise ValueError("unsupported world asset schema version")
    decoders = {
        "account": _account_from_data,
        "asset_position": _position_from_data,
        "earmark": _earmark_from_data,
        "world_state": _state_from_data,
        "world_asset_bundle": _bundle_from_data,
        "currency_amount": _currency_amount_from_data,
        "access_assessment": _access_from_data,
    }
    decoder = decoders.get(envelope["value_type"])
    if decoder is None:
        raise ValueError("unsupported world asset type")
    return decoder(envelope["value"])


def dumps_world_asset(value: Any) -> str:
    return json.dumps(
        serialize_world_asset(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def loads_world_asset(payload: str) -> Any:
    if not isinstance(payload, str):
        raise ValueError("world asset JSON must be text")
    try:
        data = json.loads(payload, parse_constant=lambda value: _reject_constant(value))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("invalid world asset JSON") from error
    return deserialize_world_asset(data)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not supported: {value}")


def _object(data: Any, keys: set, name: str) -> Dict[str, Any]:
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(keys))}")
    return data


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
