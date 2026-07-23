"""Shared Python-source snippets reused across every vendored ``emitted*`` block.

The emitted helpers are self-contained plain-Python strings prepended to
the LLM's script (the final script MUST NOT import from this project).
Several utility functions are identical across blocks — atomic file
writes, deterministic filename derivation, existence checks. Defining
each once here and composing the blocks from these snippets keeps the
vendored code in sync without a runtime dependency.

Every snippet is a ``str`` of Python source with no leading/trailing
blank lines; the emitted blocks join them with the project's own
``\n\n`` separators. Snippets are deliberately dependency-free (stdlib
only) so they stay valid inside a self-contained emitted script.
"""

from __future__ import annotations

ATOMIC_WRITE_SNIPPET = '''\
def _write_atomic(path, data):
    """Write ``data`` to ``path`` atomically (temp + rename). On any failure,
    remove the temp file. Renames are atomic on POSIX so a crash mid-write
    never leaves a partial file at ``path``. ``path`` may be ``str`` or ``Path``."""
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(part, path)
    except Exception:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise'''


EXISTING_SIZE_SNIPPET = '''\
def _existing_size(path):
    """Return existing on-disk size in bytes, or 0 when missing/empty/corrupt."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    return st.st_size if st.st_size > 0 else 0'''


PDF_FILENAME_SNIPPET = '''\
def _pdf_filename_for(url):
    """Deterministic, collision-safe on-disk filename for ``url``.

    Returns ``pdf_<sha1(url)[:12]>.pdf`` — a pure function of the
    download URL, so "file exists at path" == "this exact PDF was
    already downloaded" regardless of page order or label reuse.
    """
    return f"pdf_{hashlib.sha1(url.encode()).hexdigest()[:12]}.pdf"'''


HTML_FILENAME_SNIPPET = '''\
def _html_filename_for(url):
    """Deterministic, collision-safe on-disk filename for ``url``.

    Returns ``html_<sha1(url)[:12]>.html`` — a pure function of the
    source URL, so "file exists at path" == "this exact page HTML was
    already saved" regardless of page order. Mirrors the PDF naming
    scheme so the two never collide (different prefix + extension).
    """
    return f"html_{hashlib.sha1(url.encode()).hexdigest()[:12]}.html"'''
