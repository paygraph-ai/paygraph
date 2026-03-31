from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VirtualCard:
    """A virtual card returned by a card gateway.

    Attributes:
        pan: Primary Account Number (full card number). **Treat as sensitive.**
        cvv: Card Verification Value.
        expiry: Expiration date in ``MM/YY`` format.
        spend_limit_cents: Maximum spend limit in cents.
        gateway_ref: Unique reference ID from the gateway.
        gateway_type: Gateway identifier (e.g. ``"mock"``, ``"stripe_test"``).
    """

    pan: str
    cvv: str
    expiry: str
    spend_limit_cents: int
    gateway_ref: str
    gateway_type: str

    def redacted(self) -> "VirtualCard":
        """Return a copy with the PAN masked, showing only the last 4 digits.

        Returns:
            A new ``VirtualCard`` with ``pan`` set to ``****XXXX``.
        """
        return VirtualCard(
            pan=f"****{self.pan[-4:]}",
            cvv=self.cvv,
            expiry=self.expiry,
            spend_limit_cents=self.spend_limit_cents,
            gateway_ref=self.gateway_ref,
            gateway_type=self.gateway_type,
        )


class BaseGateway(ABC):
    """Abstract base class for card payment gateways.

    Subclass this to implement a custom card gateway. You must implement
    ``execute_spend()`` and ``revoke()``.
    """

    @abstractmethod
    def execute_spend(self, amount_cents: int, vendor: str, memo: str) -> VirtualCard:
        """Create a virtual card for the given spend.

        Args:
            amount_cents: Spend limit in cents.
            vendor: Name of the vendor.
            memo: Justification or memo for the spend.

        Returns:
            A ``VirtualCard`` with the card details.
        """
        ...

    @abstractmethod
    def revoke(self, gateway_ref: str) -> bool:
        """Revoke (cancel) a previously issued virtual card.

        Args:
            gateway_ref: The ``gateway_ref`` from the ``VirtualCard``.

        Returns:
            True if the card was successfully revoked, False if not found.
        """
        ...
