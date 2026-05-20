"""Custom rate-limiting HTTP transport and client factories for httpx and huggingface_hub."""

from __future__ import annotations

import logging
import time
from typing import Iterator

import httpx
from huggingface_hub.utils._http import hf_request_event_hook

logger = logging.getLogger(__name__)


class RateLimitingSyncByteStream(httpx.SyncByteStream):
    """A sync byte stream wrapper that throttles download speed."""

    def __init__(self, original_stream: httpx.SyncByteStream, max_bytes_per_second: int):
        self.original_stream = original_stream
        self.max_bytes_per_second = max_bytes_per_second
        self.bytes_received = 0
        self.start_time = time.monotonic()

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.original_stream:
            yield chunk
            self.bytes_received += len(chunk)

            # Throttling calculation
            elapsed = time.monotonic() - self.start_time
            expected = self.bytes_received / self.max_bytes_per_second
            if elapsed < expected:
                time.sleep(expected - elapsed)

    def close(self) -> None:
        self.original_stream.close()


class RateLimitingHTTPTransport(httpx.HTTPTransport):
    """An HTTPTransport wrapper that injects rate-limiting stream decorators into responses."""

    def __init__(self, max_bytes_per_second: int, **kwargs):
        super().__init__(**kwargs)
        self.max_bytes_per_second = max_bytes_per_second

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        # Apply the throttling wrapper to the response stream
        response.stream = RateLimitingSyncByteStream(response.stream, self.max_bytes_per_second)
        return response


def configure_hf_client(max_bytes_per_second: int | None) -> None:
    """Register a custom, rate-limited httpx client factory with huggingface_hub globally.

    Args:
        max_bytes_per_second: The download limit in bytes per second.
            If None or <= 0, no limit is applied and standard defaults are kept.
    """
    if not max_bytes_per_second or max_bytes_per_second <= 0:
        return

    from huggingface_hub import set_client_factory

    logger.info(
        "Registering rate-limited Hugging Face HTTP client factory with limit %d B/s",
        max_bytes_per_second,
    )

    def rate_limiting_client_factory() -> httpx.Client:
        # Replicates standard huggingface_hub default client but with our custom transport
        transport = RateLimitingHTTPTransport(max_bytes_per_second=max_bytes_per_second)
        return httpx.Client(
            transport=transport,
            event_hooks={"request": [hf_request_event_hook]},
            follow_redirects=True,
            timeout=None,
        )

    set_client_factory(rate_limiting_client_factory)
