import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, asdict
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
    def __init__(
        self, log_path: str = "paygraph_audit.jsonl", verbose: bool = True, animate: bool = False,
    ) -> None:
        self.log_path = log_path
        self.verbose = verbose
        self.animate = animate

    def start_request(self, amount: float, vendor: str) -> Callable[[str, bool], None]:
        """Print request header and return a callback for live check output."""
        print()
        print(f"  {_DIM}{'─' * 50}{_RESET}")
        print(f"  {_BOLD}Spend Request{_RESET}  ${amount:.2f} → {_CYAN}{vendor}{_RESET}")
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
