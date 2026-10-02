from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional


def _normalize_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class RepositoryRef:
    provider: str
    workspace: str
    slug: str
    display_name: str
    full_name: str
    web_url: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.full_name.lower()}"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RepositoryRef":
        workspace = value.get("workspace") or value.get("owner") or ""
        slug = value.get("slug") or value.get("repo") or value.get("name") or ""
        full_name = value.get("full_name") or ""
        if not full_name and workspace and slug:
            full_name = f"{workspace}/{slug}"
        return cls(
            provider=(value.get("provider") or "").lower(),
            workspace=workspace,
            slug=slug,
            display_name=value.get("display_name") or value.get("name") or full_name,
            full_name=full_name,
            web_url=value.get("web_url") or value.get("html_url") or "",
        )


@dataclass(frozen=True)
class ContributionScope:
    scope_type: str
    repositories: tuple[RepositoryRef, ...]
    label: str


@dataclass
class ContributionRecord:
    record_type: str
    provider: str
    workspace: str
    repository: str
    repository_display: str
    author: str
    created_at: Optional[datetime]
    merged_at: Optional[datetime] = None
    title: str = ""
    description: str = ""
    message: str = ""
    state: str = ""
    pr_id: str = ""
    commit_hash: str = ""
    source_branch: str = ""
    destination_branch: str = ""
    branch: str = ""
    link: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)
    ticket_id: str = ""
    additions: Optional[int] = None
    deletions: Optional[int] = None
    file_count: Optional[int] = None

    @property
    def primary_text(self) -> str:
        return self.title or self.message

    @property
    def effective_date(self) -> Optional[datetime]:
        return self.merged_at or self.created_at

    @property
    def unique_key(self) -> str:
        if self.record_type == "pr":
            return f"pr:{self.provider}:{self.repository.lower()}:{self.pr_id}"
        return f"commit:{self.provider}:{self.repository.lower()}:{self.commit_hash.lower()}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat() if self.created_at else ""
        payload["merged_at"] = self.merged_at.isoformat() if self.merged_at else ""
        payload["labels"] = list(self.labels)
        return payload


@dataclass
class ContributionHistoryQuery:
    developer: str
    start_date: Optional[date]
    end_date: Optional[date]
    scope_type: str
    contribution_type: str
    search_text: str = ""
    branch_filter: str = ""
    exclude_bots: bool = True
    group_by: str = "none"
    titles_only: bool = False
    scope_repositories: Optional[tuple[RepositoryRef, ...]] = None
    scope_label_override: str = ""


@dataclass
class ContributionHistoryResult:
    records: list[ContributionRecord]
    repositories_scanned: tuple[RepositoryRef, ...]
    scope: ContributionScope
    partial_errors: list[str] = field(default_factory=list)
    grouped_records: list[tuple[str, list[ContributionRecord]]] = field(default_factory=list)
    total_prs: int = 0
    total_commits: int = 0
    active_repositories: tuple[str, ...] = field(default_factory=tuple)
    top_repositories: tuple[str, ...] = field(default_factory=tuple)
    ticket_prefixes: tuple[str, ...] = field(default_factory=tuple)


def normalize_record_datetime(value: Optional[str]) -> Optional[datetime]:
    return _normalize_datetime(value)
