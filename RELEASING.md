# Releasing RepoLens

RepoLens uses automated GitHub Actions to build standalone desktop binaries (macOS, Windows, Linux) and Python distribution packages (wheel & sdist), and automatically publish them to GitHub Releases.

## Automated Version Bumping & Releases

The release pipeline includes an automated semantic version bumper (`.github/scripts/bump_version.py`) that syncs versions across `pyproject.toml` and `repolens.spec`.

### Option A: Trigger Release via GitHub Actions (Recommended)

You can trigger a release directly from GitHub without manually editing version files or tagging:

1. Go to **Actions** > [**Release workflow**](https://github.com/Ameer-Jamal/RepoLens/actions/workflows/release.yml).
2. Click **Run workflow**.
3. Select your **Version bump strategy**:
   - `auto` (*Default*): Analyzes conventional commit messages since the last release tag:
     - Breaking change (e.g., `feat!: ...` or `BREAKING CHANGE`): bumps **major** (e.g., `1.0.0` → `2.0.0`).
     - New feature (e.g., `feat: ...`): bumps **minor** (e.g., `1.0.0` → `1.1.0`).
     - Bug fixes and maintenance (e.g., `fix: ...`): bumps **patch** (e.g., `1.0.0` → `1.0.1`).
     - *Note*: If the current version has not been tagged yet (e.g. initial `1.0.0`), `auto` publishes `1.0.0`.
   - `patch`: Increments patch version (e.g., `1.0.0` → `1.0.1`).
   - `minor`: Increments minor version (e.g., `1.0.0` → `1.1.0`).
   - `major`: Increments major version (e.g., `1.0.0` → `2.0.0`).
   - `custom`: Specify an explicit version in the `custom_version` input (e.g. `2.0.0-rc1`).
   - `none`: Keeps the current version in `pyproject.toml`.
4. Optionally check **Create as a draft release** or **Create as a prerelease**.
5. Click **Run workflow**.

The workflow will automatically:
- Compute the new version and update `pyproject.toml` and `repolens.spec`.
- Commit the version bump back to your branch with `[skip ci]`.
- Create and push the release git tag (`vX.Y.Z`).
- Build wheels, sdist, and desktop binaries for macOS, Windows, and Linux.
- Publish the GitHub Release with generated release notes and attached binaries.

---

### Option B: Push a Git Tag Manually

If you prefer to tag manually from your local terminal:

1. **Verify all tests pass**:
   ```bash
   pytest tests/
   ```

2. **Tag and push**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. The GitHub Actions release workflow will detect the `v*` tag push and build and publish the release for that exact tag.

---

### CLI Helper: Previewing or Bumping Versions Locally

You can run the bump script locally at any time:

```bash
# Preview what the next version would be without making changes:
python .github/scripts/bump_version.py --dry-run

# Preview a specific bump:
python .github/scripts/bump_version.py --type minor --dry-run

# Apply a bump locally:
python .github/scripts/bump_version.py --type patch
```

---

### Optional: Publishing to PyPI

If you wish to publish the Python package to PyPI (`pip install repolens`):
1. Generate an API token on [pypi.org](https://pypi.org/manage/account/token/).
2. Add it as a secret in your GitHub repository:
   - Go to **Settings > Secrets and variables > Actions > New repository secret**.
   - Name: `PYPI_API_TOKEN`.
   - Value: `<your-pypi-token>`.
3. The release workflow will automatically detect this secret and upload wheels and source archives to PyPI.

