"""Push a built :class:`SyncPlan` to Uwazi (entity creation + file uploads).

The pure data transform lives in :mod:`sync_plan_builder`; this module
owns the side-effecting half: creating each entity with its primary PDF
and supporting HTML in one upload call, and recording the per-row
outcome in an :class:`ApplyResult`.
"""

from __future__ import annotations
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from browser_agent.domain.apply_result import ApplyResult
from browser_agent.domain.sync_plan import SyncAction, SyncPlan
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent import configuration
from browser_agent.use_cases.push_progress import PushProgress

from uwazi_api.domain.FileType import FileType
from uwazi_api.domain.entity_file_upload import EntityFileUpload

# Uwazi's originalname field is limited to 255 characters.
# Entity titles can be longer, so we truncate for file uploads.
_UPLOAD_TITLE_MAX_LENGTH = 255

_SUPPORTED_FILE_TYPES = {
    ".doc": FileType.DOC,
    ".docx": FileType.DOCX,
    ".rtf": FileType.RTF,
    ".odt": FileType.ODT,
    ".odp": FileType.ODP,
    ".ods": FileType.ODS,
    ".xls": FileType.XLS,
    ".xlsx": FileType.XLSX,
    ".ppt": FileType.PPT,
    ".pptx": FileType.PPTX,
    ".pdf": FileType.PDF,
    ".html": FileType.HTML,
    ".txt": FileType.TXT,
    ".csv": FileType.CSV,
    ".zip": FileType.ZIP,
}


def _file_type_for(suffix: str):
    return _SUPPORTED_FILE_TYPES.get((suffix or "").lower(), FileType.BIN)


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
        lock = threading.Lock()
        rows = list(enumerate(plan.rows, start=1))
        with ThreadPoolExecutor(max_workers=configuration.UWAZI_PUSH_MAX_WORKERS) as pool:
            futures = [
                pool.submit(self._push_row, client, out, row, plan.mapping, i, total, progress, lock) for i, row in rows
            ]
            for _ in as_completed(futures):
                pass
        return out

    def _push_row(
        self,
        client,
        out: ApplyResult,
        row,
        mapping: UwaziMapping,
        i: int,
        total: int,
        progress: PushProgress,
        lock: threading.Lock,
    ) -> None:
        """Push one :class:`SyncPlanRow` to Uwazi and update ``out`` accordingly."""
        try:
            if row.action is SyncAction.CREATE:
                with lock:
                    progress.begin_active()
                shared_id = self._create_entity(client, row, mapping)
                if mapping.registry_template:
                    self._create_registry_and_link(client, row, mapping, shared_id)
                with lock:
                    progress.end_active()
                    self._record_result(out, row.language, row.action)
                    print(
                        f"  [{i}/{total}] {progress.format_prefix()} | created {row.language} '{row.title}' -> {shared_id}"
                    )
            elif row.action is SyncAction.CREATE_REGISTRY_ONLY:
                with lock:
                    progress.begin_active()
                registry_shared_id = self._create_registry_and_link(client, row, mapping, row.primary_shared_id)
                with lock:
                    progress.end_active()
                    self._record_result(out, row.language, row.action)
                    print(
                        f"  [{i}/{total}] {progress.format_prefix()} | created registry {row.language} '{row.title}' -> {registry_shared_id}"
                    )
            elif row.action is SyncAction.SKIP:
                with lock:
                    self._record_skip(out, row.language, row.core_id, row.skip_reason or "skipped_by_plan")
                    print(
                        f"  [{i}/{total}] {progress.format_prefix()} | skipped {row.language} '{row.title}': {row.skip_reason}"
                    )
                return
        except Exception as exc:  # noqa: BLE001 - any failure is recorded
            with lock:
                progress.end_active()
                self._record_error(out, row.language, row.core_id, str(exc))

    def _create_entity(self, client: UwaziClient, row, mapping: UwaziMapping) -> str:
        """Create a fresh Uwazi entity for one CREATE row, return the new shared id."""
        entity = Entity(template=mapping.template, title=row.title, published=mapping.publish, metadata=row.metadata)
        files = self._build_entity_files(row, mapping)
        return client.entities.upload(entity=entity, language=row.language, files=files or None)

    def _create_registry_and_link(
        self,
        client: UwaziClient,
        row,
        mapping: UwaziMapping,
        primary_shared_id: str | None,
    ) -> str:
        """Create the registry entity with its date, relationship and hash set in metadata."""
        registry_metadata = dict(row.registry_metadata)
        if mapping.scraper_date_property:
            registry_metadata[mapping.scraper_date_property] = int(datetime.now(timezone.utc).timestamp())
        if mapping.scraper_document_relationship and primary_shared_id:
            registry_metadata[mapping.scraper_document_relationship] = [{"value": primary_shared_id}]
        if mapping.scraper_document_hash:
            registry_metadata[mapping.scraper_document_hash] = self._document_hash_for(row)
        registry_entity = Entity(
            template=mapping.registry_template,
            title=row.title,
            published=False,
            metadata=registry_metadata,
        )
        registry_shared_id = client.entities.upload(entity=registry_entity, language=row.language, files=None)
        return registry_shared_id

    @staticmethod
    def _document_hash_for(row) -> str | None:
        """Return the SHA-256 hex of the row's document file (PDF or DOC), or None."""
        import hashlib

        for path in (row.pdf_path, row.supporting_path, row.html_path):
            if not path:
                continue
            candidate = Path(path)
            if not candidate.exists():
                continue
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
        return None

    def _build_entity_files(self, row, mapping: UwaziMapping) -> list[EntityFileUpload]:
        """Build the primary + supporting file uploads for one entity."""
        from uwazi_api.domain.file_fieldname import FileFieldname

        files: list[EntityFileUpload] = []
        if mapping.upload_pdf and row.pdf_path:
            files.append(self._file_upload(FileFieldname.FILE, f"{row.title}.pdf", FileType.PDF, row.pdf_path))
        if mapping.upload_pdf and row.html_path:
            files.append(self._file_upload(FileFieldname.ATTACHMENT, f"{row.title}.html", FileType.HTML, row.html_path))
        if mapping.upload_pdf and row.supporting_path:
            files.append(
                self._file_upload(
                    FileFieldname.ATTACHMENT,
                    Path(row.supporting_path).name,
                    _file_type_for(Path(row.supporting_path).suffix),
                    row.supporting_path,
                )
            )
        return files

    def _file_upload(self, fieldname, filename: str, file_type, path: str) -> EntityFileUpload:
        """Read ``path`` and wrap it as one :class:`EntityFileUpload` for the entity call."""
        return EntityFileUpload(
            fieldname=fieldname,
            filename=filename[:_UPLOAD_TITLE_MAX_LENGTH],
            content=Path(path).read_bytes(),
            content_type=file_type,
        )

    @staticmethod
    def _record_result(out: ApplyResult, language: str, action: SyncAction) -> None:
        """Increment the per-language count for ``action``."""
        bucket = out.per_language_counts.setdefault(language, {})
        bucket[action.value] = bucket.get(action.value, 0) + 1

    def _record_skip(self, out: ApplyResult, language: str, core_id: str, reason: str) -> None:
        """Append one skip row to the apply result and bump the per-language count."""
        out.skip_reasons = out.skip_reasons + ((language, core_id, reason),)
        self._record_result(out, language, SyncAction.SKIP)

    @staticmethod
    def _record_error(out: ApplyResult, language: str, core_id: str, message: str) -> None:
        """Append one error row to the apply result."""
        out.error_rows = out.error_rows + ((language, core_id, message),)
