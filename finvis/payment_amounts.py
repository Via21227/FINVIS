"""Source-described payment basis for hypothetical cash movement, never settlement.

A quote remains an observation.  Only an explicit credit changes its future due;
refundable/nonrefundable descriptions never rewrite historical payments.  This
module performs the same native-currency precision arithmetic as settlement's
read-only summaries, without creating any financial record or authorization.
"""
from dataclasses import dataclass
from typing import Tuple

from .domain import Currency, Money
from .world_values import AmountEstimate, AmountPrecision
from .world_fulfillment import signed_amount


ROLES = ('paid', 'credited', 'refundable', 'nonrefundable')
KINDS = ('total', 'additional', 'refund', 'cancellation_fee')


def _require(ok, code):
    if not ok:
        raise ValueError(code)


def _text(value, maximum):
    return type(value) is str and bool(value.strip()) and len(value) <= maximum


@dataclass(frozen=True)
class PaymentPart:
    identity: str
    role: str
    amount: AmountEstimate
    source: str

    def __post_init__(self):
        _require(_text(self.identity, 256), 'payment_identity_required')
        _require(self.role in ROLES, 'payment_role_invalid')
        _require(type(self.amount) is AmountEstimate and self.amount.currency in tuple(Currency),
                 'payment_native_amount_required')
        _require(_text(self.source, 4000), 'payment_source_required')


def _bounds(amount):
    if amount.precision is AmountPrecision.EXACT_REPORTED:
        return amount.amount.minor_units, amount.amount.minor_units
    if amount.lower_bound is not None:
        return amount.lower_bound.minor_units, amount.upper_bound.minor_units
    return None


def _capacity(paid, portions):
    """Known capacity, unresolved capacity, or a concrete over-allocation error.

    An explicit whole-payment allocation is the same correlated operand, even
    when approximate.  Different uncertain operands do not manufacture bounds.
    """
    if not portions:
        return True
    if len(portions) == 1 and portions[0] == paid:
        return True
    original = _bounds(paid)
    pieces = [_bounds(p) for p in portions]
    if original is not None and all(p is not None for p in pieces):
        _require(sum(p[0] for p in pieces) <= original[1], 'payment_allocation_exceeds_paid')
        return sum(p[1] for p in pieces) <= original[0]
    if paid.amount is not None and all(p.amount is not None for p in portions):
        _require(sum(p.amount.minor_units for p in portions) <= paid.amount.minor_units,
                 'payment_allocation_exceeds_paid')
    return False


def _allocation_status(parts):
    payments = {p.identity: p for p in parts if p.role == 'paid'}
    status = {}
    for identity, paid in payments.items():
        values = {p.role: p.amount for p in parts if p.identity == identity}
        credit_refund = [values[r] for r in ('credited', 'refundable') if r in values]
        nonrefund_refund = [values[r] for r in ('nonrefundable', 'refundable') if r in values]
        # Credit and nonrefundable can overlap: a nonrefundable deposit can
        # still be applied to the purchase.  They are NOT added together.
        status[identity] = (_capacity(paid.amount, credit_refund),
                            _capacity(paid.amount, nonrefund_refund))
    return status


@dataclass(frozen=True)
class PaymentBasis:
    kind: str
    parts: Tuple[PaymentPart, ...]
    source: str

    def __post_init__(self):
        _require(self.kind in KINDS, 'payment_basis_kind_invalid')
        _require(type(self.parts) is tuple and len(self.parts) <= 128 and
                 all(type(p) is PaymentPart for p in self.parts), 'payment_parts_required')
        _require(_text(self.source, 8000), 'payment_source_required')
        keys = [(p.identity, p.role) for p in self.parts]
        _require(len(keys) == len(set(keys)), 'payment_part_duplicate')
        paid = {p.identity: p for p in self.parts if p.role == 'paid'}
        _require(all(p.role == 'paid' or p.identity in paid for p in self.parts),
                 'payment_paid_parent_required')
        _require(len({p.amount.currency for p in self.parts}) <= 1, 'payment_currency_mismatch')
        _allocation_status(self.parts)
        if self.kind in ('additional', 'cancellation_fee'):
            _require(not any(p.role == 'credited' for p in self.parts), 'payment_credit_requires_total')
        if self.kind == 'refund':
            _require(sum(p.role == 'refundable' for p in self.parts) == 1,
                     'payment_explicit_refund_required')


def _estimate(result, currency):
    if result['precision'] == 'unknown':
        return AmountEstimate.unknown('Payment depends on an unknown or unbounded amount', currency)
    lo, hi = result['lower_minor'], result['upper_minor']
    nominal = result['value_minor']
    _require((nominal is None or nominal >= 0) and (lo is None or lo >= 0),
             'payment_remaining_negative')
    if result['precision'] == 'range':
        return AmountEstimate.range(Money(lo, currency), Money(hi, currency))
    if result['precision'] == 'approximate':
        return AmountEstimate.approximate(Money(nominal, currency),
            None if lo is None else Money(lo, currency),
            None if hi is None else Money(hi, currency))
    return AmountEstimate.exact_reported(Money(nominal, currency))


def derive_payment(observed, basis):
    """Derive the future cash movement, retaining reported precision.

    Unknown allocation capacity cannot support a precise remaining payment or
    refund.  A separately quoted additional payment/fee does not depend on any
    refund being known.  No fee-to-refund arithmetic is performed.
    """
    _require(type(observed) is AmountEstimate and observed.currency in tuple(Currency),
             'payment_native_amount_required')
    _require(type(basis) is PaymentBasis, 'typed_payment_basis_required')
    _require(all(p.amount.currency == observed.currency for p in basis.parts),
             'payment_currency_mismatch')
    status = _allocation_status(basis.parts)
    if basis.kind == 'refund':
        refund = next(p for p in basis.parts if p.role == 'refundable')
        _require(observed == refund.amount, 'payment_refund_quote_mismatch')
        if not all(status[refund.identity]):
            return AmountEstimate.unknown('Refund allocation needs supported bounds', observed.currency)
        return observed
    if basis.kind != 'total':
        return observed
    credits = [p for p in basis.parts if p.role == 'credited']
    if not credits:
        return observed
    if any(not status[p.identity][0] for p in credits):
        return AmountEstimate.unknown('Payment credit allocation needs supported bounds', observed.currency)
    return _estimate(signed_amount([(observed, 1), *((p.amount, -1) for p in credits)],
                                   observed.currency), observed.currency)


def payment_planning(observed, basis):
    """Existing Future exact-to-approximate rule applied after derivation."""
    result = derive_payment(observed, basis)
    return AmountEstimate.approximate(result.amount) if result.precision is AmountPrecision.EXACT_REPORTED else result


def validate_payment_effects(effects):
    """Check simultaneously selected effects, not mutually exclusive options.

    Same paid evidence may be described in multiple effects.  A particular
    credit/refund allocation may drive only one active cash effect, and the
    combined active descriptions must still fit the original payment.
    """
    parts = {}
    used = set()
    for effect in effects:
        basis = effect.payment_basis
        if basis is None:
            continue
        for part in basis.parts:
            key = part.identity, part.role
            old = parts.get(key)
            _require(old is None or old.amount == part.amount, 'payment_identity_conflict')
            parts.setdefault(key, part)
            active = (basis.kind == 'total' and part.role == 'credited') or (
                basis.kind == 'refund' and part.role == 'refundable')
            if active:
                _require(key not in used, 'payment_allocation_reused')
                used.add(key)
    if parts:
        # Structural validity was already checked per basis.  Recheck their
        # merged active-world descriptions to prevent cross-option overuse.
        status = _allocation_status(tuple(parts.values()))
        for identity in {key[0] for key in used}:
            active = {role for ident, role in used if ident == identity}
            if len(active) > 1:
                _require(all(status[identity]), 'payment_allocation_capacity_unresolved')
