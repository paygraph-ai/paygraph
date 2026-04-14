# Roadmap

This is the contributor-facing roadmap for PayGraph. Items are grouped by how close they are to landing. Everything here is open for contribution — see the bottom of this file for how to get involved.

## Shipping now (2–4 weeks)

These are actively being worked on or are ready to pick up.

### MCP server
Add `paygraph/mcp_server.py` exposing spend and x402 tools as an MCP server. New installable extra: `paygraph[mcp]`.

### HITL Slack webhook
Async human-in-the-loop approval channel that replaces the terminal `input()` prompt in MockGateway. Lets teams approve spend requests from Slack instead of a local terminal.

### Cross-gateway budget tracker
Unified daily budget tracking across card and x402 gateways. Requires shared budget state between `policy.py` and `wallet.py` so a single `SpendPolicy` governs total agent spend regardless of payment rail.

### Better StripeCardGateway error handling
Map Stripe API errors to specific PayGraph exception types (e.g. card declined, insufficient funds, rate limited) instead of generic `GatewayError`.

### Additional payment integrations
Ramp integration first, then Lithic / Marqeta / Brex following the same `BaseGateway` pattern. Each new gateway needs a corresponding mock variant for testing.

## Exploring (1–3 months)

Design is in progress or needs more input before implementation starts.

### KYC/AML gate
Pre-payment compliance hook that runs before the gateway executes. Could be a new policy check or a standalone middleware step.

### Async gateway variants
`AsyncAgentWallet` and `BaseAsyncGateway` for non-blocking payment flows. Important for high-throughput agent systems.

### More framework integrations
Adapters for Vercel AI SDK, AutoGen, Pydantic AI, and Semantic Kernel — similar to the existing LangGraph and CrewAI integrations.

## Ideas welcomed (longer horizon)

These are directions we think are interesting but don't have concrete designs for yet. If any of these excite you, open an issue to start the conversation.

- **A2A / SEPA / RTP rails** — non-card, non-crypto payment networks
- **Agent-to-agent payment flows** — agents paying other agents with policy enforcement on both sides
- **Native MCP tool registry** — auto-register PayGraph tools in MCP-compatible agent runtimes

## How to contribute to roadmap items

**Shipping now** — These are ready to build. Comment on the relevant issue (or open one) to claim it, then submit a PR. Check [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

**Exploring** — Start by opening an issue with your proposed design. We'll discuss the approach before any code is written.

**Ideas welcomed** — Open an issue describing the use case and how you'd approach it. The more concrete the proposal, the faster it moves up the roadmap.

Questions? Reach out on [Discord](https://discord.gg/PPVZWSMdEm) or open a [GitHub Issue](https://github.com/paygraph-ai/paygraph/issues).
