# MCP Server

Expose PayGraph spend controls to any MCP-compatible host (Claude Desktop, Claude Code, Cursor) through a stdio MCP server.

## Install

```bash
pip install paygraph[mcp]
```

## Environment variables

`paygraph-mcp` reads these environment variables on startup:

- `PAYGRAPH_GATEWAY`: `mock`, `stripe`, or `stripe_mpp`
- `PAYGRAPH_API_KEY`: required for `stripe` and `stripe_mpp`
- `PAYGRAPH_DAILY_BUDGET`: optional float (default: `200`)
- `PAYGRAPH_MAX_TRANSACTION`: optional float (default: `50`)
- `PAYGRAPH_AUDIT_LOG_PATH`: optional audit log path (default: `paygraph_audit.jsonl`)

For `stripe_mpp`, also set:

- `STRIPE_MPP_PAYMENT_METHOD`: Stripe PaymentMethod id (`pm_...`)
- `STRIPE_MPP_GRANTEE`: Stripe profile id (`profile_...`)

## Run

```bash
PAYGRAPH_GATEWAY=mock \
PAYGRAPH_DAILY_BUDGET=100 \
PAYGRAPH_MAX_TRANSACTION=25 \
paygraph-mcp
```

## Claude Desktop config

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "paygraph": {
      "command": "paygraph-mcp",
      "env": {
        "PAYGRAPH_GATEWAY": "mock",
        "PAYGRAPH_DAILY_BUDGET": "100",
        "PAYGRAPH_MAX_TRANSACTION": "25"
      }
    }
  }
}
```

The server exposes:

- `paygraph_request_spend(amount, vendor, justification)`
- `paygraph_request_x402(url, amount, vendor, justification, method, headers, body)`
