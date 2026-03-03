"""File discovery for SPICE Toolkit source."""

from pathlib import Path


def scan_directory(
    root: str, extensions: list[str] | None = None
) -> list[Path]:
    """Recursively find all source files matching given extensions.

    Args:
        root: Root directory to scan.
        extensions: File extensions to match (e.g., ['.f', '.inc']).
            Defaults to ['.f', '.inc'].

    Returns:
        Sorted list of matching file paths.
    """
    if extensions is None:
        extensions = [".f", ".inc"]

    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Source directory not found: {root}")

    files = []
    for ext in extensions:
        files.extend(root_path.rglob(f"*{ext}"))

    return sorted(files)


def get_file_stats(paths: list[Path]) -> dict:
    """Get statistics about a list of source files.

    Returns:
        Dict with 'file_count', 'total_loc', and 'by_extension' breakdown.
    """
    by_ext: dict[str, int] = {}
    total_loc = 0

    for p in paths:
        ext = p.suffix.lower()
        with p.open(encoding="latin-1") as fh:
            loc = sum(1 for _ in fh)
        by_ext[ext] = by_ext.get(ext, 0) + 1
        total_loc += loc

    return {
        "file_count": len(paths),
        "total_loc": total_loc,
        "by_extension": by_ext,
    }


if __name__ == "__main__":
    import sys

    source_dir = sys.argv[1] if len(sys.argv) > 1 else "../data/spice"
    files = scan_directory(source_dir)
    stats = get_file_stats(files)

    print(f"Source directory: {source_dir}")
    print(f"Total files: {stats['file_count']}")
    print(f"Total LOC:   {stats['total_loc']:,}")
    print("By extension:")
    for ext, count in sorted(stats["by_extension"].items()):
        print(f"  {ext}: {count}")
