"""Push a built :class:`SyncPlan` to Uwazi (entity creation + file uploads).

The pure data transform lives in :mod:`sync_plan_builder`; this module
owns the side-effecting half: creating entities, attaching the primary
PDF and the supporting HTML, and recording the per-row outcome in an
:class:`ApplyResult`.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.apply_result import ApplyResult
from browser_agent.domain.sync_plan import SyncAction, SyncPlan
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.use_cases.push_progress import PushProgress

from uwazi_api.client import UwaziClient
from uwazi_api.domain.entity import Entity


def push_plan(*, plan: SyncPlan, client: UwaziClient) -> ApplyResult:
    """Push the plan to Uwazi; no LLM, pure :class:`UwaziClient` calls."""
    return UwaziPusher().push(plan=plan, client=client)


class UwaziPusher:
    """Push one :class:`SyncPlan` to Uwazi and record the outcome."""

    def push(self, *, plan: SyncPlan, client: UwaziClient) -> ApplyResult:
        """Push the plan to Uwazi; no LLM, pure :class:`UwaziClient` calls."""
        out = ApplyResult()
        total = len(plan.rows)
        active = sum(1 for row in plan.rows if row.action is not SyncAction.SKIP)
        progress = PushProgress(total, active)
        for i, row in enumerate(plan.rows, start=1):
            self._push_row(client, out, row, plan.mapping, i, total, progress)
        return out

    def _push_row(
        self, client, out: ApplyResult, row, mapping: UwaziMapping, i: int, total: int, progress: PushProgress
    ) -> None:
        """Push one :class:`SyncPlanRow` to Uwazi and update ``out`` accordingly."""
        try:
            if row.action is SyncAction.CREATE:
                progress.begin_active()
                shared_id = self._create_entity(client, row, mapping)
                progress.end_active()
                print(f"  [{i}/{total}] {progress.format_prefix()} | created {row.language} '{row.title}' -> {shared_id}")
            elif row.action is SyncAction.SKIP:
                self._record_skip(out, row.language, row.source_url, row.skip_reason or "skipped_by_plan")
                print(
                    f"  [{i}/{total}] {progress.format_prefix()} | skipped {row.language} '{row.title}': {row.skip_reason}"
                )
                return
            self._record_result(out, row.language, row.action)
        except Exception as exc:  # noqa: BLE001 - any failure is recorded
            progress.end_active()
            self._record_error(out, row.language, row.source_url, str(exc))

    def _create_entity(self, client: UwaziClient, row, mapping: UwaziMapping) -> str:
        """Create a fresh Uwazi entity for one CREATE row, return the new shared id."""
        entity = Entity(template=mapping.template, title=row.title, published=mapping.publish, metadata=row.metadata)
        shared_id = client.entities.upload(entity=entity, language=row.language)
        if mapping.upload_pdf and row.pdf_path:
            self._upload_primary_file(client, shared_id, row.pdf_path, row.language, row.title)
        if mapping.upload_pdf and row.html_path:
            self._upload_supporting_html(client, shared_id, row.html_path, row.language, row.title)
        return shared_id

    def _upload_primary_file(self, client, shared_id: str, pdf_path: str, language: str, title: str) -> None:
        """Attach ``pdf_path`` as the primary document of the entity."""
        from uwazi_api.domain.FileType import FileType

        payload = Path(pdf_path).read_bytes()
        client.files.upload_document_from_bytes(
            file_bytes=payload,
            share_id=shared_id,
            language=language,
            title=title,
            file_type=FileType.PDF,
        )

    def _upload_supporting_html(self, client, shared_id: str, html_path: str, language: str, title: str) -> None:
        """Attach ``html_path`` as a supporting file (attachment) of the entity."""
        from uwazi_api.domain.FileType import FileType

        payload = Path(html_path).read_bytes()
        client.files.upload_file_from_bytes(
            file_bytes=payload,
            share_id=shared_id,
            language=language,
            title=f"{title} (source HTML)",
            file_type=str(FileType.HTML),
        )

    @staticmethod
    def _record_result(out: ApplyResult, language: str, action: SyncAction) -> None:
        """Increment the per-language count for ``action``."""
        bucket = out.per_language_counts.setdefault(language, {})
        bucket[action.value] = bucket.get(action.value, 0) + 1

    def _record_skip(self, out: ApplyResult, language: str, source_url: str, reason: str) -> None:
        """Append one skip row to the apply result and bump the per-language count."""
        out.skip_reasons = out.skip_reasons + ((language, source_url, reason),)
        self._record_result(out, language, SyncAction.SKIP)

    @staticmethod
    def _record_error(out: ApplyResult, language: str, source_url: str, message: str) -> None:
        """Append one error row to the apply result."""
        out.error_rows = out.error_rows + ((language, source_url, message),)
