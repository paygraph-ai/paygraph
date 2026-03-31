# Gateways

Gateways execute the actual payment after policy approval. PayGraph supports two payment rails with multiple gateway implementations.

## Card Gateways

Card gateways implement `BaseGateway` and return a `VirtualCard`.

### MockGateway

For development and testing. Generates fake card numbers and optionally prompts for human approval.

```python
from paygraph import AgentWallet, MockGateway

# Auto-approve (no terminal prompt)
wallet = AgentWallet(gateway=MockGateway(auto_approve=True))

# Human-in-the-loop (default)
wallet = AgentWallet(gateway=MockGateway())
```

When `auto_approve=False` (default), the gateway prints a terminal prompt asking for approval before issuing the card.

### StripeCardGateway

Creates real single-use virtual cards via Stripe Issuing.

```python
from paygraph import AgentWallet, StripeCardGateway

wallet = AgentWallet(
    gateway=StripeCardGateway(
        api_key="sk_test_...",
        currency="usd",
        single_use=True,
    ),
)
```

Features:

- Auto-detects test vs live mode from the API key prefix
- Creates or reuses a Stripe cardholder automatically
- Supports MCC allowlist/blocklist at the card level
- Single-use mode (default) creates a new card per transaction

### Writing a Custom Gateway

Subclass `BaseGateway` and implement two methods:

```python
from paygraph.gateways.base import BaseGateway, VirtualCard

class MyGateway(BaseGateway):
    def execute_spend(self, amount_cents: int, vendor: str, memo: str) -> VirtualCard:
        # Your payment logic here
        return VirtualCard(
            pan="4111...",
            cvv="123",
            expiry="12/28",
            spend_limit_cents=amount_cents,
            gateway_ref="my_ref_123",
            gateway_type="my_gateway",
        )

    def revoke(self, gateway_ref: str) -> bool:
        # Cancel/revoke the card
        return True
```

## x402 Gateways

x402 gateways handle HTTP 402 payments using the Coinbase x402 protocol. They return an `X402Receipt`.

### X402Gateway

Makes real on-chain USDC payments via the x402 protocol.

```python
from paygraph import AgentWallet, X402Gateway

wallet = AgentWallet(
    x402_gateway=X402Gateway(evm_private_key="0x..."),
)

body = wallet.request_x402(
    url="https://api.example.com/paid-endpoint",
    amount=0.50,
    vendor="Example API",
    justification="Fetching premium data",
)
```

Supports both EVM (Base, Ethereum, etc.) and Solana:

```python
gateway = X402Gateway(
    evm_private_key="0x...",     # For EVM chains
    svm_private_key="base58...", # For Solana
)
```

At least one key must be provided.

### MockX402Gateway

For testing x402 flows without blockchain access.

```python
from paygraph import AgentWallet, MockX402Gateway

wallet = AgentWallet(
    x402_gateway=MockX402Gateway(
        auto_approve=True,
        response_body='{"data": "mock"}',
    ),
)
```

## API Reference

- [`BaseGateway`](../api/gateways.md#paygraph.gateways.base.BaseGateway)
- [`MockGateway`](../api/gateways.md#paygraph.gateways.mock.MockGateway)
- [`StripeCardGateway`](../api/gateways.md#paygraph.gateways.stripe.StripeCardGateway)
- [`X402Gateway`](../api/gateways.md#paygraph.gateways.x402.X402Gateway)
- [`MockX402Gateway`](../api/gateways.md#paygraph.gateways.mock_x402.MockX402Gateway)
- [`VirtualCard`](../api/virtual-card.md)
