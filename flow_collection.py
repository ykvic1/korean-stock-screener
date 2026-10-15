"""Bound live flow requests without converting missing observations to zero."""
import time


class FlowCollector:
    def __init__(self, primary, fallback, budget_seconds=1200, timeout_limit=3,
                 clock=time.monotonic):
        self.primary, self.fallback = primary, fallback
        self.clock, self.started = clock, clock()
        self.budget_seconds, self.timeout_limit = budget_seconds, timeout_limit
        self.consecutive_timeouts = 0
        self.attempted = 0

    def collect(self, code):
        if self.consecutive_timeouts >= self.timeout_limit:
            return None, 'deferred: consecutive supplier timeouts'
        if self.clock() - self.started >= self.budget_seconds:
            return None, 'deferred: flow collection time budget exhausted'
        self.attempted += 1
        errors = []
        # A failed fallback is never submitted a second time for the same stock.
        for source, fetch in (('KRX', self.primary), ('Npay', self.fallback)):
            if fetch is None:
                continue
            try:
                result = fetch(code)
                self.consecutive_timeouts = 0
                return result, None
            except Exception as exc:
                errors.append(f'{source}: {type(exc).__name__}: {str(exc)[:180]}')
                if source == 'Npay':
                    # Playwright and requests use different timeout subclasses.
                    timed_out = any('timeout' in c.__name__.lower()
                                    for c in type(exc).__mro__)
                    self.consecutive_timeouts = self.consecutive_timeouts + 1 if timed_out else 0
        return None, ' / '.join(errors)
