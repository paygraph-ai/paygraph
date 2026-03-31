# Audit Logging

Every spend request — approved or denied — is recorded in a structured JSONL audit trail.

## How It Works

The `AuditLogger` writes one JSON line per request to a log file. Each line is an `AuditRecord` containing:

- **Who** — `agent_id` identifies which agent made the request
- **What** — `amount`, `vendor`, `justification`
- **When** — ISO 8601 UTC `timestamp`
- **Result** — `policy_result` ("approved" or "denied"), `denial_reason`
- **Checks** — `checks_passed` lists which policy checks succeeded
- **Gateway** — `gateway_ref` and `gateway_type` (for approved requests)

## Example Log Entry

```json
{
  "timestamp": "2025-01-15T10:30:00.123456+00:00",
  "agent_id": "research-agent",
  "amount": 4.2,
  "vendor": "Anthropic API",
  "justification": "Need Claude tokens for analysis",
  "policy_result": "approved",
  "denial_reason": null,
  "checks_passed": ["amount_cap", "vendor_allowlist", "vendor_blocklist", "mcc_filter", "daily_budget", "justification"],
  "gateway_ref": "mock_a1b2c3d4",
  "gateway_type": "mock"
}
```

## Configuration

```python
from paygraph import AgentWallet

wallet = AgentWallet(
    agent_id="my-agent",                  # Identifies the agent in logs
    log_path="logs/spend_audit.jsonl",    # Custom log file path
    verbose=True,                          # Print results to stdout
    animate=False,                         # Animate policy checks in terminal
)
```

## Verbose Output

When `verbose=True` (default), the logger prints formatted output to the terminal showing each policy check as it runs, followed by the approval or denial result.

When `animate=True`, a short delay is added between each check for visual effect (useful for demos).

## Querying the Audit Log

The log is plain JSONL — one JSON object per line. Use standard tools:

```bash
# All denied requests
cat paygraph_audit.jsonl | python -m json.tool --json-lines | grep '"denied"'

# Total approved spend
python -c "
import json
total = sum(
    r['amount'] for r in (json.loads(l) for l in open('paygraph_audit.jsonl'))
    if r['policy_result'] == 'approved'
)
print(f'Total approved: \${total:.2f}')
"
```

## API Reference

- [`AuditRecord`](../api/wallet.md) — structured log entry
- [`AuditLogger`](../api/wallet.md) — writes JSONL + optional stdout
