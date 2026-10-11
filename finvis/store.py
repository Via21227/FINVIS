"""Single-process, volatile transactions: immutable history + one atomic publish.

Not a database or durable audit store. All application writes go through one lock.
Readers receive an immutable snapshot; no staged result is visible on failure.
"""

from dataclasses import dataclass, replace
from threading import RLock
from typing import Callable, Optional, Tuple, Type, TypeVar

from .domain import FinancialState, LivingPlan, Ref

T = TypeVar("T")


class Rejected(ValueError):
    """Invalid command; no success or mutation is committed."""


class StaleVersion(Rejected):
    pass


@dataclass(frozen=True)
class Snapshot:
    records: Tuple[object, ...]
    current_state_ref: Ref
    current_plan_ref: Ref
    revision: int = 0

    def get(self, kind: Type[T], ref: Ref) -> T:
        for record in self.records:
            if type(record) is kind and record.ref == ref:
                return record
        raise Rejected(f"Missing {kind.__name__}: {ref}")

    def latest(self, kind: Type[T], identity: str) -> T:
        matches = [r for r in self.records if type(r) is kind and r.ref.id == identity]
        if not matches:
            raise Rejected(f"Missing {kind.__name__}: {identity}")
        return max(matches, key=lambda r: r.ref.version)

    def append(self, *records: object) -> "Snapshot":
        keys = {(type(r), r.ref) for r in self.records}
        for record in records:
            if (type(record), record.ref) in keys:
                raise Rejected("An existing identity/version cannot be overwritten")
            keys.add((type(record), record.ref))
        return replace(self, records=self.records + tuple(records))


class MemoryStore:
    def __init__(self, initial: Snapshot, before_publish: Optional[Callable[[Snapshot], None]] = None):
        self._lock = RLock()
        self._snapshot = initial
        self._before_publish = before_publish  # Fault injection for atomicity tests.
        self._validate(initial)

    @staticmethod
    def _validate(snapshot: Snapshot) -> None:
        if not isinstance(snapshot.records, tuple):
            raise Rejected("History must be immutable")
        keys = [(type(r), r.ref) for r in snapshot.records]
        if len(keys) != len(set(keys)):
            raise Rejected("Duplicate record versions")
        state = snapshot.get(FinancialState, snapshot.current_state_ref)
        plan = snapshot.get(LivingPlan, snapshot.current_plan_ref)
        if plan.state_ref != state.ref or plan.owner_id != state.owner_id:
            raise Rejected("Current state and plan must be coherent")

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def transact(self, command: Callable[[Snapshot], Tuple[Snapshot, T]]) -> T:
        with self._lock:
            before = self._snapshot
            candidate, result = command(before)
            if candidate is before:
                return result  # Exact idempotent retry, no new history.
            if candidate.records[:len(before.records)] != before.records:
                raise Rejected("Transactions must preserve every prior record")
            candidate = replace(candidate, revision=before.revision + 1)
            self._validate(candidate)
            if self._before_publish:
                self._before_publish(candidate)
            self._snapshot = candidate  # Sole publication point, after all checks.
            return result
