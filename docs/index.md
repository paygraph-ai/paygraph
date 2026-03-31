# PayGraph

**Open-source spend governance for AI agents.**

PayGraph gives your AI agents the ability to spend money — safely. It supports two payment rails:

- **Virtual Cards** — Single-use Stripe Issuing cards for traditional purchases
- **x402 Payments** — On-chain USDC payments for x402-enabled API endpoints

Both rails share the same policy enforcement, audit logging, and human-in-the-loop approval.

## Quick Install

```bash
pip install paygraph
```

## Card Example

```python
from paygraph import AgentWallet, SpendPolicy

wallet = AgentWallet(
    policy=SpendPolicy(max_transaction=25.0, daily_budget=100.0),
)

# Policy-checked spend → returns virtual card details
result = wallet.request_spend(
    amount=4.20,
    vendor="Anthropic API",
    justification="Need Claude tokens to complete analysis",
)
print(result)
```

## x402 Example

```python
from paygraph import AgentWallet, SpendPolicy, X402Gateway

wallet = AgentWallet(
    x402_gateway=X402Gateway(evm_private_key="0x..."),
    policy=SpendPolicy(max_transaction=10.0),
)

# Policy-checked x402 payment → returns response body
body = wallet.request_x402(
    url="https://api.example.com/paid-endpoint",
    amount=0.50,
    vendor="Example API",
    justification="Fetching premium data for report",
)
```

## LangGraph Integration

PayGraph exposes `spend_tool` and `x402_tool` as LangChain-compatible tools:

```python
from paygraph import AgentWallet

wallet = AgentWallet()
tools = [wallet.spend_tool]  # Add to your LangGraph agent
```

## Next Steps

- [Installation](getting-started/installation.md) — All install options
- [Quickstart](getting-started/quickstart.md) — Zero to working in 5 steps
- [Concepts](concepts/policy.md) — How policies, gateways, and auditing work
- [API Reference](api/wallet.md) — Full API documentation
