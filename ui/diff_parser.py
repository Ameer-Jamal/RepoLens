from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiffLine:
    old_line_num: Optional[int]
    new_line_num: Optional[int]
    line_type: str  # "add", "del", "context", "hunk_header"
    content: str    # Raw line content without the leading + / - / ' '


@dataclass
class DiffHunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class DiffFile:
    old_path: str
    new_path: str
    change_type: str  # "M", "A", "D", "R"
    hunks: list[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0

    @property
    def display_path(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    """Parse unified diff text into structured file and hunk objects."""
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    files: list[DiffFile] = []
    current_file: Optional[DiffFile] = None
    current_hunk: Optional[DiffHunk] = None

    curr_old = 0
    curr_new = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for new file diff header
        diff_match = _DIFF_GIT_RE.match(line)
        if diff_match:
            if current_hunk and current_file:
                current_file.hunks.append(current_hunk)
                current_hunk = None
            if current_file:
                files.append(current_file)

            old_p = diff_match.group(1).strip()
            new_p = diff_match.group(2).strip()
            current_file = DiffFile(
                old_path=old_p,
                new_path=new_p,
                change_type="M",
            )
            i += 1
            continue

        if not current_file:
            i += 1
            continue

        # File metadata lines
        if line.startswith("new file mode"):
            current_file.change_type = "A"
            i += 1
            continue
        elif line.startswith("deleted file mode"):
            current_file.change_type = "D"
            i += 1
            continue
        elif line.startswith("similarity index") or line.startswith("rename from"):
            current_file.change_type = "R"
            i += 1
            continue
        elif line.startswith("--- "):
            raw_path = line[4:].strip()
            if raw_path.startswith("a/"):
                current_file.old_path = raw_path[2:]
            elif raw_path == "/dev/null":
                current_file.old_path = "/dev/null"
                current_file.change_type = "A"
            i += 1
            continue
        elif line.startswith("+++ "):
            raw_path = line[4:].strip()
            if raw_path.startswith("b/"):
                current_file.new_path = raw_path[2:]
            elif raw_path == "/dev/null":
                current_file.new_path = "/dev/null"
                current_file.change_type = "D"
            i += 1
            continue

        # Hunk header
        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            if current_hunk:
                current_file.hunks.append(current_hunk)

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or 1)
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or 1)
            header_suffix = hunk_match.group(5) or ""

            current_hunk = DiffHunk(
                header=line,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            curr_old = old_start
            curr_new = new_start
            i += 1
            continue

        # Inside hunk
        if current_hunk is not None:
            if line.startswith("+"):
                current_file.additions += 1
                current_hunk.lines.append(
                    DiffLine(
                        old_line_num=None,
                        new_line_num=curr_new,
                        line_type="add",
                        content=line[1:],
                    )
                )
                curr_new += 1
            elif line.startswith("-"):
                current_file.deletions += 1
                current_hunk.lines.append(
                    DiffLine(
                        old_line_num=curr_old,
                        new_line_num=None,
                        line_type="del",
                        content=line[1:],
                    )
                )
                curr_old += 1
            elif line.startswith(" ") or line == "":
                content = line[1:] if line.startswith(" ") else ""
                current_hunk.lines.append(
                    DiffLine(
                        old_line_num=curr_old,
                        new_line_num=curr_new,
                        line_type="context",
                        content=content,
                    )
                )
                curr_old += 1
                curr_new += 1
            elif line.startswith("\\ No newline at end of file"):
                pass

        i += 1

    if current_hunk and current_file:
        current_file.hunks.append(current_hunk)
    if current_file:
        files.append(current_file)

    return files
