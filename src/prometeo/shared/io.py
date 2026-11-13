"""Atomic file writes, checksums and safe path components.

Two project rules are implemented here:

* never publish a partially written file (atomic replace);
* never build a filename that is invalid on Windows or Linux.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from prometeo.shared.errors import ProMeteoError

# Characters that are invalid in Windows filenames (and control characters).
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Runs of whitespace are collapsed into a single underscore.
_WHITESPACE = re.compile(r"\s+")
# Windows device names cannot be used as normal file names.
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def ensure_directory(path: Path) -> Path:
    """Create a directory and its parents, returning the resolved path."""
    directory = Path(path).expanduser()
    try:
        # exist_ok=True makes this idempotent: callers may invoke it repeatedly.
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProMeteoError(
            f"No se pudo crear el directorio: {directory}",
            hint=str(exc),
        ) from exc

    # mkdir may have succeeded for a path that is not a directory only if the
    # path already existed as a file; report that clearly.
    if not directory.is_dir():
        raise ProMeteoError(f"La ruta existe y no es un directorio: {directory}")
    return directory.resolve()


@contextmanager
def atomic_output_path(path: Path) -> Iterator[Path]:
    """Yield a temporary sibling path and publish it atomically on success.

    The temporary file is created in the same directory as ``path`` so that
    ``os.replace`` stays on one filesystem and is therefore atomic.
    """
    destination = Path(path)
    # Ensure the destination directory exists before creating the temp file.
    parent = ensure_directory(destination.parent if destination.parent != Path("") else Path("."))

    # mkstemp returns an open descriptor plus a unique name.  We close the
    # descriptor because callers write using normal path-based APIs.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)

    try:
        # Hand the temporary path to the caller.  If anything raises, the
        # except block below removes the partial file.
        yield temporary

        # Flush file data to disk before the rename, so a crash after the
        # rename cannot leave a zero-length or partially flushed file.  Open
        # read-write because on Windows os.fsync maps to _commit(), which
        # rejects read-only handles with EBADF.
        with open(temporary, "rb+") as handle:
            os.fsync(handle.fileno())

        # Atomic publish: either the old file remains or the new one appears.
        os.replace(temporary, destination)
    except BaseException:
        # Clean up the temporary file on any failure, including KeyboardInterrupt.
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write text atomically."""
    destination = Path(path)
    # Write into the temporary sibling, then let atomic_output_path publish it.
    with atomic_output_path(destination) as temporary:
        temporary.write_text(text, encoding=encoding)
    return destination


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Write bytes atomically."""
    destination = Path(path)
    # Same pattern as atomic_write_text, but for binary payloads.
    with atomic_output_path(destination) as temporary:
        temporary.write_bytes(data)
    return destination


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the hexadecimal SHA-256 checksum of a file.

    Reading in chunks keeps memory usage constant even for multi-GB NetCDF
    files.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        # The walrus operator stops the loop when read() returns b"".
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_matches(path: Path, expected: str) -> bool:
    """Compare a file checksum with the expected value."""
    # Normalize the expected value; an empty checksum is never valid.
    candidate = expected.strip().lower()
    if not candidate:
        return False

    # A missing file cannot match any checksum.
    file_path = Path(path)
    if not file_path.is_file():
        return False

    # Compare case-insensitively so manifests may store upper or lower hex.
    return sha256_file(file_path).lower() == candidate


def safe_filename_component(value: str) -> str:
    """Replace characters that are invalid in Windows and Linux filenames."""
    # Replace forbidden characters with an underscore.
    text = _INVALID_FILENAME_CHARS.sub("_", str(value))

    # Collapse whitespace and trim leading/trailing whitespace.
    text = _WHITESPACE.sub("_", text.strip())

    # Windows dislikes trailing dots and spaces.
    text = text.strip(" .")

    # An empty name is not usable; use a single underscore.
    if not text:
        text = "_"

    # Prefix reserved device names so they cannot collide with CON, NUL, etc.
    # Windows also reserves names with an extension, such as "CON.txt", so the
    # check uses the part before the first dot.
    if text.split(".", 1)[0].upper() in _RESERVED_NAMES:
        text = f"_{text}"

    # Keep components short enough for Windows path limits.  A short digest of
    # the original name avoids collisions when two long names share a prefix.
    if len(text) > 120:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        text = f"{text[:111]}-{digest}"
    return text
