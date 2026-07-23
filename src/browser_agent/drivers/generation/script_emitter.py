"""Apply the post-LLM transforms and write the executable script to disk.

Hides the chain of source-level rewrites the agent's emitted
code needs before it can run as a standalone script:

1. ``with_emitted_normalize_launch`` rewrites ``zd.start(...)`` to
   ``start_browser(...)`` so the script does not pass automation-
   flagging Chrome args that trigger anti-bot checks.
2. ``with_emitted_inject_profile_path`` points the emitted script
   at the agent's warm profile directory.
3. The remaining transforms prepend the vendored helper
   definitions the script depends on.

Once the source is final, the file is written to the path
:class:`ScriptPathBuilder` computed and the structured payload
(explanation + dependencies + python_code + script_path) is
printed as JSON for downstream tooling.
"""

from __future__ import annotations

import json
from pathlib import Path

from browser_agent.adapters.emitted_clean_launch import (
    with_emitted_clean_launch,
    with_emitted_inject_profile_path,
    with_emitted_normalize_launch,
)
from browser_agent.adapters.emitted_page_wait import with_emitted_page_wait
from browser_agent.adapters.emitted_pdf_download import with_emitted_pdf_download
from browser_agent.adapters.emitted_save_html import with_emitted_save_html
from browser_agent.adapters.emitted_save_record import with_emitted_save_record
from browser_agent.domain.generated_script import GeneratedScript


class ScriptEmitter:
    """Apply post-LLM transforms, write the script, print the JSON payload."""

    def __init__(self, path_builder) -> None:
        self._path_builder = path_builder

    def emit(self, task: str, script: GeneratedScript, run_path: Path) -> Path:
        """Build the final source, write it, and return the on-disk path."""
        final_code = self._finalize_source(script, run_path)
        script_path = self._path_builder.build(task)
        script_path.write_text(final_code, encoding="utf-8")
        self._print_payload(script, script_path, run_path)
        return script_path

    def _finalize_source(self, script: GeneratedScript, run_path: Path) -> str:
        """Run every source-level transform the emitted script needs."""
        code = with_emitted_normalize_launch(script.python_code)
        code = with_emitted_inject_profile_path(code, self._profile_path(run_path))
        code = with_emitted_clean_launch(code)
        code = with_emitted_page_wait(code)
        code = with_emitted_save_record(code)
        code = with_emitted_save_html(code)
        return with_emitted_pdf_download(code, script.pdf_download_strategy)

    def _profile_path(self, run_path: Path) -> str:
        """Return the absolute profile path the emitted script must reuse."""
        return str((run_path / "profile").resolve())

    def _print_payload(self, script: GeneratedScript, script_path: Path, run_path: Path) -> None:
        """Print the structured :class:`GeneratedScript` payload as JSON."""
        payload = script.model_dump()
        payload["script_path"] = str(script_path)
        payload["metadata_db_path"] = str(run_path / "metadata.db")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
