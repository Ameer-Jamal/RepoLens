"""Provider-neutral access to manually runnable CI pipelines."""

from __future__ import annotations

from urllib.parse import quote, urlencode
from fnmatch import fnmatchcase

import requests
import yaml

from models.contribution_models import RepositoryRef
from services.provider_api import build_provider_client_for_name


LOG_LIMIT = 256_000


class PipelineService:
    def __init__(self, config):
        self.config = config

    def _context(self, repo):
        provider = (repo.get("provider") or self.config.get_provider() or "").lower()
        if provider not in {"github", "bitbucket"}:
            raise ValueError("Only GitHub and Bitbucket repositories are supported.")
        owner = repo.get("owner") or repo.get("workspace") or ""
        slug = repo.get("slug") or repo.get("name") or ""
        if not owner or not slug:
            raise ValueError("Repository owner and slug are required.")
        client = build_provider_client_for_name(provider, self.config)
        if provider == "github":
            if not (self.config.get_github_token() or "").strip():
                raise ValueError("A GitHub token with Actions access is required.")
            headers, auth = client._headers(), None
            base = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(slug, safe='')}"
        else:
            auth = client._auth()
            if not all(auth):
                raise ValueError("Bitbucket account email and API token are required.")
            headers = {"Accept": "application/json"}
            base = f"https://api.bitbucket.org/2.0/repositories/{quote(owner, safe='')}/{quote(slug, safe='')}"
        return provider, client, base, headers, auth

    @staticmethod
    def _request(method, url, *, headers=None, auth=None, **kwargs):
        response = requests.request(method, url, headers=headers, auth=auth, timeout=25, **kwargs)
        response.raise_for_status()
        return response

    def _pages(self, url, *, headers, auth, key, limit=None, predicate=None):
        records = []
        for _ in range(10):
            response = self._request("GET", url, headers=headers, auth=auth)
            payload = response.json()
            batch = payload if key == "__list__" and isinstance(payload, list) else payload.get(key, [])
            records.extend(item for item in batch if predicate is None or predicate(item))
            if limit is not None and len(records) >= limit:
                return records[:limit]
            if "api.bitbucket.org" in url:
                url = payload.get("next")
            else:
                url = response.links.get("next", {}).get("url")
            if not url:
                break
        return records

    def list_branches(self, repo):
        provider, _, base, headers, auth = self._context(repo)
        if provider == "github":
            items = self._pages(f"{base}/branches?per_page=100", headers=headers, auth=auth, key="__list__")
            # GitHub's branch response is a top-level array, unlike Bitbucket's page.
            return [item["name"] for item in items if item.get("name")]
        items = self._pages(f"{base}/refs/branches?pagelen=100", headers=headers, auth=auth, key="values")
        return [item["name"] for item in items if item.get("name")]

    def _definition(self, client, repo, path, ref):
        try:
            encoded_ref = quote(ref, safe="") if (repo.get("provider") or "").lower() == "bitbucket" else ref
            content = client.get_file_content(RepositoryRef.from_dict(repo), path, encoded_ref)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return {}
            raise
        try:
            data = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid pipeline definition in {path}: {exc}") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _inputs(raw):
        if not isinstance(raw, dict):
            return []
        return [{"name": str(name), "description": str(spec.get("description") or ""),
                 "required": bool(spec.get("required")), "default": spec.get("default"),
                 "type": spec.get("type") or "string", "options": spec.get("options") or []}
                for name, spec in raw.items() if isinstance(spec, dict)]

    def list_pipelines(self, repo, branch):
        branch = (branch or "").strip()
        if not branch or not self._branch_exists(repo, branch):
            raise ValueError(f"Remote branch '{branch}' does not exist.")
        provider, client, base, headers, auth = self._context(repo)
        if provider == "bitbucket":
            data = self._definition(client, repo, "bitbucket-pipelines.yml", branch)
            pipelines = data.get("pipelines") or {}
            result = []
            for pattern in (pipelines.get("branches") or {}):
                # The provider selects the matching branch definition when launched without a selector.
                if fnmatchcase(branch, pattern) and not any(p["kind"] == "branch" for p in result):
                    result.append({"id": "branch", "name": f"Branch pipeline ({pattern})", "kind": "branch", "inputs": []})
            if not result and pipelines.get("default"):
                result.append({"id": "default", "name": "Default branch pipeline", "kind": "branch", "inputs": []})
            for name, steps in (pipelines.get("custom") or {}).items():
                variables = steps[0].get("variables", []) if isinstance(steps, list) and steps and isinstance(steps[0], dict) else []
                inputs = [{"name": v.get("name", ""), "description": "", "required": not bool(v.get("default")),
                           "default": v.get("default"), "type": "string", "options": v.get("allowed-values") or []}
                          for v in variables if isinstance(v, dict) and v.get("name")]
                result.append({"id": name, "name": name, "kind": "custom", "inputs": inputs})
            return result
        workflows = self._pages(f"{base}/actions/workflows?per_page=100", headers=headers, auth=auth, key="workflows")
        result = []
        # GitHub requires workflow_dispatch in the default-branch workflow file.
        default_ref = repo.get("default_branch") or self._request("GET", base, headers=headers, auth=auth).json().get("default_branch")
        for workflow in workflows:
            if workflow.get("state") != "active":
                continue
            path = workflow.get("path") or ""
            if not path.startswith(".github/workflows/"):
                continue
            data = self._definition(client, repo, path, default_ref)
            triggers = data.get("on", data.get(True, {}))
            dispatch = triggers.get("workflow_dispatch") if isinstance(triggers, dict) else None
            if not ((isinstance(triggers, dict) and "workflow_dispatch" in triggers)
                    or (isinstance(triggers, list) and "workflow_dispatch" in triggers)
                    or triggers == "workflow_dispatch"):
                continue
            result.append({"id": str(workflow["id"]), "name": workflow.get("name") or path,
                           "kind": "workflow", "inputs": self._inputs(dispatch.get("inputs") if isinstance(dispatch, dict) else {})})
        return result

    def _branch_exists(self, repo, branch):
        _, client, _, _, _ = self._context(repo)
        return client.branch_exists(RepositoryRef.from_dict(repo), branch)

    def run_pipeline(self, repo, branch, pipeline_id, *, kind="", inputs=None):
        provider, _, base, headers, auth = self._context(repo)
        branch = (branch or "").strip()
        if not branch or not self._branch_exists(repo, branch):
            raise ValueError(f"Remote branch '{branch}' does not exist.")
        match = next((p for p in self.list_pipelines(repo, branch) if p["id"] == str(pipeline_id)), None)
        if not match:
            raise ValueError("Pipeline is not runnable on the selected branch.")
        if kind and kind != match["kind"]:
            raise ValueError("Pipeline kind does not match the selected pipeline.")
        values = {str(k): str(v).lower() if isinstance(v, bool) else str(v) for k, v in (inputs or {}).items()}
        if provider == "github":
            allowed = {field["name"] for field in match["inputs"]}
            if set(values) - allowed:
                raise ValueError("Unknown GitHub workflow input: " + ", ".join(sorted(set(values) - allowed)))
            for field in match["inputs"]:
                if field["required"] and not values.get(field["name"]) and field["default"] is None:
                    raise ValueError(f"Required input missing: {field['name']}")
                if field["options"] and field["name"] in values and values[field["name"]] not in field["options"]:
                    raise ValueError(f"Invalid choice for {field['name']}")
                if field.get("type") == "boolean" and field["name"] in values and values[field["name"]] not in {"true", "false"}:
                    raise ValueError(f"Invalid boolean for {field['name']}")
            response = self._request("POST", f"{base}/actions/workflows/{quote(str(pipeline_id), safe='')}/dispatches",
                                     headers=headers, auth=auth, json={"ref": branch, "inputs": values})
            payload = response.json() if response.content else {}
            run_id = str(payload.get("workflow_run_id") or "")
            url = payload.get("html_url") or f"https://github.com/{repo['owner']}/{repo['slug']}/actions/workflows/{pipeline_id}"
        else:
            if match["kind"] == "branch" and values:
                raise ValueError("Variables require a custom Bitbucket pipeline.")
            target = {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": branch}
            if match["kind"] == "custom":
                target["selector"] = {"type": "custom", "pattern": pipeline_id}
            payload = self._request("POST", f"{base}/pipelines/", headers=headers, auth=auth,
                                    json={"target": target, "variables": [{"key": k, "value": v} for k, v in values.items()]}).json()
            run_id = payload.get("uuid") or ""
            url = ((payload.get("links") or {}).get("html") or {}).get("href") or ""
        return {"run_id": run_id, "url": url, "provider": provider, "branch": branch, "pipeline_id": str(pipeline_id)}

    def list_runs(self, repo, pipeline_id="", branch="", limit=30):
        provider, _, base, headers, auth = self._context(repo)
        limit = min(max(int(limit), 1), 100)
        if provider == "github":
            path = f"/actions/workflows/{quote(str(pipeline_id), safe='')}/runs" if pipeline_id else "/actions/runs"
            url = f"{base}{path}?{urlencode({'per_page': limit, **({'branch': branch} if branch else {})})}"
            raw = self._pages(url, headers=headers, auth=auth, key="workflow_runs", limit=limit,
                              predicate=lambda r: not branch or r.get("head_branch") == branch)
            return [{"run_id": str(r.get("id")), "name": r.get("name") or "", "branch": r.get("head_branch") or "",
                     "status": r.get("status") or "", "conclusion": r.get("conclusion") or "", "url": r.get("html_url") or "",
                     "created_at": r.get("created_at") or ""} for r in raw if not branch or r.get("head_branch") == branch][:limit]
        params = {"pagelen": limit, "sort": "-created_on"}
        if branch:
            params["target.ref_name"] = branch
        if pipeline_id and pipeline_id not in {"branch", "default"}:
            params["target.selector.type"] = "custom"
            params["target.selector.pattern"] = pipeline_id
        def matches(run):
            target = run.get("target") or {}
            selector = target.get("selector") or {}
            if branch and target.get("ref_name") != branch:
                return False
            if pipeline_id in {"branch", "default"}:
                return selector.get("type") != "custom"
            return not pipeline_id or selector.get("pattern") == pipeline_id
        raw = self._pages(f"{base}/pipelines/?{urlencode(params)}", headers=headers, auth=auth, key="values",
                          limit=limit, predicate=matches)
        return [{"run_id": r.get("uuid") or "", "name": ((r.get("target") or {}).get("selector") or {}).get("pattern") or "Branch pipeline",
                 "branch": (r.get("target") or {}).get("ref_name") or "", "status": ((r.get("state") or {}).get("name")) or "",
                 "conclusion": (((r.get("state") or {}).get("result") or {}).get("name")) or "",
                 "url": ((r.get("links") or {}).get("html") or {}).get("href") or "", "created_at": r.get("created_on") or ""}
                for r in raw]

    def get_run(self, repo, run_id):
        provider, _, base, headers, auth = self._context(repo)
        if not run_id:
            raise ValueError("Run ID is required.")
        escaped = quote(str(run_id), safe="")
        if provider == "github":
            run = self._request("GET", f"{base}/actions/runs/{escaped}", headers=headers, auth=auth).json()
            jobs = self._pages(f"{base}/actions/runs/{escaped}/jobs?per_page=100", headers=headers, auth=auth, key="jobs")
            steps = [{"id": str(j.get("id")), "name": j.get("name") or "", "status": j.get("status") or "",
                      "conclusion": j.get("conclusion") or "", "steps": j.get("steps") or []} for j in jobs]
            status, conclusion, url = run.get("status") or "", run.get("conclusion") or "", run.get("html_url") or ""
        else:
            run = self._request("GET", f"{base}/pipelines/{escaped}", headers=headers, auth=auth).json()
            raw_steps = self._pages(f"{base}/pipelines/{escaped}/steps?pagelen=100", headers=headers, auth=auth, key="values")
            steps = [{"id": s.get("uuid") or "", "name": s.get("name") or "Step", "status": (s.get("state") or {}).get("name") or "",
                      "conclusion": ((s.get("state") or {}).get("result") or {}).get("name") or ""} for s in raw_steps]
            status = (run.get("state") or {}).get("name") or ""
            conclusion = ((run.get("state") or {}).get("result") or {}).get("name") or ""
            url = ((run.get("links") or {}).get("html") or {}).get("href") or ""
        return {"run_id": str(run_id), "provider": provider, "status": status, "conclusion": conclusion,
                "url": url, "steps": steps}

    def get_log(self, repo, run_id, step_id, max_chars=LOG_LIMIT):
        provider, _, base, headers, auth = self._context(repo)
        if not run_id or not step_id:
            raise ValueError("Run ID and step or job ID are required.")
        limit = min(max(int(max_chars), 1), LOG_LIMIT)
        run, step = quote(str(run_id), safe=""), quote(str(step_id), safe="")
        url = f"{base}/actions/jobs/{step}/logs" if provider == "github" else f"{base}/pipelines/{run}/steps/{step}/log"
        if provider == "bitbucket":
            # The step log endpoint serves a raw stream and answers 406 to Accept: application/json.
            headers = {**headers, "Accept": "application/octet-stream"}
        response = self._request("GET", url, headers=headers, auth=auth, stream=True)
        chunks, size = [], 0
        try:
            for chunk in response.iter_content(chunk_size=8192):
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    break
        finally:
            response.close()
        content = b"".join(chunks)
        return {"run_id": str(run_id), "step_id": str(step_id), "text": content[:limit].decode("utf-8", errors="replace"),
                "truncated": size > limit}
