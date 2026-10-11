from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import unittest

from finvis.domain import Confidence, ConfidenceAssessment, Currency, Money, RealitySource, Ref
from finvis.replay import markdown, structured
from finvis.story import run_story
from finvis.world_domain import (
    AccessAssessment,
    AccessStatus,
    Account,
    AccountKind,
    AssetPosition,
    AssetPositionKind,
    CurrencyAmount,
    Earmark,
    EarmarkLifecycle,
    EarmarkRole,
    RecordLifecycle,
    WorldAssetBundle,
    WorldRecordKind,
    WorldState,
    amount_for_currency,
    deserialize_world_asset,
    dumps_world_asset,
    loads_world_asset,
    serialize_world_asset,
    typed_ref,
)
from finvis.world_values import (
    AmountEstimate,
    AmountPrecision,
    FreshnessAssessment,
    FreshnessStatus,
    ObservationMetadata,
    ObservationRole,
    TemporalSpec,
    dumps_world_value,
    loads_world_value,
)


ROOT = Path(__file__).resolve().parents[1]
OWNER = "synthetic-owner"
WORLD = "synthetic-world"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def money(major: int, currency: Currency = Currency.CNY) -> Money:
    return Money(major * 100, currency)


def exact(major: int, currency: Currency = Currency.CNY) -> AmountEstimate:
    return AmountEstimate.exact_reported(money(major, currency))


def observation(identity: str, role: ObservationRole = ObservationRole.CURRENT) -> ObservationMetadata:
    return ObservationMetadata(
        RealitySource.USER_REPORT,
        (Ref(f"evidence/{identity}", 1),),
        (),
        NOW,
        TemporalSpec.known_date(date(2026, 9, 20)),
        NOW,
        role,
        Confidence(ConfidenceAssessment.SUPPORTED, "Reviewed synthetic report"),
        FreshnessAssessment(FreshnessStatus.CURRENT, "Reviewed for this snapshot", NOW),
    )


def account(identity: str, label: str) -> Account:
    return Account(
        Ref(f"account/{identity}", 1), OWNER, WORLD, label, label,
        AccountKind.CASH_CONTAINER, observation(f"account-{identity}"),
    )


def position(identity: str, owner: Account, balance: AmountEstimate, currency: Currency = Currency.CNY, partition: str = "primary") -> AssetPosition:
    return AssetPosition(
        Ref(f"position/{identity}", 1), OWNER, WORLD,
        typed_ref(WorldRecordKind.ACCOUNT, owner.ref), currency,
        AssetPositionKind.CASH, partition, balance,
        observation(f"position-{identity}"),
        AccessAssessment(AccessStatus.UNKNOWN, "Access eligibility not reviewed", observation(f"access-{identity}")),
    )


def earmark(identity: str, owner: AssetPosition, allocation: AmountEstimate, role: EarmarkRole, purpose: str, version: int = 1) -> Earmark:
    record_ref = Ref(f"earmark/{identity}", version)
    metadata = observation(f"earmark-{identity}-{version}")
    if version > 1:
        metadata = replace(
            metadata,
            corrects_ref=Ref(record_ref.id, version - 1),
            correction_reason="Reviewed allocation correction",
        )
    return Earmark(
        record_ref, OWNER, WORLD,
        typed_ref(WorldRecordKind.ASSET_POSITION, owner.ref), allocation,
        purpose, role, role is EarmarkRole.EMERGENCY,
        metadata,
    )


def bundle_with_allocations(emergency: int = 5_000, phones: int = 20_000, earmark_version: int = 1) -> WorldAssetBundle:
    icbc = account("icbc", "ICBC")
    alipay = account("alipay", "Alipay")
    icbc_cny = position("icbc-cny", icbc, exact(9_000))
    alipay_cny = position("alipay-cny", alipay, exact(25_000))
    emergency_mark = earmark("alipay-emergency", alipay_cny, exact(emergency), EarmarkRole.EMERGENCY, "Emergency reserve", earmark_version)
    phone_mark = earmark("alipay-phones", alipay_cny, exact(phones), EarmarkRole.PURCHASE, "Phones", earmark_version)
    accounts = (icbc, alipay)
    positions = (icbc_cny, alipay_cny)
    earmarks = (emergency_mark, phone_mark)
    state = WorldState(
        Ref("world-state", earmark_version), OWNER, WORLD,
        tuple(typed_ref(WorldRecordKind.ACCOUNT, item.ref) for item in accounts),
        tuple(typed_ref(WorldRecordKind.ASSET_POSITION, item.ref) for item in positions),
        tuple(typed_ref(WorldRecordKind.EARMARK, item.ref) for item in earmarks),
        observation(f"state-{earmark_version}"),
        "Reviewed ICBC and Alipay current-asset subset only",
    )
    return WorldAssetBundle(state, accounts, positions, earmarks)


class EntityConstructionTests(unittest.TestCase):
    def test_account_has_identity_location_and_no_balance_field(self):
        value = account("icbc", "ICBC")
        self.assertEqual(value.label, "ICBC")
        self.assertNotIn("balance", {field.name for field in fields(Account)})

    def test_position_is_the_only_current_owned_amount(self):
        owner = account("icbc", "ICBC")
        value = position("icbc-cny", owner, exact(9_000))
        self.assertEqual(value.balance.amount, money(9_000))
        self.assertIs(value.observation.role, ObservationRole.CURRENT)

    def test_position_rejects_currency_mismatch_and_noncurrent_observation(self):
        owner = account("icbc", "ICBC")
        with self.assertRaises(ValueError):
            position("icbc-cny", owner, exact(9_000, Currency.SGD), Currency.CNY)
        with self.assertRaises(ValueError):
            replace(position("icbc-cny", owner, exact(9_000)), observation=observation("future", ObservationRole.FUTURE))

    def test_earmark_is_an_allocation_not_an_asset(self):
        owner = account("alipay", "Alipay")
        pool = position("alipay-cny", owner, exact(25_000))
        value = earmark("phones", pool, exact(20_000), EarmarkRole.PURCHASE, "Phones")
        self.assertEqual(value.allocation.amount, money(20_000))
        self.assertNotIn("balance", {field.name for field in fields(Earmark)})

    def test_record_identity_is_separate_from_display_label(self):
        first = account("wallet-a", "Wallet")
        second = account("wallet-b", "Wallet")
        self.assertEqual(first.label, second.label)
        self.assertNotEqual(first.ref, second.ref)

    def test_entities_and_state_are_immutable(self):
        bundle = bundle_with_allocations()
        with self.assertRaises(FrozenInstanceError):
            bundle.accounts[0].label = "Changed"
        with self.assertRaises(FrozenInstanceError):
            bundle.state.coverage_note = "Changed"


class MembershipAndConservationTests(unittest.TestCase):
    def test_canonical_state_derives_assets_earmarks_and_unallocated(self):
        bundle = bundle_with_allocations()
        current = amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY)
        marked = amount_for_currency(bundle.earmarked_assets_by_currency(), Currency.CNY)
        free = amount_for_currency(bundle.unallocated_assets_by_currency(), Currency.CNY)
        self.assertEqual(current.amount, money(34_000))
        self.assertEqual(marked.amount, money(25_000))
        self.assertEqual(free.amount, money(9_000))

    def test_canonical_position_and_account_results(self):
        bundle = bundle_with_allocations()
        icbc = next(item for item in bundle.accounts if item.ref.id == "account/icbc")
        alipay = next(item for item in bundle.accounts if item.ref.id == "account/alipay")
        icbc_position = next(item for item in bundle.positions if item.ref.id == "position/icbc-cny")
        alipay_position = next(item for item in bundle.positions if item.ref.id == "position/alipay-cny")
        self.assertEqual(amount_for_currency(bundle.account_assets_by_currency(icbc.ref), Currency.CNY).amount, money(9_000))
        self.assertEqual(amount_for_currency(bundle.account_assets_by_currency(alipay.ref), Currency.CNY).amount, money(25_000))
        self.assertEqual(bundle.position_unallocated(icbc_position.ref).amount, money(9_000))
        self.assertEqual(bundle.position_earmarked(alipay_position.ref).amount, money(25_000))
        self.assertEqual(bundle.position_unallocated(alipay_position.ref).amount, money(0))

    def test_valid_correction_reallocates_without_creating_money(self):
        before = bundle_with_allocations(5_000, 20_000, 1)
        after = bundle_with_allocations(6_000, 19_000, 2)
        self.assertEqual(
            amount_for_currency(before.current_assets_by_currency(), Currency.CNY),
            amount_for_currency(after.current_assets_by_currency(), Currency.CNY),
        )
        self.assertEqual(amount_for_currency(after.earmarked_assets_by_currency(), Currency.CNY).amount, money(25_000))
        self.assertEqual(amount_for_currency(after.unallocated_assets_by_currency(), Currency.CNY).amount, money(9_000))
        for corrected in after.earmarks:
            self.assertEqual(corrected.ref.version, 2)
            self.assertEqual(corrected.observation.corrects_ref, Ref(corrected.ref.id, 1))

    def test_overallocation_rejects_the_whole_bundle(self):
        with self.assertRaisesRegex(ValueError, "exceed"):
            bundle_with_allocations(6_000, 20_000, 2)

    def test_wrong_currency_earmark_is_rejected(self):
        base = bundle_with_allocations()
        bad = replace(base.earmarks[0], allocation=exact(5_000, Currency.SGD))
        refs = tuple(typed_ref(WorldRecordKind.EARMARK, item.ref) for item in (bad, base.earmarks[1]))
        state = replace(base.state, earmark_refs=refs)
        with self.assertRaisesRegex(ValueError, "currency"):
            WorldAssetBundle(state, base.accounts, base.positions, (bad, base.earmarks[1]))

    def test_dangling_position_account_ref_fails_closed(self):
        base = bundle_with_allocations()
        bad = replace(base.positions[0], account_ref=typed_ref(WorldRecordKind.ACCOUNT, Ref("account/missing", 1)))
        refs = tuple(typed_ref(WorldRecordKind.ASSET_POSITION, item.ref) for item in (bad, base.positions[1]))
        state = replace(base.state, position_refs=refs)
        with self.assertRaisesRegex(ValueError, "nonexistent account"):
            WorldAssetBundle(state, base.accounts, (bad, base.positions[1]), base.earmarks)

    def test_dangling_earmark_position_ref_fails_closed(self):
        base = bundle_with_allocations()
        bad = replace(base.earmarks[0], position_ref=typed_ref(WorldRecordKind.ASSET_POSITION, Ref("position/missing", 1)))
        refs = tuple(typed_ref(WorldRecordKind.EARMARK, item.ref) for item in (bad, base.earmarks[1]))
        state = replace(base.state, earmark_refs=refs)
        with self.assertRaisesRegex(ValueError, "nonexistent asset position"):
            WorldAssetBundle(state, base.accounts, base.positions, (bad, base.earmarks[1]))

    def test_duplicate_stable_ids_fail_closed(self):
        base = bundle_with_allocations()
        duplicate = replace(base.accounts[0], ref=Ref(base.accounts[1].ref.id, 2))
        refs = tuple(typed_ref(WorldRecordKind.ACCOUNT, item.ref) for item in (duplicate, base.accounts[1]))
        with self.assertRaisesRegex(ValueError, "duplicate stable ID"):
            replace(base.state, account_refs=refs)

    def test_duplicate_membership_ids_fail_closed(self):
        base = bundle_with_allocations()
        with self.assertRaisesRegex(ValueError, "duplicate stable ID"):
            replace(base.state, account_refs=(base.state.account_refs[0], base.state.account_refs[0]))

    def test_duplicate_account_currency_partition_fails_closed(self):
        base = bundle_with_allocations()
        original = base.positions[0]
        duplicate = replace(original, ref=Ref("position/icbc-alias", 1))
        positions = (*base.positions, duplicate)
        refs = tuple(typed_ref(WorldRecordKind.ASSET_POSITION, item.ref) for item in positions)
        state = replace(base.state, position_refs=refs)
        with self.assertRaisesRegex(ValueError, "duplicate one account/currency/partition"):
            WorldAssetBundle(state, base.accounts, positions, base.earmarks)

    def test_state_membership_must_match_exact_versions(self):
        base = bundle_with_allocations()
        stale = replace(base.state, position_refs=(typed_ref(WorldRecordKind.ASSET_POSITION, Ref("position/alipay-cny", 99)), base.state.position_refs[1]))
        with self.assertRaisesRegex(ValueError, "membership"):
            WorldAssetBundle(stale, base.accounts, base.positions, base.earmarks)

    def test_retired_or_released_records_cannot_be_current_members(self):
        base = bundle_with_allocations()
        with self.assertRaisesRegex(ValueError, "retired account"):
            WorldAssetBundle(base.state, (replace(base.accounts[0], lifecycle=RecordLifecycle.RETIRED), base.accounts[1]), base.positions, base.earmarks)
        with self.assertRaisesRegex(ValueError, "released earmark"):
            WorldAssetBundle(base.state, base.accounts, base.positions, (replace(base.earmarks[0], lifecycle=EarmarkLifecycle.RELEASED), base.earmarks[1]))


class CurrencyAndPrecisionTests(unittest.TestCase):
    def test_multi_currency_totals_remain_separate_without_fx(self):
        wallet = account("multi", "Multi-currency wallet")
        cny_position = position("multi-cny", wallet, exact(100, Currency.CNY), Currency.CNY, "cny")
        sgd_position = position("multi-sgd", wallet, exact(50, Currency.SGD), Currency.SGD, "sgd")
        state = WorldState(
            Ref("world-state-multi", 1), OWNER, WORLD,
            (typed_ref(WorldRecordKind.ACCOUNT, wallet.ref),),
            tuple(typed_ref(WorldRecordKind.ASSET_POSITION, item.ref) for item in (cny_position, sgd_position)),
            (), observation("state-multi"), "Reported wallet currencies remain separate",
        )
        bundle = WorldAssetBundle(state, (wallet,), (cny_position, sgd_position), ())
        totals = bundle.current_assets_by_currency()
        self.assertEqual(tuple(item.currency for item in totals), (Currency.CNY, Currency.SGD))
        self.assertEqual(amount_for_currency(totals, Currency.CNY).amount, money(100, Currency.CNY))
        self.assertEqual(amount_for_currency(totals, Currency.SGD).amount, money(50, Currency.SGD))

    def test_unallocated_approximate_position_preserves_precision(self):
        owner = account("approx", "Approximate wallet")
        balance = AmountEstimate.approximate(money(100), money(90), money(110))
        pool = position("approx-cny", owner, balance)
        state = WorldState(Ref("state-approx", 1), OWNER, WORLD, (typed_ref(WorldRecordKind.ACCOUNT, owner.ref),), (typed_ref(WorldRecordKind.ASSET_POSITION, pool.ref),), (), observation("state-approx"), "Approximate report")
        bundle = WorldAssetBundle(state, (owner,), (pool,), ())
        self.assertEqual(bundle.position_unallocated(pool.ref), balance)
        self.assertIs(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).precision, AmountPrecision.APPROXIMATE)

    def test_range_position_and_exact_earmark_use_conservative_bounds(self):
        owner = account("range", "Range wallet")
        pool = position("range-cny", owner, AmountEstimate.range(money(100), money(120)))
        mark = earmark("range-use", pool, exact(80), EarmarkRole.OTHER, "Reserved use")
        state = WorldState(Ref("state-range", 1), OWNER, WORLD, (typed_ref(WorldRecordKind.ACCOUNT, owner.ref),), (typed_ref(WorldRecordKind.ASSET_POSITION, pool.ref),), (typed_ref(WorldRecordKind.EARMARK, mark.ref),), observation("state-range"), "Range report")
        bundle = WorldAssetBundle(state, (owner,), (pool,), (mark,))
        remaining = bundle.position_unallocated(pool.ref)
        self.assertIs(remaining.precision, AmountPrecision.RANGE)
        self.assertEqual((remaining.lower_bound, remaining.upper_bound), (money(20), money(40)))

    def test_bounded_approximate_allocation_can_be_proven_conservative(self):
        owner = account("bounded", "Bounded wallet")
        pool = position("bounded-cny", owner, AmountEstimate.approximate(money(110), money(100), money(120)))
        allocation = AmountEstimate.approximate(money(70), money(60), money(80))
        mark = earmark("bounded-use", pool, allocation, EarmarkRole.OTHER, "Bounded reserve")
        state = WorldState(Ref("state-bounded", 1), OWNER, WORLD, (typed_ref(WorldRecordKind.ACCOUNT, owner.ref),), (typed_ref(WorldRecordKind.ASSET_POSITION, pool.ref),), (typed_ref(WorldRecordKind.EARMARK, mark.ref),), observation("state-bounded"), "Bounded report")
        bundle = WorldAssetBundle(state, (owner,), (pool,), (mark,))
        remaining = bundle.position_unallocated(pool.ref)
        self.assertIs(remaining.precision, AmountPrecision.APPROXIMATE)
        self.assertEqual((remaining.lower_bound, remaining.upper_bound), (money(20), money(60)))

    def test_unbounded_or_unknown_allocation_cannot_prove_conservation(self):
        owner = account("uncertain", "Uncertain wallet")
        pool = position("uncertain-cny", owner, AmountEstimate.approximate(money(100)))
        for allocation in (exact(10), AmountEstimate.approximate(money(10)), AmountEstimate.unknown("Not reported", Currency.CNY)):
            mark = earmark("uncertain-use", pool, allocation, EarmarkRole.OTHER, "Uncertain reserve")
            state = WorldState(Ref("state-uncertain", 1), OWNER, WORLD, (typed_ref(WorldRecordKind.ACCOUNT, owner.ref),), (typed_ref(WorldRecordKind.ASSET_POSITION, pool.ref),), (typed_ref(WorldRecordKind.EARMARK, mark.ref),), observation("state-uncertain"), "Uncertain report")
            with self.subTest(allocation=allocation), self.assertRaisesRegex(ValueError, "unresolved"):
                WorldAssetBundle(state, (owner,), (pool,), (mark,))

    def test_unknown_position_without_allocation_remains_unknown_not_zero(self):
        owner = account("unknown", "Unknown wallet")
        balance = AmountEstimate.unknown("Balance not reported", Currency.CNY)
        pool = position("unknown-cny", owner, balance)
        state = WorldState(Ref("state-unknown", 1), OWNER, WORLD, (typed_ref(WorldRecordKind.ACCOUNT, owner.ref),), (typed_ref(WorldRecordKind.ASSET_POSITION, pool.ref),), (), observation("state-unknown"), "Unknown balance retained")
        bundle = WorldAssetBundle(state, (owner,), (pool,), ())
        self.assertIs(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).precision, AmountPrecision.UNKNOWN)
        self.assertEqual(bundle.position_unallocated(pool.ref), balance)


class SerializationAndRegressionTests(unittest.TestCase):
    def test_slice_b_records_and_bundle_round_trip(self):
        bundle = bundle_with_allocations()
        values = (*bundle.accounts, *bundle.positions, *bundle.earmarks, bundle.state, bundle, bundle.positions[0].access, bundle.current_assets_by_currency()[0])
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(loads_world_asset(dumps_world_asset(value)), value)
                self.assertEqual(deserialize_world_asset(serialize_world_asset(value)), value)

    def test_canonical_bundle_serialization_is_deterministic(self):
        bundle = bundle_with_allocations()
        first = dumps_world_asset(bundle)
        second = dumps_world_asset(loads_world_asset(first))
        self.assertEqual(first, second)
        self.assertIn('"schema_version":"finvis.world-assets.v0.1"', first)
        self.assertNotIn(": ", first)
        self.assertNotIn(", ", first)

    def test_deserialization_rejects_unknown_schema_type_and_fields(self):
        data = serialize_world_asset(bundle_with_allocations())
        for mutated in (
            {**data, "schema_version": "finvis.world-assets.v9"},
            {**data, "value_type": "financial_world"},
            {**data, "unexpected": True},
        ):
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                deserialize_world_asset(mutated)

    def test_slice_a_serialization_and_precision_remain_unchanged(self):
        value = AmountEstimate.range(money(17_000), money(19_000))
        self.assertEqual(loads_world_value(dumps_world_value(value)), value)
        self.assertIs(value.precision, AmountPrecision.RANGE)

    def test_slice_b_contains_no_future_or_plan_fields(self):
        field_names = {field.name for kind in (Account, AssetPosition, Earmark, WorldState) for field in fields(kind)}
        for forbidden in ("future_inflows", "planned_expenses", "actual_occurrences", "decisions", "living_plan"):
            self.assertNotIn(forbidden, field_names)

    def test_phase2_replay_artifacts_remain_exact(self):
        snapshot = run_story().snapshot
        actual_markdown = (markdown(snapshot) + "\n").encode()
        actual_json = (json.dumps(structured(snapshot), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        self.assertEqual(actual_markdown, (ROOT / "artifacts/phase2-replay.md").read_bytes())
        self.assertEqual(actual_json, (ROOT / "artifacts/phase2-replay.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
