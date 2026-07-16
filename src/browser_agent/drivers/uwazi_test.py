"""Helper script: delete every entity on the configured Uwazi instance.

Used during development to wipe Uwazi between runs. The script
prints the live thesaurus list first (so it doubles as a smoke
test that the client credentials are still good) and then asks
for a y/N confirmation before bulk-deleting. A single ``y`` or
``Y`` proceeds; anything else aborts.

The mode is a module constant — flip ``DELETE_ENTITIES`` to True
to run the wipe, leave it False to just print thesauri. There is
no CLI argument: per project convention, scripts are configured
via source-level constants, not argv parsing.
"""

from __future__ import annotations

from uwazi_api.client import UwaziClient
from uwazi_api.domain.search_filters import SearchFilters

from browser_agent.configuration import UWAZI_URL, UWAZI_USER, UWAZI_PASSWORD

# Flip to True to bulk-delete every entity on the configured template,
# leave False to just dump the thesaurus list as a smoke test.
DELETE_ENTITIES: bool = False

# Template whose entities get wiped. Other templates stay untouched.
WIPE_TEMPLATE: str = "Document"

# Page size for both the search and the bulk-delete batches.
WIPE_PAGE_SIZE: int = 200


def _print_thesauri(client: UwaziClient) -> None:
    """Print the live thesaurus list to stdout, one per line."""
    rows = client.thesauris.get("en")
    print(f"Found {len(rows)} thesaurus/thesauri")
    for t in rows:
        print(f"  {t.id}: {t.name} ({len(t.values)} top-level values)")


def _collect_template_shared_ids(client: UwaziClient, template_name: str, page_size: int) -> list[str]:
    """Return every shared id on ``template_name`` via paginated search."""
    out: list[str] = []
    start = 0
    while True:
        page = client.search.search_by_filter(
            filters=SearchFilters(filters={}),
            template_name=template_name,
            start_from=start,
            batch_size=page_size,
            language="en",
        )
        if not page:
            break
        out.extend(e.shared_id for e in page if e.shared_id)
        if len(page) < page_size:
            break
        start += page_size
    return out


def _delete_in_batches(client: UwaziClient, shared_ids: list[str], page_size: int) -> None:
    """Call ``/api/entities/bulkdelete`` once per page-sized batch."""
    for i in range(0, len(shared_ids), page_size):
        batch = shared_ids[i : i + page_size]
        client.entities.delete_entities(batch)
        print(f"  deleted {len(batch)} ({i + len(batch)}/{len(shared_ids)})")


def _delete_all_entities(client: UwaziClient) -> None:
    """Bulk-delete every entity on ``WIPE_TEMPLATE``, after a y/N prompt."""
    shared_ids = _collect_template_shared_ids(client, WIPE_TEMPLATE, WIPE_PAGE_SIZE)
    if not shared_ids:
        print(f"No entities on template {WIPE_TEMPLATE!r}.")
        return
    print(f"About to delete {len(shared_ids)} entities (template={WIPE_TEMPLATE!r}).")
    confirm = input("Type 'y' to confirm, anything else aborts: ").strip().lower()
    if confirm != "y":
        print("Aborted; no entities were deleted.")
        return
    _delete_in_batches(client, shared_ids, WIPE_PAGE_SIZE)
    print("Done.")


def main() -> None:
    """Module entry: print thesauri, then optionally delete entities."""
    client = UwaziClient(url=UWAZI_URL, user=UWAZI_USER, password=UWAZI_PASSWORD)
    _print_thesauri(client)
    if DELETE_ENTITIES:
        _delete_all_entities(client)


if __name__ == "__main__":
    main()
