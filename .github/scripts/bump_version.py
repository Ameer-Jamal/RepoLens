#!/usr/bin/env python3
"""
Automated version bumper for RepoLens.
Reads pyproject.toml, computes the next semantic version (patch, minor, major, or auto based on git commits),
updates pyproject.toml and repolens.spec, and sets GITHUB_OUTPUT variables.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def parse_semver(v: str) -> tuple[int, int, int]:
    v = v.lstrip("v").strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not match:
        raise ValueError(f"Invalid semver version string: '{v}'")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_semver(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def get_current_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return "1.0.0"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "1.0.0"


def get_commits_since_last_tag() -> list[str]:
    try:
        last_tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        log_range = f"{last_tag}..HEAD"
    except Exception:
        log_range = "HEAD"

    try:
        out = subprocess.check_output(
            ["git", "log", log_range, "--oneline"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def tag_exists(tag: str) -> bool:
    try:
        out = subprocess.check_output(
            ["git", "tag", "-l", tag],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return bool(out)
    except Exception:
        return False


def determine_auto_bump(commits: list[str]) -> str:
    for c in commits:
        lower = c.lower()
        if "breaking change" in lower or "!:" in lower:
            return "major"
    for c in commits:
        lower = c.lower()
        parts = lower.split()
        if len(parts) > 1 and any(
            parts[1].startswith(prefix) for prefix in ("feat:", "feat(")
        ):
            return "minor"
    return "patch"


def calculate_next_version(current: str, bump_type: str) -> str:
    if bump_type == "none":
        return current
    major, minor, patch = parse_semver(current)
    if bump_type == "major":
        return format_semver(major + 1, 0, 0)
    elif bump_type == "minor":
        return format_semver(major, minor + 1, 0)
    elif bump_type == "patch":
        return format_semver(major, minor, patch + 1)
    else:
        raise ValueError(f"Unknown bump type '{bump_type}'")


def update_pyproject(root: Path, new_version: str) -> None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return
    text = pyproject.read_text(encoding="utf-8")
    updated = re.sub(
        r'^(version\s*=\s*["\'])[^"\']+(["\'])',
        rf"\g<1>{new_version}\g<2>",
        text,
        flags=re.MULTILINE,
    )
    pyproject.write_text(updated, encoding="utf-8")


def update_spec_file(root: Path, new_version: str) -> None:
    spec = root / "repolens.spec"
    if not spec.exists():
        return
    text = spec.read_text(encoding="utf-8")
    text = re.sub(
        r"('CFBundleVersion':\s*')[^']+(\')",
        rf"\g<1>{new_version}\g<2>",
        text,
    )
    text = re.sub(
        r"('CFBundleShortVersionString':\s*')[^']+(\')",
        rf"\g<1>{new_version}\g<2>",
        text,
    )
    spec.write_text(text, encoding="utf-8")


def set_github_output(name: str, value: str) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto bump RepoLens version")
    parser.add_argument(
        "--type",
        choices=["auto", "patch", "minor", "major", "none"],
        default="auto",
        help="Semantic bump type (auto checks commit messages)",
    )
    parser.add_argument(
        "--set-version",
        default="",
        help="Explicit version to set (overrides bump calculation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print computed version without modifying files",
    )
    args = parser.parse_args()

    root = repo_root()
    current = get_current_version(root)

    if args.set_version:
        new_version = args.set_version.lstrip("v").strip()
    elif args.type == "none":
        new_version = current
    elif args.type == "auto":
        # If the current version is not yet tagged in git, publish the current version first
        if not tag_exists(f"v{current}"):
            new_version = current
        else:
            commits = get_commits_since_last_tag()
            bump_type = determine_auto_bump(commits)
            new_version = calculate_next_version(current, bump_type)
    else:
        new_version = calculate_next_version(current, args.type)

    tag = f"v{new_version}"
    print(f"Current version: {current}")
    print(f"New version:     {new_version}")
    print(f"Tag:             {tag}")

    if not args.dry_run:
        update_pyproject(root, new_version)
        update_spec_file(root, new_version)
        print("Updated pyproject.toml and repolens.spec")

    set_github_output("version", new_version)
    set_github_output("tag", tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
