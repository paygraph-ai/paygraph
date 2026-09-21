"""Nano (XNO) payment gateway for PayGraph.

PayGraph's ``NanoGateway`` lets an agent settle a spend directly in Nano
(XNO) by building and broadcasting a send block to a vendor's ``nano_...``
address, returning the on-chain block hash as the gateway reference. It is
the Nano rail of ``StripeMPPGateway`` (which binds spend to a merchant):
the agent holds its own Nano account and moves XNO straight to the vendor
with no processor, no network fee, and sub-second finality.

Nano has no transaction fees and settles in about half a second, so an
agent can re-fund a small balance or pay a per-call price without the
per-transaction cost of a card network. Because the network is
peer-to-peer and permissionless, there is no issuer that can freeze funds
or reverse a send.

The real gateway imports ``nanopy`` lazily — it only needs to be installed
when this gateway is used (``pip install paygraph[nano]``). For testing
without a node or wallet use :class:`MockNanoGateway` from
``paygraph.gateways.mock_nano``.
"""

from dataclasses import dataclass

from paygraph.exceptions import GatewayError
from paygraph.gateways.base import BaseGateway, SpendResult


@dataclass
class NanoResult(SpendResult):
    """Result of a successful Nano (XNO) payment.

    Attributes:
        block_hash: The on-chain hash of the broadcast send block.
        sender: The ``nano_...`` address the XNO was sent from.
        receiver: The ``nano_...`` address the XNO was sent to.
        amount_raw: The amount moved in raw (1 XNO = 10^30 raw).
    """

    block_hash: str = ""
    sender: str = ""
    receiver: str = ""
    amount_raw: str = ""


class NanoGateway(BaseGateway):
    """Gateway that settles a spend by sending Nano (XNO) to a vendor address.

    Builds and broadcasts a Nano send block using ``nanopy``, which is
    imported lazily (install with ``pip install paygraph[nano]``). The
    agent holds its own Nano account key; the private key is provided at
    construction and never leaves the process.

    Args:
        seed: Hex-encoded Nano seed (64 hex chars) whose first index is
            the account that pays.
        rpc_url: Nano node RPC URL used to broadcast the send block.
            Defaults to a public node.
    """

    def __init__(
        self,
        seed: str,
        rpc_url: str = "https://rpc.nano.to",
    ) -> None:
        if not seed or len(seed) != 64:
            raise ValueError("Nano seed must be a 64-character hex string")

        try:
            import nanopy
        except ImportError:
            raise ImportError(
                "Nano integration requires nanopy. "
                "Install it with: pip install paygraph[nano]"
            )

        from nanopy.rpc import HTTP

        self._seed = seed
        self._rpc = HTTP(rpc_url)
        self._account = nanopy.Account()
        self._account.sk = nanopy.deterministic_key(seed)

    @property
    def sender(self) -> str:
        """The ``nano_...`` address that pays (seed index 0)."""
        return self._account.addr

    def execute(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        receiver: str,
        amount_xno: float | None = None,
        work: str = "",
    ) -> NanoResult:
        """Send XNO to ``receiver`` and return the block hash.

        Fetches the sender account's current on-chain state (frontier and
        confirmed balance) from the node, then builds, signs and broadcasts
        a send block. Nano send blocks require proof-of-work; pass a valid
        ``work`` value (e.g. from a distributed work server) to avoid
        blocking on local PoW generation.

        Args:
            amount_cents: Spend amount in cents (used for the audit and
                policy commit, and to price the XNO when ``amount_xno`` is
                not given).
            vendor: Name of the vendor or service.
            memo: Justification for the send.
            receiver: The ``nano_...`` address to send XNO to.
            amount_xno: Exact XNO amount to send. When omitted, the cents
                amount is interpreted as XNO micro-units (1 cent -> 0.01
                XNO) for a convenient default.
            work: Optional precomputed work string (16 hex chars). When
                omitted, the gateway generates work locally, which can be
                slow.

        Returns:
            A ``NanoResult`` with the broadcast block hash and addresses.

        Raises:
            GatewayError: If the send block could not be built or broadcast
                (insufficient balance, node error, or no block hash).
        """
        if not receiver.startswith("nano_") and not receiver.startswith("xrb_"):
            raise ValueError(f"receiver must be a nano_ address, got {receiver!r}")

        if amount_xno is None:
            amount_xno = amount_cents / 100

        import nanopy

        try:
            info = self._rpc.account_info(self.sender) or {}
            # account_info: {"frontier": ..., "confirmed_balance": "...", "representative": ...}
            frontier = info.get("frontier", "")
            confirmed_balance_raw = info.get("confirmed_balance", "0")
            representative = info.get("representative", self.sender)

            if not frontier:
                raise GatewayError(
                    "Nano sender account has no confirmed frontier — "
                    "a fresh account must receive its first block before sending"
                )

            self._account.state = (
                frontier,
                int(confirmed_balance_raw),
                nanopy.Account(addr=representative),
            )

            to = nanopy.Account(addr=receiver)
            raw_amt = nanopy.Network().to_raw(f"{amount_xno:.12f}")
            block = self._account.send(to, raw_amt, work=work)
            result = self._rpc.process(block.dict_, subtype="send")
            block_hash = result.get("hash", "")
        except GatewayError:
            raise
        except Exception as e:  # noqa: BLE001 — surface any node/block error
            raise GatewayError(f"Nano send failed: {e}") from e

        if not block_hash:
            raise GatewayError("Nano node returned no block hash for the send")

        return NanoResult(
            amount_cents=amount_cents,
            gateway_ref=block_hash,
            gateway_type="nano",
            block_hash=block_hash,
            sender=self.sender,
            receiver=receiver,
            amount_raw=str(nanopy.Network().to_raw(f"{amount_xno:.12f}")),
        )
