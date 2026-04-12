import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

_DIM = "\033[2m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"
_CHECK = "\u2713"
_CROSS = "\u2717"

_CHECK_LABELS = {
    "amount_cap": "Amount cap",
    "vendor_allowlist": "Vendor allowlist",
    "vendor_blocklist": "Vendor blocklist",
    "mcc_filter": "MCC filter",
    "daily_budget": "Daily budget",
    "justification": "Justification",
}


@dataclass
class AuditRecord:
    """Structured audit log entry for a spend request.

    Attributes:
        timestamp: ISO 8601 UTC timestamp of the request.
        agent_id: Identifier of the agent that made the request.
        amount: Dollar amount of the spend request.
        vendor: Name of the vendor or service.
        justification: Reason provided for the spend (may be None).
        policy_result: ``"approved"`` or ``"denied"``.
        denial_reason: Human-readable reason if denied, else None.
        checks_passed: Names of policy checks that passed.
        gateway_ref: Gateway reference ID (for approved requests).
        gateway_type: Gateway type string (e.g. ``"mock"``, ``"stripe_test"``).
    """

    timestamp: str
    agent_id: str
    amount: float
    vendor: str
    justification: str | None
    policy_result: str  # "approved" or "denied"
    denial_reason: str | None
    checks_passed: list[str]
    gateway_ref: str | None
    gateway_type: str | None

    @classmethod
    def now(
        cls,
        agent_id: str,
        amount: float,
        vendor: str,
        justification: str | None,
        policy_result: str,
        denial_reason: str | None = None,
        checks_passed: list[str] | None = None,
        gateway_ref: str | None = None,
        gateway_type: str | None = None,
    ) -> "AuditRecord":
        """Create an AuditRecord with the current UTC timestamp.

        Args:
            agent_id: Identifier of the agent.
            amount: Dollar amount of the request.
            vendor: Vendor name.
            justification: Spend justification.
            policy_result: ``"approved"`` or ``"denied"``.
            denial_reason: Reason for denial, if applicable.
            checks_passed: List of passed policy check names.
            gateway_ref: Gateway reference ID.
            gateway_type: Gateway type string.

        Returns:
            A new ``AuditRecord`` with ``timestamp`` set to now (UTC).
        """
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            amount=amount,
            vendor=vendor,
            justification=justification,
            policy_result=policy_result,
            denial_reason=denial_reason,
            checks_passed=checks_passed or [],
            gateway_ref=gateway_ref,
            gateway_type=gateway_type,
        )


class AuditLogger:
    """Writes structured JSONL audit trail with optional terminal output.

    Each call to ``log()`` appends one JSON line to the log file and
    optionally prints a formatted result to stdout.
    """

    def __init__(
        self,
        log_path: str = "paygraph_audit.jsonl",
        verbose: bool = True,
        animate: bool = False,
    ) -> None:
        """Initialize the audit logger.

        Args:
            log_path: File path for the JSONL audit log.
            verbose: If True, print formatted results to stdout.
            animate: If True, add a short delay between policy check
                outputs for visual effect.
        """
        self.log_path = log_path
        self.verbose = verbose
        self.animate = animate

    def start_request(self, amount: float, vendor: str) -> Callable[[str, bool], None]:
        """Print a formatted request header and return a live check callback.

        The returned callback can be passed to ``PolicyEngine.evaluate()``
        as ``on_check`` to display each policy check result in real time.

        Args:
            amount: Dollar amount of the request.
            vendor: Vendor name.

        Returns:
            A callback ``(check_name: str, passed: bool) -> None``.
        """
        print()
        print(f"  {_DIM}{'─' * 50}{_RESET}")
        print(
            f"  {_BOLD}Spend Request{_RESET}  ${amount:.2f} → {_CYAN}{vendor}{_RESET}"
        )
        print(f"  {_DIM}{'─' * 50}{_RESET}")
        print()
        sys.stdout.flush()

        animate = self.animate

        def on_check(name: str, passed: bool) -> None:
            label = _CHECK_LABELS.get(name, name)
            if passed:
                print(f"    {_GREEN}{_CHECK}{_RESET}  {label}")
            else:
                print(f"    {_RED}{_CROSS}  {label}{_RESET}")
            sys.stdout.flush()
            if animate:
                time.sleep(0.15)

        return on_check

    def log(self, record: AuditRecord) -> None:
        """Write an audit record to the JSONL log file.

        If ``verbose`` is True, also prints a formatted result to stdout.

        Args:
            record: The ``AuditRecord`` to log.
        """
        with open(self.log_path, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

        if self.verbose:
            self._print_result(record)

    def _print_result(self, record: AuditRecord) -> None:
        print()
        if record.policy_result == "approved":
            print(f"  {_GREEN}{_BOLD}{_CHECK} APPROVED{_RESET}")
            if record.gateway_ref:
                print(f"  {_DIM}Card ref: {record.gateway_ref}{_RESET}")
            if record.gateway_type:
                print(f"  {_DIM}Gateway:  {record.gateway_type}{_RESET}")
        else:
            print(f"  {_RED}{_BOLD}{_CROSS} DENIED{_RESET}")
            if record.denial_reason:
                print(f"  {_YELLOW}Reason: {record.denial_reason}{_RESET}")

        print(f"  {_DIM}{'─' * 50}{_RESET}")
        print()
