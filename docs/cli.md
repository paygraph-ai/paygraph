# CLI Reference

PayGraph includes a CLI for running interactive demos.

## Demo Command

```bash
paygraph demo
```

Runs an interactive demo with a mock LLM and mock gateway. No API keys needed.

### Options

| Flag | Description |
|------|-------------|
| `--live` | Use a real LLM instead of the mock |
| `--model` | LLM provider: `anthropic` or `openai` (default: `anthropic`) |
| `--stripe` | Use Stripe Issuing instead of MockGateway (requires `STRIPE_API_KEY` env var) |

### Examples

```bash
# Basic demo (no keys needed)
paygraph demo

# Live demo with Anthropic Claude
paygraph demo --live --model anthropic

# Live demo with Stripe Issuing
paygraph demo --live --stripe
```

### Environment Variables

| Variable | Required For |
|----------|-------------|
| `ANTHROPIC_API_KEY` | `--live --model anthropic` |
| `OPENAI_API_KEY` | `--live --model openai` |
| `STRIPE_API_KEY` | `--stripe` |
