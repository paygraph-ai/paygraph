# Quickstart

Get from zero to a working spend-governed agent in 5 steps.

## 1. Install

```bash
pip install paygraph
```

## 2. Create a Wallet

```python
from paygraph import AgentWallet, SpendPolicy

wallet = AgentWallet(
    policy=SpendPolicy(
        max_transaction=25.0,   # Max $25 per transaction
        daily_budget=100.0,     # Max $100 per day
    ),
)
```

The default `MockGateway` prompts you for approval in the terminal — no API keys needed.

## 3. Make a Spend Request

```python
result = wallet.request_spend(
    amount=4.20,
    vendor="Anthropic API",
    justification="Need Claude tokens for analysis",
)
print(result)
# Card approved. PAN: 4111111111111111, CVV: 123, Expiry: 12/28
```

## 4. Check the Audit Log

Every request is logged to `paygraph_audit.jsonl`:

```json
{
  "timestamp": "2025-01-15T10:30:00+00:00",
  "agent_id": "default",
  "amount": 4.2,
  "vendor": "Anthropic API",
  "justification": "Need Claude tokens for analysis",
  "policy_result": "approved",
  "checks_passed": ["amount_cap", "vendor_allowlist", "vendor_blocklist", "mcc_filter", "daily_budget", "justification"]
}
```

## 5. Try the CLI Demo

```bash
paygraph demo
```

This runs a fully interactive demo with a mock LLM and mock gateway. No API keys required.

For a live demo with a real LLM:

```bash
pip install "paygraph[live]"
paygraph demo --live --model anthropic
```

## Next Steps

- [Your First Policy](first-policy.md) — Fine-tune spend rules
- [Gateways](../concepts/gateways.md) — Connect Stripe or x402
- [API Reference](../api/wallet.md) — Full `AgentWallet` docs
