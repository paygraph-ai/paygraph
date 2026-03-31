# PayGraph

[![Tests](https://github.com/Allenbrd/paygraph/actions/workflows/test.yml/badge.svg)](https://github.com/Allenbrd/paygraph/actions/workflows/test.yml)

**Open-source spend governance for AI agents.** Issue single-use virtual cards with policy enforcement, audit logging, and human-in-the-loop approval.

## Architecture

```
 Agent (LangGraph / any framework)
   │
   ▼
 ┌─────────────────────────────┐
 │        AgentWallet           │
 │  ┌───────────┐ ┌──────────┐ │
 │  │  Policy    │ │  Audit   │ │
 │  │  Engine    │ │  Logger  │ │
 │  └─────┬─────┘ └────┬─────┘ │
 │        │             │       │
 │  ┌─────▼─────────────▼─────┐ │
 │  │      BaseGateway        │ │
 │  │  MockGateway │ Stripe   │ │
 │  └─────────────────────────┘ │
 └─────────────────────────────┘
```

## Install

```bash
pip install paygraph
```

With LangGraph support:

```bash
pip install paygraph[langgraph]
```

With live demo (includes LLM providers):

```bash
pip install paygraph[live]
```

## Quickstart

```python
from paygraph import AgentWallet, SpendPolicy, MockGateway

wallet = AgentWallet(
    gateway=MockGateway(auto_approve=True),
    policy=SpendPolicy(
        max_transaction=25.0,
        daily_budget=100.0,
        blocked_vendors=["doordash"],
    ),
)

result = wallet.request_spend(
    amount=4.20,
    vendor="Anthropic API",
    justification="Need Claude credits for document summarization.",
)
print(result)  # Card approved. PAN: 4111..., CVV: 123, Expiry: 12/28
```

## StripeCardGateway (Real Cards)

```python
from paygraph import AgentWallet, SpendPolicy, StripeCardGateway

wallet = AgentWallet(
    gateway=StripeCardGateway(api_key="sk_test_..."),
    policy=SpendPolicy(max_transaction=50.0),
)

result = wallet.request_spend(
    amount=4.20,
    vendor="Anthropic API",
    justification="API credits for task completion.",
)
```

### Configuration

`StripeCardGateway` accepts the following parameters:

| Parameter         | Type              | Default     | Description                                          |
|-------------------|-------------------|-------------|------------------------------------------------------|
| `api_key`         | `str`             | *required*  | Stripe secret key (`sk_test_...` or `sk_live_...`)   |
| `cardholder_id`   | `str \| None`     | `None`      | Existing cardholder ID to use (skips auto-creation)  |
| `currency`        | `str`             | `"usd"`     | Card currency (e.g. `"eur"`, `"gbp"`)               |
| `billing_address` | `dict \| None`    | US address  | Cardholder billing address (`line1`, `city`, `postal_code`, `country`) |
| `single_use`      | `bool`            | `True`      | Mint a new card per transaction; set `False` to reuse one card |

When `single_use=False`, a single card is created on the first spend and reused for subsequent calls (spending limit is updated each time).

## LangGraph Integration

```python
from paygraph import AgentWallet, SpendPolicy

wallet = AgentWallet(
    policy=SpendPolicy(max_transaction=25.0, blocked_vendors=["doordash"]),
)

# wallet.spend_tool is a LangChain-compatible tool
tool = wallet.spend_tool

# Use with LangGraph
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4-20250514")
agent = create_react_agent(llm, tools=[tool])
result = agent.invoke({"messages": [("user", "Buy $4.20 in API credits from Anthropic")]})
```

## Policy Configuration

`SpendPolicy` accepts the following parameters:

| Parameter              | Type         | Default | Description                                 |
|------------------------|--------------|---------|---------------------------------------------|
| `max_transaction`      | `float`      | `100.0` | Maximum amount per transaction (dollars)    |
| `daily_budget`         | `float`      | `1000.0`| Maximum total spend per day                 |
| `blocked_vendors`      | `list[str]`  | `[]`    | Vendors that are always denied              |
| `allowed_vendors`      | `list[str]`  | `[]`    | If set, only these vendors are allowed      |
| `blocked_mccs`         | `list[str]`  | `[]`    | Blocked merchant category codes             |
| `require_justification`| `bool`       | `False` | Require non-empty justification string      |

## Environment Variables

| Variable                | Required for            | Description                                  |
|-------------------------|-------------------------|----------------------------------------------|
| `ANTHROPIC_API_KEY`     | `--live` (default)      | Anthropic API key for Claude LLM             |
| `OPENAI_API_KEY`        | `--live --model openai` | OpenAI API key for GPT LLM                  |
| `STRIPE_API_KEY`        | `--stripe`              | Stripe secret key (`sk_test_` or `sk_live_`) |
| `STRIPE_CURRENCY`       | `--stripe` (optional)   | Card currency (default: `usd`)               |
| `STRIPE_BILLING_COUNTRY`| `--stripe` (optional)   | Billing address country code (e.g. `FR`)     |
| `STRIPE_CARDHOLDER_ID`  | `--stripe` (optional)   | Reuse an existing cardholder ID              |

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

## CLI

```bash
# Run simulated demo (no API keys needed)
paygraph demo

# Run live demo with a real LLM
export ANTHROPIC_API_KEY=sk-ant-...
paygraph demo --live

# Use OpenAI instead
export OPENAI_API_KEY=sk-...
paygraph demo --live --model openai

# Use Stripe Issuing for real card issuance
export STRIPE_API_KEY=sk_test_...
paygraph demo --live --stripe
```

## License

MIT
