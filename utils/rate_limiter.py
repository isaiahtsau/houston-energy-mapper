"""
Polite rate limiter for web scraping.

Enforces a minimum delay between outbound HTTP requests so the pipeline
does not hammer any single source. The implementation is sleep-based
(minimum inter-request delay) rather than a token bucket, which is
appropriate for this pipeline's single-process, sequential scraping pattern.

If the pipeline is refactored to use concurrent scraping (e.g. httpx async),
replace this with a thread-safe token bucket from the `limits` library.
"""
from __future__ import annotations

import time


class RateLimiter:
    """Tracks the time of the last request and sleeps to enforce a minimum gap.

    Usage in a harvester:
        self.rate_limiter.wait()   # call before every outbound HTTP request
        response = requests.get(url)

    Args:
        min_delay_seconds: Minimum seconds between consecutive requests.
                           Defaults to 1.5s (polite for most sites).
                           Increase for sites with aggressive bot detection.
    """

    def __init__(self, min_delay_seconds: float = 1.5) -> None:
        self.min_delay_seconds = min_delay_seconds
        # Initialize to 0 so the first request is always immediate
        self._last_request_at: float = 0.0

    def wait(self) -> None:
        """Sleep if necessary to enforce the minimum inter-request delay.

        Call this immediately before every outbound HTTP request.
        The sleep duration is the remaining time since the last call;
        if enough time has already passed, this returns immediately.
        """
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def reset(self) -> None:
        """Reset the timer so the next wait() call returns immediately.

        Useful between harvester runs when a delay is not needed at the boundary.
        """
        self._last_request_at = 0.0
