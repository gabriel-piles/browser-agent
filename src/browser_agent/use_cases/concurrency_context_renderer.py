"""Render the concurrency directive the processing agent sees.

Extracted from the step-0 driver so the driver only sequences use cases.
A non-empty directive instructs the agent to fan the per-document phase
out across N tabs; an empty string leaves the classic single-tab flow.
"""

from __future__ import annotations

from browser_agent.domain.run_config import RunConfig


def render_concurrency_context(run: RunConfig) -> str:
    """Render the concurrency directive the agent sees, or "" for single-tab.

    When ``run.parallel_runners`` is set (>= 2), returns a directive that
    instructs the agent to fan the per-document phase out across that many
    tabs; otherwise returns "" so the classic single-tab flow is unchanged.
    """
    pr = run.parallel_runners
    if pr is None or pr <= 1:
        return ""
    return (
        "# Concurrency requirement\n"
        f"parallel_runners = {pr}\n"
        f"The script MUST process documents across {pr} browser tabs concurrently "
        f"(see the Concurrency / multi-tab section of the script rules). "
        "Discovery (filter iteration + link collection + scroll/load-more) stays "
        f"single-tab; only the per-document processing fans out across {pr} tabs via ONE worker coroutine per tab consuming "
        f"a shared asyncio.Queue (FORBIDDEN: idx % N tab assignment "
        f"behind a global asyncio.Semaphore — concurrent tab.get() on "
        f"a shared tab invalidates element handles). Open {pr} tabs with "
        "`tab = await browser.get(url, new_tab=True)` after start_browser and call "
        "`await prepare_page_wait(tab)` on EACH tab before its first navigation. "
        "Pass each task its OWN tab to download_pdf_curl_cffi / save_page_html so "
        "cookies are not shared across concurrent sessions. "
        "Foreground-gated SPAs (Aurelia/vLex/Corte IDH, React lazy mounts) "
        "render late-bound metadata ONLY in the visible tab — concurrent "
        "per-tab bring_to_front() calls steal foreground from each other and "
        "N-1 tabs' metadata never renders (gate timeout -> load_failed). "
        "Declare `gate_lock = asyncio.Lock()` before the workers and wrap the "
        "navigate + bring_to_front + metadata-gate (+ retry) block in "
        "`async with gate_lock:`; release before extraction/download so PDF "
        "I/O still parallelizes (rule 15h, lint-enforced)."
    )
