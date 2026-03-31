from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VirtualCard:
    pan: str
    cvv: str
    expiry: str
    spend_limit_cents: int
    gateway_ref: str
    gateway_type: str

    def redacted(self) -> "VirtualCard":
        """Return a copy with masked PAN (only last 4 digits visible)."""
        return VirtualCard(
            pan=f"****{self.pan[-4:]}",
            cvv=self.cvv,
            expiry=self.expiry,
            spend_limit_cents=self.spend_limit_cents,
            gateway_ref=self.gateway_ref,
            gateway_type=self.gateway_type,
        )


class BaseGateway(ABC):
    """Abstract base for payment gateways."""

    @abstractmethod
    def execute_spend(self, amount_cents: int, vendor: str, memo: str) -> VirtualCard:
        ...

    @abstractmethod
    def revoke(self, gateway_ref: str) -> bool:
        ...
