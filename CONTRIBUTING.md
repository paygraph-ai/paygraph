# Contributing to PayGraph

Thanks for your interest in contributing to PayGraph! This guide will help you get set up and start contributing.

## Getting started

```bash
git clone https://github.com/paygraph-ai/paygraph.git
cd paygraph
uv pip install -e ".[dev,langgraph,x402]"
make test
paygraph demo --live
```

## Project structure

```
src/paygraph/
  wallet.py        # AgentWallet — main orchestrator (spend_tool, x402_tool, crewai_tool)
  policy.py        # SpendPolicy config + PolicyEngine (stateful daily spend tracking)
  audit.py         # AuditLogger — structured JSONL audit trail with terminal callbacks
  exceptions.py    # PayGraphError base → SpendDeniedError, PolicyViolationError, GatewayError
  cli.py           # `paygraph demo` command (--live, --stripe, --stripe-mpp)
  gateways/
    base.py        # BaseGateway abstract class (strategy pattern)
    mock.py        # MockGateway — fake cards for dev/testing
    mock_x402.py   # MockX402Gateway — x402 testing without blockchain
    stripe.py      # StripeCardGateway — virtual cards via Stripe Issuing API
    stripe_mpp.py  # StripeMPPGateway — scoped Shared Payment Tokens
    x402.py        # X402Gateway — USDC payments on EVM / Solana

tests/
  test_policy.py           # Policy engine rules
  test_wallet.py           # AgentWallet orchestration
  test_audit.py            # Audit logging
  test_mock_gateway.py     # Mock gateway behavior
  test_stripe_gateway.py   # Stripe Issuing integration
  test_stripe_mpp_gateway.py  # Stripe MPP integration
  test_x402.py             # x402 protocol
  test_crewai.py           # CrewAI tool adapter
```

## Running tests

Run the full suite:

```bash
make test
```

Run a single file:

```bash
uv run pytest tests/test_policy.py -v
```

Run a single test class or method:

```bash
uv run pytest tests/test_policy.py::TestAmountCap::test_under_limit -v
```

CI runs against Python 3.10–3.13.

**Test conventions:**
- Class-based test organization (e.g. `TestAmountCap`, `TestDailyBudget`)
- `_make_wallet()` helper creates a temp audit file and returns a configured wallet
- `_read_audit()` reads back audit records from the temp file
- Use `unittest.mock.patch` for date/time mocking — no conftest or shared fixtures

## Code style

```bash
make format   # auto-format with ruff (style + import sorting)
make lint     # lint with ruff
make check    # CI entrypoint (lint + test)
```

- **Ruff** rules: E, F, I (E501 ignored). Line length 88.
- **Google-style docstrings** (used by mkdocstrings for the docs site)
- **Type hints** throughout — Python 3.10+
- **Dataclasses** for data structures, **Pydantic** for validation

## Submitting a PR

1. Branch from `main`
2. Write tests for new behavior
3. Run `make check` and fix any failures
4. Open a PR with a clear description of what changed and why
5. If adding a new gateway, include a mock variant for testing

## What to work on

Check the [ROADMAP.md](ROADMAP.md) for planned features at every level of complexity. Issues tagged [`good first issue`](https://github.com/paygraph-ai/paygraph/labels/good%20first%20issue) are a great starting point.

## Communication

- **GitHub Issues** — bug reports, feature requests, and discussion
- **Discord** — [join the community](https://discord.gg/PPVZWSMdEm)
