"""Mock Nano gateway for testing spend governance without a Nano node or key.

``MockNanoGateway`` simulates sending Nano (XNO) to a ``nano_...`` address
by returning a fake block hash, mirroring how ``MockGateway`` fakes cards
and ``MockX402Gateway`` fakes x402 receipts. Use it in tests and demos the
same way you would use the real :class:`paygraph.gateways.nano.NanoGateway`.
"""

import secrets

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.base import BaseGateway
from paygraph.gateways.nano import NanoResult


class MockNanoGateway(BaseGateway):
    """Mock Nano gateway — simulates a send block without a node or key.

    Args:
        auto_approve: If True, skip the terminal approval prompt and
            approve all sends automatically.
        sender: The fake ``nano_...`` address pays are sent from. Defaults
            to a deterministic test address.
    """

    def __init__(
        self,
        auto_approve: bool = False,
        sender: str = "nano_3mockp3141592653589793238464626433832795gu3k3z",
        default_receiver: str = "",
    ) -> None:
        if not sender.startswith("nano_") and not sender.startswith("xrb_"):
            raise ValueError("sender must be a nano_ or xrb_ address")
        if default_receiver and not (
            default_receiver.startswith("nano_")
            or default_receiver.startswith("xrb_")
        ):
            raise ValueError(
                f"default_receiver must be a nano_ address, got {default_receiver!r}"
            )
        self.auto_approve = auto_approve
        self.sender = sender
        self.default_receiver = default_receiver
        self._sends: dict[str, NanoResult] = {}

    def execute(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        receiver: str = "",
    ) -> NanoResult:
        """Simulate sending XNO to ``receiver``, optionally prompting for approval.

        Args:
            amount_cents: Spend amount in cents.
            vendor: Name of the vendor.
            memo: Justification for the send.
            receiver: The ``nano_...`` address to send XNO to. Defaults to
                ``default_receiver`` from the constructor when omitted.

        Returns:
            A ``NanoResult`` with a fake ``block_hash``.

        Raises:
            ValueError: If ``receiver`` is not a ``nano_`` address.
            SpendDeniedError: If the human denies the approval prompt.
        """
        if not receiver:
            receiver = self.default_receiver
        if not receiver.startswith("nano_") and not receiver.startswith("xrb_"):
            raise ValueError(f"receiver must be a nano_ address, got {receiver!r}")

        amount_dollars = amount_cents / 100
        if not self.auto_approve:
            response = input(
                f"[PayGraph] Approve Nano send {amount_dollars:.8f} XNO "
                f"to {receiver} for {vendor}? [Y/n]: "
            )
            if response.strip().lower() not in ("", "y", "yes"):
                raise SpendDeniedError(
                    f"Human denied Nano send of {amount_dollars:.8f} XNO "
                    f"to {receiver} for {vendor}"
                )

        block_hash = f"nano_mock_{secrets.token_hex(16)}"
        result = NanoResult(
            amount_cents=amount_cents,
            gateway_ref=block_hash,
            gateway_type="nano",
            block_hash=block_hash,
            sender=self.sender,
            receiver=receiver,
            amount_raw=str(amount_cents * 10**28),  # 1 XNO = 10^30 raw
        )
        self._sends[block_hash] = result
        return result
