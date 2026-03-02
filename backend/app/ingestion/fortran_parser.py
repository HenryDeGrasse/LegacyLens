"""Fortran 77 fixed-form parser for SPICE Toolkit source files.

Parses SPICE .f files into structured RoutineInfo objects by detecting:
- Comment lines (column 1: C, c, *, !)
- Continuation lines (column 6 non-blank)
- Routine boundaries (SUBROUTINE, FUNCTION, PROGRAM, ENTRY)
- SPICE header sections (C$ markers: Abstract, Keywords, Brief_I/O, etc.)
- CALL and INCLUDE statements
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RoutineInfo:
    """Parsed representation of a Fortran routine."""

    name: str
    kind: str  # SUBROUTINE, FUNCTION, PROGRAM, ENTRY
    file_path: str
    start_line: int
    end_line: int
    header_comments: str = ""
    body_code: str = ""
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    brief_io: str = ""
    calls: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    parent_routine: str = ""  # For ENTRY points, name of parent


# Regex patterns
_ROUTINE_RE = re.compile(
    r"^\s*(?:INTEGER\s+|DOUBLE\s+PRECISION\s+|LOGICAL\s+|CHARACTER\s*(?:\*\s*\(?\s*[^)]*\)?\s+)?)??"
    r"(SUBROUTINE|FUNCTION|PROGRAM|ENTRY|BLOCK\s+DATA)\s+(\w+)",
    re.IGNORECASE,
)

_TYPED_FUNC_RE = re.compile(
    r"^\s*(?:INTEGER|DOUBLE\s+PRECISION|REAL|LOGICAL|CHARACTER\s*(?:\*\s*\(?\s*[^)]*\)?)?)\s+"
    r"FUNCTION\s+(\w+)",
    re.IGNORECASE,
)

_END_RE = re.compile(
    r"^\s*END\s*(?:(?:SUBROUTINE|FUNCTION|PROGRAM|BLOCK\s+DATA)\s*(\w*))?\s*$",
    re.IGNORECASE,
)
# Must NOT match END IF, END DO, END WHERE, etc.

_CALL_RE = re.compile(r"\bCALL\s+(\w+)", re.IGNORECASE)
_INCLUDE_RE = re.compile(r"^\s*INCLUDE\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
_SECTION_RE = re.compile(r"^C\$\s*(\w+)", re.IGNORECASE)
_PROCEDURE_RE = re.compile(r"^C\$Procedure", re.IGNORECASE)

# Executable statement patterns — these mark the transition from header/declarations to body
_EXECUTABLE_RE = re.compile(
    r"^\s*(?:CALL\s|IF\s*\(|DO\s|GO\s*TO|GOTO|RETURN|WRITE\s*\(|READ\s*\(|"
    r"PRINT\s|ASSIGN\s|CONTINUE|STOP|PAUSE|SAVE\b(?!\s))",
    re.IGNORECASE,
)

# Declaration patterns (non-executable, part of header region)
_DECLARATION_RE = re.compile(
    r"^\s*(?:IMPLICIT|INTEGER|DOUBLE\s+PRECISION|REAL|LOGICAL|CHARACTER|"
    r"DIMENSION|COMMON|EQUIVALENCE|EXTERNAL|INTRINSIC|DATA\s|"
    r"PARAMETER\s*\(|INCLUDE\s|SAVE\s)",
    re.IGNORECASE,
)


def is_comment(line: str) -> bool:
    """Check if a line is a Fortran 77 comment (column 1 indicator)."""
    if not line:
        return True  # Empty lines treated as comment-like
    return line[0] in ("C", "c", "*", "!")


def is_continuation(line: str) -> bool:
    """Check if a line is a continuation (column 6 non-blank, non-zero)."""
    if len(line) < 6:
        return False
    if is_comment(line):
        return False
    return line[5] not in (" ", "0", "\t")


def get_statement(line: str) -> str:
    """Extract the statement portion (columns 7-72) from a source line."""
    if is_comment(line):
        return ""
    if len(line) > 6:
        return line[6:72] if len(line) >= 72 else line[6:]
    return ""


def _is_executable_statement(stmt: str) -> bool:
    """Check if a statement is executable (not a declaration)."""
    if not stmt.strip():
        return False
    if _DECLARATION_RE.match(stmt):
        return False
    if _EXECUTABLE_RE.match(stmt):
        return True
    # Assignment statements (variable = expression) are executable
    # But be careful not to match PARAMETER assignments
    if "=" in stmt and not stmt.strip().startswith("PARAMETER"):
        # Simple heuristic: if it has = and isn't a declaration, it's executable
        lhs = stmt.split("=")[0].strip()
        if lhs and lhs[0].isalpha() and "(" not in lhs[:3]:
            return True
    return False


def _parse_header_sections(comment_lines: list[str]) -> dict[str, str]:
    """Parse C$ section markers from header comment block."""
    sections: dict[str, list[str]] = {}
    current_section = None

    for line in comment_lines:
        stripped = line.rstrip()
        match = _SECTION_RE.match(stripped)
        if match:
            section_name = match.group(1).strip()
            current_section = section_name
            if current_section not in sections:
                sections[current_section] = []
            continue

        if current_section is not None:
            content = stripped
            if content and content[0] in ("C", "c", "*", "!"):
                content = content[1:]
            if content.startswith(" "):
                content = content[1:]
            sections[current_section].append(content)

    return {k: "\n".join(v).strip() for k, v in sections.items()}


def parse_file(path: Path) -> list[RoutineInfo]:
    """Parse a Fortran 77 source file into RoutineInfo objects.

    SPICE files have a specific structure:
    1. C$Procedure line
    2. SUBROUTINE/FUNCTION declaration
    3. Massive C$ header documentation interleaved with declarations
    4. Executable code body

    We split at the first executable statement.
    """
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except Exception as e:
        print(f"Warning: Could not read {path}: {e}")
        return []

    routines: list[RoutineInfo] = []
    file_str = str(path)

    # First pass: find all routine boundaries
    routine_starts: list[tuple[int, str, str]] = []  # (line_num, kind, name)
    routine_ends: list[int] = []

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        if is_comment(line):
            continue
        stmt = get_statement(line)
        if not stmt.strip():
            continue

        match = _ROUTINE_RE.match(stmt)
        if match:
            kind = match.group(1).upper().replace("  ", " ")
            name = match.group(2).upper()

            # Check for typed FUNCTION
            typed_match = _TYPED_FUNC_RE.match(stmt)
            if typed_match:
                kind = "FUNCTION"
                name = typed_match.group(1).upper()

            if kind != "ENTRY":
                routine_starts.append((i, kind, name))

        end_match = _END_RE.match(stmt)
        if end_match:
            routine_ends.append(i)

    if not routine_starts:
        # No routines found — might be an include file or data-only
        return []

    # Second pass: for each routine, collect header and body
    for idx, (start_idx, kind, name) in enumerate(routine_starts):
        # Find the END for this routine
        end_idx = len(lines) - 1
        for e in routine_ends:
            if e > start_idx:
                # Make sure this END is before the next routine start
                if idx + 1 < len(routine_starts) and e >= routine_starts[idx + 1][0]:
                    continue
                end_idx = e
                break

        # Collect all lines for this routine (from start to END)
        routine_lines = lines[start_idx : end_idx + 1]

        # Also collect preceding comment block (C$Procedure header etc.)
        pre_comments = []
        j = start_idx - 1
        while j >= 0:
            prev = lines[j].rstrip()
            if is_comment(prev) or prev.strip() == "":
                pre_comments.insert(0, prev)
                j -= 1
            else:
                break
            # Don't cross into another routine's END
            if j >= 0 and any(e == j for e in routine_ends):
                break

        # Now split routine_lines into header (comments + declarations) and body (executable)
        header_lines = list(pre_comments)
        body_lines: list[str] = []
        entry_points: list[str] = []
        calls: set[str] = set()
        includes: list[str] = []
        found_executable = False

        for line_offset, raw_line in enumerate(routine_lines):
            line = raw_line.rstrip()
            abs_line = start_idx + line_offset

            if not found_executable:
                if is_comment(line) or line.strip() == "":
                    header_lines.append(line)
                    continue

                stmt = get_statement(line)

                # Check for ENTRY
                entry_match = _ROUTINE_RE.match(stmt)
                if entry_match and entry_match.group(1).upper() == "ENTRY":
                    entry_points.append(entry_match.group(2).upper())
                    body_lines.append(line)
                    found_executable = True  # ENTRY is in the body region
                    continue

                # Check for INCLUDE
                inc_match = _INCLUDE_RE.match(stmt)
                if inc_match:
                    includes.append(inc_match.group(1))
                    header_lines.append(line)
                    continue

                # Declaration? Part of header.
                if _DECLARATION_RE.match(stmt):
                    header_lines.append(line)
                    continue

                # The routine declaration itself is part of header
                if line_offset == 0:
                    header_lines.append(line)
                    continue

                # Executable statement — transition to body
                if _is_executable_statement(stmt):
                    found_executable = True
                    body_lines.append(line)

                    # Extract calls
                    for cm in _CALL_RE.finditer(stmt):
                        calls.add(cm.group(1).upper())
                    continue

                # Unknown non-comment, non-declaration — conservatively put in header
                header_lines.append(line)
            else:
                body_lines.append(line)
                if not is_comment(line):
                    stmt = get_statement(line)
                    # Extract CALL statements
                    for cm in _CALL_RE.finditer(stmt):
                        calls.add(cm.group(1).upper())
                    # Extract INCLUDE
                    inc_match = _INCLUDE_RE.match(stmt)
                    if inc_match:
                        includes.append(inc_match.group(1))
                    # Extract ENTRY
                    entry_match = _ROUTINE_RE.match(stmt)
                    if entry_match and entry_match.group(1).upper() == "ENTRY":
                        entry_points.append(entry_match.group(2).upper())

        # Parse header sections
        sections = _parse_header_sections(header_lines)
        abstract = sections.get("Abstract", "")
        keywords_raw = sections.get("Keywords", "")
        keywords = [k.strip() for k in keywords_raw.split("\n") if k.strip()]
        brief_io = sections.get("Brief_I/O", "")

        routine = RoutineInfo(
            name=name,
            kind=kind,
            file_path=file_str,
            start_line=start_idx + 1,  # 1-indexed
            end_line=end_idx + 1,
            header_comments="\n".join(header_lines),
            body_code="\n".join(body_lines),
            abstract=abstract,
            keywords=keywords,
            brief_io=brief_io,
            calls=sorted(calls),
            includes=includes,
            entry_points=entry_points,
        )
        routines.append(routine)

    # Third pass: create ENTRY point RoutineInfo objects with their own C$Procedure headers
    entry_routines = _extract_entry_headers(path, lines, routines)
    routines.extend(entry_routines)

    return routines


def _extract_entry_headers(
    path: Path, lines: list[str], routines: list[RoutineInfo]
) -> list[RoutineInfo]:
    """Extract C$Procedure headers for ENTRY points and create RoutineInfo for each."""
    entry_infos: list[RoutineInfo] = []

    for routine in routines:
        for entry_name in routine.entry_points:
            # Find the ENTRY statement and collect headers before AND after it
            entry_header_lines: list[str] = []
            entry_line_num = 0

            for i, raw_line in enumerate(lines):
                line = raw_line.rstrip()
                stmt = get_statement(line) if not is_comment(line) else ""
                match = _ROUTINE_RE.match(stmt) if stmt else None
                if match and match.group(1).upper() == "ENTRY" and match.group(2).upper() == entry_name:
                    entry_line_num = i + 1

                    # Walk backwards for C$Procedure header
                    pre_lines: list[str] = []
                    j = i - 1
                    while j >= 0 and (is_comment(lines[j]) or lines[j].strip() == ""):
                        pre_lines.insert(0, lines[j].rstrip())
                        j -= 1

                    # Walk forwards for C$ Abstract etc. after ENTRY line
                    post_lines: list[str] = []
                    j = i + 1
                    while j < len(lines) and (is_comment(lines[j]) or lines[j].strip() == ""):
                        post_lines.append(lines[j].rstrip())
                        j += 1

                    entry_header_lines = pre_lines + [line] + post_lines
                    break

            sections = _parse_header_sections(entry_header_lines)

            entry_info = RoutineInfo(
                name=entry_name,
                kind="ENTRY",
                file_path=str(path),
                start_line=entry_line_num,
                end_line=routine.end_line,
                header_comments="\n".join(entry_header_lines),
                body_code="",  # ENTRY shares parent's body
                abstract=sections.get("Abstract", f"Entry point in {routine.name}"),
                keywords=[k.strip() for k in sections.get("Keywords", "").split("\n") if k.strip()],
                brief_io=sections.get("Brief_I/O", ""),
                calls=[],
                includes=[],
                parent_routine=routine.name,
            )
            entry_infos.append(entry_info)

    return entry_infos


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m app.ingestion.fortran_parser <file.f>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    routines = parse_file(filepath)

    for r in routines:
        print(f"\n{'='*60}")
        print(f"  Name:     {r.name}")
        print(f"  Kind:     {r.kind}")
        print(f"  Lines:    {r.start_line}-{r.end_line}")
        print(f"  Abstract: {r.abstract[:120]}..." if len(r.abstract) > 120 else f"  Abstract: {r.abstract}")
        print(f"  Keywords: {r.keywords}")
        print(f"  Calls:    {r.calls[:10]}{'...' if len(r.calls) > 10 else ''}")
        print(f"  Includes: {r.includes}")
        if r.entry_points:
            print(f"  Entries:  {r.entry_points}")
        if r.parent_routine:
            print(f"  Parent:   {r.parent_routine}")
        print(f"  Header:   {len(r.header_comments)} chars")
        print(f"  Body:     {len(r.body_code)} chars")
