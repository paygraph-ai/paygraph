from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date


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
    """

    max_transaction: float = 50.0
    daily_budget: float = 200.0
    allowed_vendors: list[str] | None = None
    blocked_vendors: list[str] | None = None
    allowed_mccs: list[int] | None = None
    require_justification: bool = True


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
    """

    def __init__(self, policy: SpendPolicy) -> None:
        """Initialize the engine with a spend policy.

        Args:
            policy: The ``SpendPolicy`` defining governance rules.
        """
        self.policy = policy
        self._daily_spend: float = 0.0
        self._current_date: date = date.today()

    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self._current_date:
            self._daily_spend = 0.0
            self._current_date = today

    def evaluate(
        self,
        amount: float,
        vendor: str,
        justification: str | None = None,
        on_check: Callable[[str, bool], None] | None = None,
    ) -> PolicyResult:
        """Evaluate a spend request against all policy rules.

        Checks are run in order: amount_cap, vendor_allowlist,
        vendor_blocklist, mcc_filter, daily_budget, justification.
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

        # 4. Daily budget
        if self._daily_spend + amount > self.policy.daily_budget:
            return _fail(
                "daily_budget",
                f"Daily budget exhausted (${self._daily_spend:.2f} / ${self.policy.daily_budget:.2f})",
            )
        _pass("daily_budget")

        # 5. Justification present
        if self.policy.require_justification and not justification:
            return _fail(
                "justification", "Justification is required but was not provided"
            )
        _pass("justification")

        # Approved — increment daily spend
        self._daily_spend += amount

        return PolicyResult(approved=True, checks_passed=checks_passed)
