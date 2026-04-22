from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .time_periods import PeriodTracker, TimePeriod


@dataclass
class SpendPolicy:
    """Configuration for spend governance rules.

    Attributes:
        max_transaction: Maximum dollar amount allowed per transaction.
        daily_budget: Maximum total dollar amount allowed per calendar day.
        allowed_vendors: If set, only vendors matching these names are
            permitted (case-insensitive substring match).
        blocked_vendors: If set, vendors matching these names are always
            blocked (case-insensitive substring match).
        allowed_mccs: Merchant Category Code allowlist (reserved for future use).
        require_justification: Whether a justification string is required
            for every spend request.
        hourly_budget: Maximum total dollar amount allowed per hour.
            If None, no hourly limit is enforced.
        weekly_budget: Maximum total dollar amount allowed per week.
            If None, no weekly limit is enforced.
        monthly_budget: Maximum total dollar amount allowed per month.
            If None, no monthly limit is enforced.
        enable_rollover: Whether unused budget from expired periods should
            roll over to the next period of the same type.
        rollover_periods: List of period types that support rollover.
            Valid values: "hourly", "daily", "weekly", "monthly".
        require_human_approval_above: If set, spends above this dollar amount
            require human approval via Slack before the gateway is called.
    """

    max_transaction: float = 50.0
    daily_budget: float = 200.0
    allowed_vendors: list[str] | None = None
    blocked_vendors: list[str] | None = None
    allowed_mccs: list[int] | None = None
    require_justification: bool = True
    
    # Time-based budget fields
    hourly_budget: float | None = None
    weekly_budget: float | None = None
    monthly_budget: float | None = None
    
    # Rollover configuration
    enable_rollover: bool = False
    rollover_periods: list[str] = field(default_factory=lambda: ["daily", "weekly", "monthly"])
    require_human_approval_above: float | None = None


@dataclass
class PolicyResult:
    """Result of a policy evaluation.

    Attributes:
        approved: Whether the spend request passed all policy checks.
        denial_reason: Human-readable reason if the request was denied.
        checks_passed: Names of policy checks that passed before denial
            (or all checks if approved).
    """

    approved: bool
    denial_reason: str | None = None
    checks_passed: list[str] = field(default_factory=list)


class PolicyEngine:
    """Stateful engine that evaluates spend requests against policy rules.

    Tracks cumulative daily spend in memory. The daily counter resets
    automatically at the start of each new calendar day.
    
    Also supports time-based spending limits (hourly, weekly, monthly)
    with rollover functionality when configured in the policy.
    """

    def __init__(self, policy: SpendPolicy) -> None:
        """Initialize the engine with a spend policy.

        Args:
            policy: The ``SpendPolicy`` defining governance rules.
        """
        self.policy = policy
        self._daily_spend: float = 0.0
        self._current_date: date = date.today()
        
        # Time-based policy tracking
        self._period_tracker = PeriodTracker()
        
        # Cache for current periods to avoid repeated calculations
        self._current_periods: dict[str, TimePeriod] = {}

    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self._current_date:
            self._daily_spend = 0.0
            self._current_date = today
    
    def _update_time_based_periods(self, current_time: Optional[datetime] = None) -> None:
        """Update current periods and process any rollovers.
        
        Args:
            current_time: Current time (defaults to datetime.now())
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Clear the cache of current periods
        self._current_periods.clear()
        
        # Define period types and their budget limits
        period_configs = []
        if self.policy.hourly_budget is not None:
            period_configs.append(("hourly", self.policy.hourly_budget))
        if self.policy.weekly_budget is not None:
            period_configs.append(("weekly", self.policy.weekly_budget))
        if self.policy.monthly_budget is not None:
            period_configs.append(("monthly", self.policy.monthly_budget))
        
        # Update each configured period type
        for period_type, budget_limit in period_configs:
            # Get or create current period
            period = self._period_tracker.get_current_period(
                period_type, budget_limit, current_time
            )
            
            # Process rollover if enabled for this period type
            if (self.policy.enable_rollover and 
                period_type in self.policy.rollover_periods and
                period.rollover_amount == 0.0):  # Only apply rollover once per period
                
                rollover_amount = self._period_tracker.process_period_rollover(
                    period_type, budget_limit, current_time
                )
                
                if rollover_amount is not None:
                    self._period_tracker.apply_rollover(period, rollover_amount)
            
            # Cache the current period
            self._current_periods[period_type] = period
        
        # Clean up old periods periodically (every 100th call, roughly)
        import random
        if random.randint(1, 100) == 1:
            self._period_tracker.cleanup_old_periods(current_time)
    
    def _check_time_based_budget(self, amount: float, period_type: str, 
                                on_check: Optional[Callable[[str, bool], None]]) -> Optional[str]:
        """Check if amount would exceed time-based budget.
        
        Args:
            amount: Amount to check
            period_type: Type of period to check
            on_check: Optional callback for check result
            
        Returns:
            None if check passes, error message if it fails
        """
        period = self._current_periods.get(period_type)
        if period is None:
            return None  # No budget configured for this period
        
        # Check if the amount would exceed the effective budget
        if period.spent_amount + amount > period.effective_budget:
            return (f"{period_type.title()} budget exhausted "
                   f"(${period.spent_amount:.2f} / ${period.effective_budget:.2f})")
        
        return None
    
    def _record_time_based_spending(self, amount: float) -> None:
        """Record spending in all active time periods.
        
        Args:
            amount: Amount to record
        """
        for period in self._current_periods.values():
            self._period_tracker.record_spending(period, amount)

    def evaluate(
        self,
        amount: float,
        vendor: str,
        justification: str | None = None,
        on_check: Callable[[str, bool], None] | None = None,
    ) -> PolicyResult:
        """Evaluate a spend request against all policy rules.

        Checks are run in order: positive_amount, amount_cap, vendor_allowlist,
        vendor_blocklist, mcc_filter, hourly_budget, weekly_budget, 
        monthly_budget, daily_budget, justification.
        Evaluation stops at the first failure.

        Args:
            amount: Dollar amount of the spend request.
            vendor: Name of the vendor or service.
            justification: Reason for the spend (required if
                ``policy.require_justification`` is True).
            on_check: Optional callback invoked after each check with
                ``(check_name, passed)``.

        Returns:
            A ``PolicyResult`` indicating approval or denial.
        """
        self._reset_daily_if_needed()
        self._update_time_based_periods()
        checks_passed: list[str] = []

        def _pass(name: str) -> None:
            checks_passed.append(name)
            if on_check:
                on_check(name, True)

        def _fail(name: str, reason: str) -> PolicyResult:
            if on_check:
                on_check(name, False)
            return PolicyResult(
                approved=False,
                denial_reason=reason,
                checks_passed=checks_passed,
            )

        # 0. Positive amount check
        if amount <= 0:
            return _fail(
                "positive_amount",
                f"Amount must be positive (got ${amount:.2f})",
            )
        _pass("positive_amount")

        # 1. Amount cap
        if amount > self.policy.max_transaction:
            return _fail(
                "amount_cap",
                f"Amount ${amount:.2f} exceeds limit of ${self.policy.max_transaction:.2f}",
            )
        _pass("amount_cap")

        # 2. Vendor allowlist / blocklist
        vendor_lower = vendor.lower()
        if self.policy.allowed_vendors is not None:
            if not any(v.lower() in vendor_lower for v in self.policy.allowed_vendors):
                return _fail(
                    "vendor_allowlist", f"Vendor '{vendor}' is not in the allowed list"
                )
        _pass("vendor_allowlist")

        if self.policy.blocked_vendors is not None:
            if any(v.lower() in vendor_lower for v in self.policy.blocked_vendors):
                return _fail("vendor_blocklist", f"Vendor '{vendor}' is blocked")
        _pass("vendor_blocklist")

        # 3. MCC filter (stubbed — no MCC in spend request yet)
        _pass("mcc_filter")

        # 4. Time-based budget checks (hourly, weekly, monthly)
        for period_type in ["hourly", "weekly", "monthly"]:
            error = self._check_time_based_budget(amount, period_type, on_check)
            if error:
                return _fail(f"{period_type}_budget", error)
            _pass(f"{period_type}_budget")

        # 5. Daily budget (existing logic)
        if self._daily_spend + amount > self.policy.daily_budget:
            return _fail(
                "daily_budget",
                f"Daily budget exhausted (${self._daily_spend:.2f} / ${self.policy.daily_budget:.2f})",
            )
        _pass("daily_budget")

        # 6. Justification present
        if self.policy.require_justification and not justification:
            return _fail(
                "justification", "Justification is required but was not provided"
            )
        _pass("justification")

        return PolicyResult(approved=True, checks_passed=checks_passed)

    def commit_spend(self, amount: float) -> None:
        """Permanently record a spend against all budget counters.

        Must be called only after a successful gateway transaction so that a
        gateway failure does not silently consume the agent's budget.

        Args:
            amount: Dollar amount that was successfully spent.
        """
        self._reset_daily_if_needed()
        self._daily_spend += amount
        self._update_time_based_periods()
        self._record_time_based_spending(amount)
