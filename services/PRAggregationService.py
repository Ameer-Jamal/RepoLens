from typing import Dict, List, Tuple


class PRAggregationService:
    """Pure helpers for multi-repo PR pagination orchestration."""

    @staticmethod
    def repo_cursor_key(repo: dict) -> str:
        repo = repo or {}
        return str(repo.get("id") or f"{repo.get('provider') or ''}:{repo.get('owner') or ''}/{repo.get('slug') or repo.get('name') or ''}")

    @staticmethod
    def seed_cursor_state(selected_repos: List[dict]) -> Dict[str, str]:
        state: Dict[str, str] = {}
        for repo in selected_repos or []:
            repo_id = PRAggregationService.repo_cursor_key(repo)
            if repo_id:
                state[repo_id] = ""
        return state

    @staticmethod
    def repos_for_page(selected_repos: List[dict], cursor_state: Dict[str, str], reset: bool) -> List[Tuple[dict, str]]:
        pairs: List[Tuple[dict, str]] = []
        if reset:
            for repo in selected_repos or []:
                pairs.append((repo, ""))
            return pairs

        # Only repos with a non-empty next cursor should be fetched again.
        for repo in selected_repos or []:
            repo_id = PRAggregationService.repo_cursor_key(repo)
            if not repo_id:
                continue
            next_cursor = (cursor_state or {}).get(repo_id, "")
            if next_cursor:
                pairs.append((repo, next_cursor))
        return pairs

    @staticmethod
    def has_more(cursor_state: Dict[str, str]) -> bool:
        if not cursor_state:
            return False
        return any(bool(v) for v in cursor_state.values())

    @staticmethod
    def normalize_next_state(selected_repos: List[dict], repo_next_tokens: Dict[str, str]) -> Dict[str, str]:
        state: Dict[str, str] = {}
        token_map = repo_next_tokens or {}
        for repo in selected_repos or []:
            repo_id = PRAggregationService.repo_cursor_key(repo)
            if not repo_id:
                continue
            state[repo_id] = token_map.get(repo_id, "") or ""
        return state
