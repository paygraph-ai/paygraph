import secrets

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.base import BaseGateway, VirtualCard


class MockGateway(BaseGateway):
    def __init__(self, auto_approve: bool = False) -> None:
        self.auto_approve = auto_approve
        self._cards: dict[str, VirtualCard] = {}

    def execute_spend(self, amount_cents: int, vendor: str, memo: str) -> VirtualCard:
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
        card = VirtualCard(
            pan="4111111111111111",
            cvv="123",
            expiry="12/28",
            spend_limit_cents=amount_cents,
            gateway_ref=token,
            gateway_type="mock",
        )
        self._cards[token] = card
        return card

    def revoke(self, gateway_ref: str) -> bool:
        return self._cards.pop(gateway_ref, None) is not None
