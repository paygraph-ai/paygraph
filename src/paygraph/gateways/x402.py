import asyncio
import base64
import json
from dataclasses import dataclass


@dataclass
class X402Receipt:
    """Result of a successful x402 payment."""

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
    """Gateway for x402 HTTP 402 payments. Supports EVM and/or Solana."""

    def __init__(
        self,
        evm_private_key: str | None = None,
        svm_private_key: str | None = None,
    ) -> None:
        if not evm_private_key and not svm_private_key:
            raise ValueError("At least one of evm_private_key or svm_private_key is required")

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

    async def _execute(
        self,
        url: str,
        amount_cents: int,
        vendor: str,
        memo: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Receipt:
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
                    tx_hash = settle.get("transaction", "") or settle.get("transactionId", "")
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
        """Make a paid HTTP request via x402 protocol (sync wrapper)."""
        return asyncio.run(
            self._execute(url, amount_cents, vendor, memo, method, headers, body)
        )
