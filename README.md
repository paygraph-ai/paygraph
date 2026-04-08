# PayGraph

[![Tests](https://github.com/paygraph-ai/paygraph/actions/workflows/test.yml/badge.svg)](https://github.com/paygraph-ai/paygraph/actions/workflows/test.yml)

**Open-source spend governance for AI agents.** Issue single-use virtual cards or pay x402-enabled APIs with USDC — with policy enforcement, audit logging, and human-in-the-loop approval.

## Architecture

```
 Agent (LangGraph / any framework)
   │
   ▼
 ┌────────────────────────────────────────┐
 │             AgentWallet                │
 │  ┌──────────────┐  ┌──────────────┐    │
 │  │ Policy Engine│  │ Audit Logger │    │
 │  └──────┬───────┘  └──────┬───────┘    │
 │         │                 │            │
 │  ┌──────▼─────────────────▼───────┐    │
 │  │         Payment Rails          │    │
 │  │  Card: Mock │ Stripe           │    │
 │  │  x402: X402Gateway │ MockX402  │    │
 │  └────────────────────────────────┘    │
 └────────────────────────────────────────┘
```

## Install

```bash
pip install paygraph
```

With LangGraph support:

```bash
pip install paygraph[langgraph]
```

With x402 support (EVM + Solana USDC payments):

```bash
pip install paygraph[x402]
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

## x402 Gateway (Pay APIs with USDC)

x402 is an HTTP 402-based protocol for machine-to-machine payments. Instead of minting a card, the gateway makes an HTTP request, handles the 402→sign→retry cycle on-chain, and returns the API response.

```python
from paygraph import AgentWallet, X402Gateway, SpendPolicy

wallet = AgentWallet(
    x402_gateway=X402Gateway(evm_private_key="0x..."),  # Base/Polygon USDC
    policy=SpendPolicy(max_transaction=5.0),
)

response = wallet.request_x402(
    url="https://api.example.com/paid-endpoint",
    amount=0.01,
    vendor="ExampleAPI",
    justification="Need data for analysis.",
)
print(response)  # The API response body
```

Supports both EVM (Base, Polygon, etc.) and Solana:

```python
# Solana only
gateway = X402Gateway(svm_private_key="BASE58_KEY")

# Both networks
gateway = X402Gateway(evm_private_key="0x...", svm_private_key="BASE58_KEY")
```

For testing without blockchain:

```python
from paygraph import AgentWallet, MockX402Gateway

wallet = AgentWallet(
    x402_gateway=MockX402Gateway(auto_approve=True, response_body='{"data": 42}'),
)
```

## LangGraph Integration

```python
from paygraph import AgentWallet, SpendPolicy, MockX402Gateway

wallet = AgentWallet(
    x402_gateway=MockX402Gateway(auto_approve=True),
    policy=SpendPolicy(max_transaction=25.0, blocked_vendors=["doordash"]),
)

# wallet.spend_tool → card payments, wallet.x402_tool → x402 API payments
tools = [wallet.spend_tool, wallet.x402_tool]

# Use with LangGraph
from langgraph.prebuilt import create_react_agent
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4-20250514")
agent = create_react_agent(llm, tools=tools)
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
| `STRIPE_API_KEY`        | `--stripe` or `--stripe-mpp` | Stripe secret key (`sk_test_` or `sk_live_`) |
| `STRIPE_CURRENCY`       | Stripe gateways (optional)   | Currency for Stripe card/SPT limits (default: `usd`) |
| `STRIPE_BILLING_COUNTRY`| `--stripe` (optional)        | Billing address country code (e.g. `FR`)     |
| `STRIPE_CARDHOLDER_ID`  | `--stripe` (optional)        | Reuse an existing cardholder ID              |
| `STRIPE_MPP_PAYMENT_METHOD` | `--stripe-mpp`          | Stripe PaymentMethod id (`pm_...`) used to issue SPTs |
| `STRIPE_MPP_GRANTEE`    | `--stripe-mpp`               | Seller grantee id (typically `profile_...`)  |
| `STRIPE_MPP_EXPIRES_IN_SECONDS` | `--stripe-mpp` (optional) | SPT lifetime in seconds (default: `3600`) |
| `EVM_PRIVATE_KEY`       | x402 (EVM)              | EVM private key for Base/Polygon USDC payments |
| `SVM_PRIVATE_KEY`       | x402 (Solana)           | Solana private key (base58) for USDC payments |

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

# Use Stripe MPP (Shared Payment Tokens)
export STRIPE_API_KEY=sk_test_...
export STRIPE_MPP_PAYMENT_METHOD=pm_...
export STRIPE_MPP_GRANTEE=profile_...
paygraph demo --live --stripe-mpp
```

## License

MIT
