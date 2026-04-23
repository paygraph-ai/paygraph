import secrets

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.base import BaseGateway, CardResult


class MockGateway(BaseGateway):
    """Mock card gateway for development and testing.

    Generates fake card numbers. When ``auto_approve`` is False (default),
    prompts for human approval in the terminal before issuing a card.
    """

    def __init__(self, auto_approve: bool = False) -> None:
        """Initialize the mock gateway.

        Args:
            auto_approve: If True, skip the terminal approval prompt and
                approve all requests automatically.
        """
        self.auto_approve = auto_approve
        self._cards: dict[str, CardResult] = {}

    def execute(self, amount_cents: int, vendor: str, memo: str) -> CardResult:
        """Create a mock virtual card, optionally prompting for approval.

        Args:
            amount_cents: Spend limit in cents.
            vendor: Name of the vendor.
            memo: Justification for the spend.

        Returns:
            A ``CardResult`` with a fake PAN (``4111111111111111``).

        Raises:
            SpendDeniedError: If the human denies the approval prompt.
        """
        amount_dollars = amount_cents / 100
        if not self.auto_approve:
            response = input(
                f"[PayGraph] Approve ${amount_dollars:.2f} for {vendor}? [Y/n]: "
            )
            if response.strip().lower() not in ("", "y", "yes"):
                raise SpendDeniedError(
                    f"Human denied spend of ${amount_dollars:.2f} for {vendor}"
                )

        token = f"mock_{secrets.token_hex(8)}"
        card = CardResult(
            pan="4111111111111111",
            cvv="123",
            expiry="12/28",
            spend_limit_cents=amount_cents,
            amount_cents=amount_cents,
            gateway_ref=token,
            gateway_type="mock",
        )
        self._cards[token] = card
        return card

    def revoke(self, gateway_ref: str) -> bool:
        """Remove a mock card from the internal store.

        Args:
            gateway_ref: The ``gateway_ref`` of the card to revoke.

        Returns:
            True if the card existed and was removed, False otherwise.
        """
        return self._cards.pop(gateway_ref, None) is not None
