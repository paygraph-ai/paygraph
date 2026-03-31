# Installation

## Base Install

```bash
pip install paygraph
```

This gives you the core library with `MockGateway` for local development and testing.

## With LangGraph Integration

```bash
pip install "paygraph[langgraph]"
```

Adds `langchain-core` so you can use `wallet.spend_tool` and `wallet.x402_tool` in LangGraph agents.

## With Live Demo

```bash
pip install "paygraph[live]"
```

Includes LangGraph, LangChain Anthropic, and LangChain OpenAI for running the interactive demo with real LLMs.

## With x402 Support

```bash
pip install "paygraph[x402]"
```

Adds the Coinbase x402 SDK with EVM and Solana signers for on-chain USDC payments.

## For Development

```bash
git clone https://github.com/Allenbrd/paygraph.git
cd paygraph
pip install -e ".[langgraph]" pytest
```

## For Documentation

```bash
pip install -e ".[docs]"
mkdocs serve
```

## Requirements

- Python 3.10+
- For Stripe: a Stripe API key with Issuing enabled
- For x402: an EVM or Solana private key with USDC balance
