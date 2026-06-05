import asyncio
import base64
import concurrent.futures
import json
from typing import TYPE_CHECKING

from paygraph.gateways.base import BaseGateway, X402Result

if TYPE_CHECKING:
    from x402 import PaymentPolicy, PaymentRequirementsSelector

# Deprecated alias — kept for one release cycle
X402Receipt = X402Result


class X402Gateway(BaseGateway):
    """Gateway for x402 HTTP 402 payments using on-chain USDC.

    Supports EVM chains (Base, Ethereum, etc.) and Solana. At least one
    private key must be provided. The x402 SDK is imported lazily — it
    only needs to be installed when this gateway is used.

    When a server's ``402`` response advertises more than one acceptable
    payment requirement (e.g. USDC on Base *and* on Ethereum, or several
    price points), the gateway must pick which one to pay. By default the
    underlying SDK simply pays the first requirement offered, with no
    upper bound — fine for demos, risky for an autonomous agent. The
    selection arguments below let you apply PayGraph-style governance to
    that choice *before any funds move*:

    Args:
        evm_private_key: Hex-encoded EVM private key (e.g. ``"0x..."``).
        svm_private_key: Base58-encoded Solana private key.
        prefer_network: If set, prefer payment requirements on this x402
            network id (e.g. ``"eip155:8453"`` for Base) when the server
            offers a choice. Requirements on other networks remain as
            fallbacks rather than being dropped.
        max_amount: If set, reject any offered payment requirement whose
            amount exceeds this value, expressed in the asset's *atomic*
            units (e.g. ``1_000_000`` == 1 USDC at 6 decimals). Acts as a
            hard per-request ceiling on the on-chain amount the gateway
            will agree to, independent of the dollar-denominated
            ``SpendPolicy`` enforced by ``AgentWallet``.
        payment_policies: Advanced escape hatch — any additional x402
            ``PaymentPolicy`` callables to register on the client, applied
            in order after ``prefer_network`` / ``max_amount``.
        payment_requirements_selector: Advanced escape hatch — a custom
            selector that chooses the final requirement from the filtered
            list. Defaults to the SDK's "first requirement" selector.
    """

    def __init__(
        self,
        evm_private_key: str | None = None,
        svm_private_key: str | None = None,
        *,
        prefer_network: str | None = None,
        max_amount: int | None = None,
        payment_policies: "list[PaymentPolicy] | None" = None,
        payment_requirements_selector: "PaymentRequirementsSelector | None" = None,
    ) -> None:
        if not evm_private_key and not svm_private_key:
            raise ValueError(
                "At least one of evm_private_key or svm_private_key is required"
            )

        try:
            from x402 import max_amount as max_amount_policy
            from x402 import prefer_network as prefer_network_policy
            from x402 import x402Client
        except ImportError:
            raise ImportError(
                "x402 integration requires the x402 SDK. "
                "Install it with: pip install paygraph[x402]"
            )

        self._client = x402Client(
            payment_requirements_selector=payment_requirements_selector
        )

        # Requirement-selection policies are applied in registration order to
        # the list of requirements the server offers, before one is chosen and
        # paid. prefer_network reorders (keeps others as fallbacks); max_amount
        # filters out anything above the ceiling.
        if prefer_network is not None:
            self._client.register_policy(prefer_network_policy(prefer_network))
        if max_amount is not None:
            self._client.register_policy(max_amount_policy(max_amount))
        for policy in payment_policies or []:
            self._client.register_policy(policy)

        if evm_private_key:
            from eth_account import Account
            from x402.mechanisms.evm import EthAccountSigner
            from x402.mechanisms.evm.exact.register import register_exact_evm_client

            account = Account.from_key(evm_private_key)
            register_exact_evm_client(self._client, EthAccountSigner(account))
            self._payer = account.address

        if svm_private_key:
            from x402.mechanisms.svm import KeypairSigner
            from x402.mechanisms.svm.exact.register import register_exact_svm_client

            svm_signer = KeypairSigner.from_base58(svm_private_key)
            register_exact_svm_client(self._client, svm_signer)
            if not evm_private_key:
                self._payer = str(svm_signer.address)

    async def execute_async(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Make a paid HTTP request via the x402 protocol (async).

        Use this coroutine directly from async contexts such as LangGraph
        agents, FastAPI handlers, or Jupyter notebooks where an event loop
        is already running. Calling the synchronous :meth:`execute`
        from those contexts will raise ``RuntimeError``.

        Args:
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            url: The x402-enabled endpoint URL.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Result`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry).
        """

        from x402.http.clients import x402HttpxClient

        async with x402HttpxClient(self._client) as http:
            req_kwargs: dict = {}
            if headers:
                req_kwargs["headers"] = headers
            if body:
                req_kwargs["content"] = body

            response = await http.request(method, url, **req_kwargs)
            await response.aread()

            # If still 402 after the SDK's retry, payment failed
            if response.status_code == 402:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", "Payment required but failed")
                except Exception:
                    error_msg = "Payment required but failed"
                raise RuntimeError(f"x402 payment failed: {error_msg}")

            tx_hash = ""
            network = ""

            # Parse the base64-encoded payment-response header
            payment_header = response.headers.get("payment-response", "")
            if payment_header:
                try:
                    settle = json.loads(base64.b64decode(payment_header))
                    tx_hash = settle.get("transaction", "") or settle.get(
                        "transactionId", ""
                    )
                    network = settle.get("network", "")
                except Exception:
                    pass

            return X402Result(
                url=url,
                amount_cents=amount_cents,
                network=network,
                transaction_hash=tx_hash,
                payer=self._payer,
                gateway_ref=tx_hash or f"x402_{id(response)}",
                gateway_type="x402",
                status_code=response.status_code,
                response_body=response.text,
                content_type=response.headers.get("content-type", ""),
            )

    def execute(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Make a paid HTTP request via the x402 protocol (sync).

        Synchronous wrapper around :meth:`execute_async`. Safe to call
        from any context — including scripts, CLI, and environments with a
        running event loop (LangGraph agents, FastAPI handlers, Jupyter
        notebooks). When a loop is already running in the current thread, the
        call is automatically dispatched to a worker thread with its own fresh
        event loop. For fully-async callers that prefer to avoid the thread
        overhead, ``await gateway.execute_async(...)`` is also available.

        Args:
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            url: The x402-enabled endpoint URL.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Result`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry).
        """
        coro = self.execute_async(
            amount_cents, vendor, memo, url=url, method=method, headers=headers, body=body
        )

        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if running:
            # A loop is already running in this thread (LangGraph, FastAPI, Jupyter,
            # etc.). Dispatch to a worker thread so asyncio.run() can create a fresh
            # event loop there without interfering with the caller's loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()

        return asyncio.run(coro)
