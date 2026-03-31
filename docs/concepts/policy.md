# Policy Engine

The policy engine is the core of PayGraph's spend governance. Every spend request — whether card or x402 — passes through the same `PolicyEngine` before reaching a gateway.

## How It Works

```
request_spend() / request_x402()
        │
        ▼
   PolicyEngine.evaluate()
        │
   ┌────┴────┐
   │ Checks  │  amount_cap → vendor_allowlist → vendor_blocklist
   │ (ordered)│  → mcc_filter → daily_budget → justification
   └────┬────┘
        │
   ┌────┴────┐
   │approved │──▶ Gateway (card or x402)
   │denied   │──▶ PolicyViolationError
   └─────────┘
```

## SpendPolicy

`SpendPolicy` is a dataclass that holds all configuration:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_transaction` | `float` | `50.0` | Maximum dollar amount per transaction |
| `daily_budget` | `float` | `200.0` | Maximum total spend per calendar day |
| `allowed_vendors` | `list[str] \| None` | `None` | If set, only these vendors are permitted |
| `blocked_vendors` | `list[str] \| None` | `None` | If set, these vendors are always blocked |
| `allowed_mccs` | `list[int] \| None` | `None` | Reserved for MCC filtering (future) |
| `require_justification` | `bool` | `True` | Whether a justification string is required |

## Daily Budget Tracking

The `PolicyEngine` is **stateful** — it tracks cumulative daily spend in memory. The counter resets automatically at the start of each new calendar day.

```python
from paygraph import AgentWallet, SpendPolicy

wallet = AgentWallet(policy=SpendPolicy(daily_budget=10.0))
wallet.request_spend(6.0, "Vendor A", "First purchase")   # OK ($6 used)
wallet.request_spend(3.0, "Vendor B", "Second purchase")   # OK ($9 used)
wallet.request_spend(2.0, "Vendor C", "Third purchase")    # DENIED (would exceed $10)
```

## Vendor Matching

Vendor matching is case-insensitive and uses substring matching:

- Allowed vendor `"AWS"` matches `"AWS Lambda"`, `"aws s3"`, etc.
- Blocked vendor `"Casino"` matches `"Online Casino"`, `"casino.com"`, etc.

## PolicyResult

Every evaluation returns a `PolicyResult`:

- `approved` — Whether the request passed all checks
- `denial_reason` — Human-readable reason if denied
- `checks_passed` — List of check names that passed before denial (or all if approved)

## API Reference

- [`SpendPolicy`](../api/policy.md)
- [`PolicyEngine`](../api/policy-engine.md)
