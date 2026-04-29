# MCP Server

PayGraph can run as an [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server, allowing any MCP-compatible AI client to request virtual cards and check budget status.

## Installation

```bash
pip install paygraph[mcp]
```

## Running the Server

### Stdio transport (for Claude Desktop, Cursor)

```bash
paygraph mcp serve
```

### HTTP transport (for remote deployment)

```bash
paygraph mcp serve --transport http --port 8080
```

### Options

| Flag | Description |
|------|-------------|
| `--transport` | Transport protocol: `stdio` or `http` (default: `stdio`) |
| `--port` | Port for HTTP transport (default: `8080`) |

## Available Tools

| Tool | Description |
|------|-------------|
| `mint_virtual_card` | Request a policy-checked virtual card for a purchase |
| `check_budget_status` | Check remaining budget across all time periods |
| `get_policy_info` | Get current spend policy configuration |

### `mint_virtual_card`

| Parameter | Type | Description |
|-----------|------|-------------|
| `amount` | float | Dollar amount to spend (e.g. 4.20 for $4.20) |
| `vendor` | string | Name of the vendor or service |
| `justification` | string | Explanation of why this purchase is necessary |

### `check_budget_status`

No parameters. Returns a summary of daily spend, remaining budget, and any configured time-based limits (hourly, weekly, monthly).

### `get_policy_info`

No parameters. Returns the current policy settings including max transaction limit, daily budget, justification requirements, and vendor allow/block lists.

## Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "paygraph": {
      "command": "paygraph",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Cursor Configuration

Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "paygraph": {
      "command": "paygraph",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Programmatic Usage

You can also create an MCP server programmatically with a custom wallet:

```python
from paygraph import AgentWallet, SpendPolicy
from paygraph.mcp_server import create_mcp_server
from paygraph.gateways.mock import MockGateway

wallet = AgentWallet(
    gateway=MockGateway(auto_approve=True),
    policy=SpendPolicy(
        max_transaction=100.0,
        daily_budget=500.0,
        blocked_vendors=["doordash"],
    ),
)

mcp = create_mcp_server(wallet=wallet)
mcp.run(transport="stdio")
```
