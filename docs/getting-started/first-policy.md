# Your First Policy

Policies are the rules that govern what your agent can spend. Every spend request passes through the `PolicyEngine` before reaching a gateway.

## Default Policy

```python
from paygraph import SpendPolicy

policy = SpendPolicy()
# max_transaction=50.0, daily_budget=200.0, require_justification=True
```

## Configuring Limits

```python
policy = SpendPolicy(
    max_transaction=10.0,    # No single spend over $10
    daily_budget=50.0,       # Total daily spend capped at $50
)
```

## Vendor Controls

### Allowlist — Only Permit Specific Vendors

```python
policy = SpendPolicy(
    allowed_vendors=["Anthropic", "OpenAI", "AWS"],
)
```

When set, only vendors matching these names (case-insensitive substring match) are permitted.

### Blocklist — Block Specific Vendors

```python
policy = SpendPolicy(
    blocked_vendors=["Casino", "Gambling"],
)
```

Blocks any vendor whose name contains a blocked term.

!!! note
    You can use both `allowed_vendors` and `blocked_vendors` together. The allowlist is checked first.

## Justification Requirement

By default, agents must provide a justification for every spend. Disable it for automated pipelines:

```python
policy = SpendPolicy(require_justification=False)
```

## Policy Check Order

The `PolicyEngine` evaluates checks in this order:

1. **Amount cap** — Is the amount within `max_transaction`?
2. **Vendor allowlist** — Is the vendor in the allowed list (if set)?
3. **Vendor blocklist** — Is the vendor blocked?
4. **MCC filter** — Merchant Category Code check (reserved for future use)
5. **Daily budget** — Is there remaining budget for today?
6. **Justification** — Was a justification provided (if required)?

If any check fails, the request is denied immediately and subsequent checks are skipped.

## Handling Denials

```python
from paygraph import AgentWallet, SpendPolicy
from paygraph.exceptions import PolicyViolationError

wallet = AgentWallet(policy=SpendPolicy(max_transaction=5.0))

try:
    wallet.request_spend(10.0, "Vendor", "Too expensive")
except PolicyViolationError as e:
    print(f"Denied: {e}")
    # Denied: Amount $10.00 exceeds limit of $5.00
```

## Next Steps

- [Policy Engine concept guide](../concepts/policy.md) — Deep dive into the engine internals
- [SpendPolicy API reference](../api/policy.md) — All fields documented
- [PolicyEngine API reference](../api/policy-engine.md) — Evaluation logic
