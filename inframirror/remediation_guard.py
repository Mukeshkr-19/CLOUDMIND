"""Thread-safe per-service restart budgets and recovery circuit breakers."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import threading
import time
from typing import Callable, cast, Deque, Dict


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    state: Dict[str, object]


class RemediationGuard:
    def __init__(
        self,
        max_restarts_per_hour: int = 3,
        max_failed_recoveries: int = 2,
        circuit_breaker_reset_sec: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_restarts = max(1, min(int(max_restarts_per_hour), 20))
        self.max_failures = max(1, min(int(max_failed_recoveries), 20))
        self.reset_sec = max(30, min(int(circuit_breaker_reset_sec), 86400))
        self.clock = clock
        self._restarts: Dict[str, Deque[float]] = defaultdict(deque)
        self._failed_recoveries: Dict[str, int] = defaultdict(int)
        self._open_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, target: str, now: float) -> None:
        events = self._restarts[target]
        while events and now - events[0] >= 3600.0:
            events.popleft()
        until = self._open_until.get(target)
        if until is not None and now >= until:
            self._open_until.pop(target, None)
            self._failed_recoveries[target] = 0

    def _state_unlocked(self, target: str, now: float) -> Dict[str, object]:
        self._prune(target, now)
        until = self._open_until.get(target)
        return {
            "target": target,
            "restart_budget_remaining": max(0, self.max_restarts - len(self._restarts[target])),
            "restart_budget_limit": self.max_restarts,
            "failed_recoveries": self._failed_recoveries[target],
            "circuit_breaker_open": until is not None,
            "circuit_breaker_reset_in_sec": max(0.0, until - now) if until is not None else 0.0,
        }

    def state(self, target: str) -> Dict[str, object]:
        with self._lock:
            return self._state_unlocked(target, self.clock())

    def reserve_restart(self, target: str) -> GuardDecision:
        """Atomically check the guard and consume one restart budget slot."""
        with self._lock:
            now = self.clock()
            state = self._state_unlocked(target, now)
            if state["circuit_breaker_open"]:
                return GuardDecision(False, "circuit_breaker_open", state)
            if cast(int, state["restart_budget_remaining"]) <= 0:
                return GuardDecision(False, "restart_budget_exhausted", state)
            self._restarts[target].append(now)
            return GuardDecision(True, "restart_reserved", self._state_unlocked(target, now))

    def record_recovery(self, target: str, recovered: bool) -> Dict[str, object]:
        with self._lock:
            now = self.clock()
            self._prune(target, now)
            if recovered:
                self._failed_recoveries[target] = 0
            else:
                self._failed_recoveries[target] += 1
                if self._failed_recoveries[target] >= self.max_failures:
                    self._open_until[target] = now + self.reset_sec
            return self._state_unlocked(target, now)

    def reset(self, target: str) -> Dict[str, object]:
        with self._lock:
            self._failed_recoveries[target] = 0
            self._open_until.pop(target, None)
            return self._state_unlocked(target, self.clock())
