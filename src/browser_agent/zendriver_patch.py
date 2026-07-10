"""Runtime monkey-patches for the vendored zendriver library.

These patches live in the project source tree so they survive a fresh virtualenv
and do not require editing site-packages. They are applied once at process startup
before any zendriver Browser/Tab is created.
"""

from __future__ import annotations

from typing import Any


def apply() -> None:
    """Apply all monkey-patches. Idempotent."""
    _patch_transaction_call()


def _patch_transaction_call() -> None:
    """Make Transaction.__call__ tolerate duplicate CDP responses.

    zendriver's shared Connection mapper can deliver the same response twice
    (or an out-of-order response for an already-completed transaction) when
    multiple tabs race on the same browser CDP connection. The original code
    calls ``set_result`` / ``set_exception`` unconditionally, which raises
    ``asyncio.InvalidStateError`` and kills the listener loop, deadlocking the
    whole browser session.

    This patch wraps the result/exception setting so that a transaction which is
    already done is simply ignored, keeping the listener alive.
    """
    from zendriver.core.connection import Transaction

    if getattr(Transaction, "_copy_domain_patched", False):
        return

    def _safe_call(self: Transaction, **response: Any) -> Any:  # type: ignore[no-untyped-def]
        if self.done():
            # Duplicate or late CDP response for an already-settled transaction.
            # Ignore it so the listener loop does not crash.
            return None

        if "error" in response:
            from zendriver.core.connection import ProtocolException

            return self.set_exception(ProtocolException(response["error"]))

        try:
            self.__cdp_obj__.send(response["result"])
        except StopIteration as e:
            return self.set_result(e.value)

        from zendriver.core.connection import ProtocolException

        raise ProtocolException("could not parse the cdp response:\n%s" % response)

    Transaction.__call__ = _safe_call  # type: ignore[assignment]
    Transaction._copy_domain_patched = True  # type: ignore[attr-defined]