import asyncio
import base64
import concurrent.futures
import json
from dataclasses import dataclass


@dataclass
class X402Receipt:
    """Result of a successful x402 payment.

    Attributes:
        url: The x402-enabled endpoint URL that was called.
        amount_cents: Amount paid in cents.
        network: Blockchain network identifier (e.g. ``"eip155:8453"``).
        transaction_hash: On-chain transaction hash.
        payer: Wallet address of the payer.
        gateway_ref: Unique reference (usually the transaction hash).
        gateway_type: Always ``"x402"``.
        status_code: HTTP status code of the response.
        response_body: Body of the HTTP response from the paid endpoint.
        content_type: Content-Type header of the response.
    """

    url: str
    amount_cents: int
    network: str
    transaction_hash: str
    payer: str
    gateway_ref: str
    gateway_type: str = "x402"

    status_code: int = 200
    response_body: str = ""
    content_type: str = "application/json"


class X402Gateway:
    """Gateway for x402 HTTP 402 payments using on-chain USDC.

    Supports EVM chains (Base, Ethereum, etc.) and Solana. At least one
    private key must be provided. The x402 SDK is imported lazily — it
    only needs to be installed when this gateway is used.

    Args:
        evm_private_key: Hex-encoded EVM private key (e.g. ``"0x..."``).
        svm_private_key: Base58-encoded Solana private key.
    """

    def __init__(
        self,
        evm_private_key: str | None = None,
        svm_private_key: str | None = None,
    ) -> None:
        if not evm_private_key and not svm_private_key:
            raise ValueError(
                "At least one of evm_private_key or svm_private_key is required"
            )

        try:
            from x402 import x402Client
        except ImportError:
            raise ImportError(
                "x402 integration requires the x402 SDK. "
                "Install it with: pip install paygraph[x402]"
            )

        self._client = x402Client()

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

    async def execute_x402_async(
        self,
        url: str,
        amount_cents: int,
        vendor: str,
        memo: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Receipt:
        """Make a paid HTTP request via the x402 protocol (async).

        Use this coroutine directly from async contexts such as LangGraph
        agents, FastAPI handlers, or Jupyter notebooks where an event loop
        is already running. Calling the synchronous :meth:`execute_x402`
        from those contexts will raise ``RuntimeError``.

        Args:
            url: The x402-enabled endpoint URL.
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Receipt`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry).
        """
        from x402.http.clients import x402HttpxClient

        async with x402HttpxClient(self._client) as http:
            kwargs: dict = {}
            if headers:
                kwargs["headers"] = headers
            if body:
                kwargs["content"] = body

            response = await http.request(method, url, **kwargs)
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

            return X402Receipt(
                url=url,
                amount_cents=amount_cents,
                network=network,
                transaction_hash=tx_hash,
                payer=self._payer,
                gateway_ref=tx_hash or f"x402_{id(response)}",
                status_code=response.status_code,
                response_body=response.text,
                content_type=response.headers.get("content-type", ""),
            )

    def execute_x402(
        self,
        url: str,
        amount_cents: int,
        vendor: str,
        memo: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Receipt:
        """Make a paid HTTP request via the x402 protocol (sync).

        Synchronous wrapper around :meth:`execute_x402_async`. Safe to call
        from any context — including scripts, CLI, and environments with a
        running event loop (LangGraph agents, FastAPI handlers, Jupyter
        notebooks). When a loop is already running in the current thread, the
        call is automatically dispatched to a worker thread with its own fresh
        event loop. For fully-async callers that prefer to avoid the thread
        overhead, ``await gateway.execute_x402_async(...)`` is also available.

        Args:
            url: The x402-enabled endpoint URL.
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Receipt`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry).
        """
        coro = self.execute_x402_async(
            url, amount_cents, vendor, memo, method, headers, body
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
