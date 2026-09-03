# Releasing

The tag is the decision. Pushing `vX.Y.Z` builds, checks, and publishes
that version to PyPI; nothing else does.

## Once, before the first release

PyPI must be told to trust this workflow. Nothing is stored here, so
there is no token to leak or rotate.

1. Sign in to <https://pypi.org> and open **Your projects → Publishing**,
   or <https://pypi.org/manage/account/publishing/> for a name that does
   not exist yet.
2. Add a **pending publisher**:
   - PyPI project name: `interactive-semantic-hunt`
   - Owner: `davetothek`
   - Repository: `interactive-semantic-hunt`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In GitHub, open **Settings → Environments** and add an environment
   named `pypi`. Require a reviewer if a release should need approval.

## Each release

1. Decide the version and set it in `pyproject.toml`.
2. `uv run poe check` — the release workflow runs it again, and refuses
   a tag whose name disagrees with the version it finds.
3. Commit, then tag and push:

   ```sh
   git commit -am "Release 0.1.0"
   git tag v0.1.0
   git push origin main --tags
   ```

4. Watch the run. The publish job waits on the build job, so a failing
   check never reaches PyPI.

## What ships

The wheel carries the package and three commands: `ish`, `ish-mcp`, and
`ish-complete`. It depends on nothing that compiles: the default backend
reaches Ollama over HTTP with the standard library.

A backend that needs more is an extra, so a reader who wants one asks
for it:

```sh
pip install interactive-semantic-hunt          # Ollama, the default
pip install "interactive-semantic-hunt[llama]" # in-process llama.cpp
pip install "interactive-semantic-hunt[st]"    # sentence-transformers
```

The build job installs the wheel on its own and runs it, because a
package that cannot be installed alone is not releasable whatever the
tests say.
