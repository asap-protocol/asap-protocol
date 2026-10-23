# Contributing to ASAP Protocol

Thanks for helping out! Here's how to get started quickly.

## Quick Start

1.  **Setup**: You need `uv` installed. Use `--dev` so that dev dependencies (pytest-xdist, ruff, mypy, etc.) are installed. Local test runs use the fast loop in [Canonical commands](.cursor/README.md#canonical-commands). Keep `serial_mcp` out of `pytest -n auto`.
    ```bash
    git clone https://github.com/asap-protocol/asap-protocol.git
    cd asap-protocol
    uv sync --all-extras --dev
    ```

2.  **Verify**: run the local floor in [Canonical commands](.cursor/README.md#canonical-commands).

## Development Workflow

-   **Linting & Formatting**: `uv run ruff check .` and `uv run ruff format .`
-   **Type Checking**: `uv run mypy src/ scripts/ tests/`
-   **Testing**: [Canonical commands](.cursor/README.md#canonical-commands). The coverage job enforces the 85% floor on the CI test job's artifact.

## Pull Requests

1.  **Branch**: Create a feature branch (`git checkout -b feature/my-cool-feature`).
2.  **Commit**: Use [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `feat: add awesome feature`, `fix: resolve crash`).
3.  **Test**: The local floor in [Canonical commands](.cursor/README.md#canonical-commands) passes.
4.  **Push**: Open a PR on GitHub.

## Testing

### Test Structure

Tests are organized into three categories:

- **Unit tests** (`tests/transport/unit/`): Test isolated components without HTTP or rate limiting dependencies
- **Integration tests** (`tests/transport/integration/`): Test component interactions within the transport layer
- **E2E tests** (`tests/transport/e2e/`): Test complete agent workflows

### Rate Limiting in Tests

**IMPORTANT**: To prevent rate limiting interference between tests:

1. **For non-rate-limiting tests**: Inherit from `NoRateLimitTestBase`:
   ```python
   from tests.transport.conftest import NoRateLimitTestBase

   class TestMyFeature(NoRateLimitTestBase):
       """Rate limiting is automatically disabled."""
       pass
   ```

2. **For rate limiting tests**: Use aggressive monkeypatch fixtures (see [Testing Guide](docs/testing.md))

3. **Run with parallel execution**: Use the fast loop in [Canonical commands](.cursor/README.md#canonical-commands). `serial_mcp` stays out of `-n auto`.

### Test Isolation Strategy

We use a three-pronged approach to ensure test isolation:

1. **Process isolation** (pytest-xdist): Tests run in separate processes
2. **Aggressive monkeypatch**: Module-level limiters are replaced for complete isolation
3. **Strategic organization**: Rate limiting tests are isolated in separate files

See the [Testing Guide](docs/testing.md) for complete details on:
- Test organization and structure
- Writing new tests
- Using fixtures
- Troubleshooting test interference

**Troubleshooting**: If you see `unrecognized arguments: -n`, run `uv sync --all-extras --dev` so pytest-xdist is installed. CI commands are in [Canonical commands](.cursor/README.md#canonical-commands). The coverage job enforces the 85% floor on the test job's artifact and uploads it.

## Guidelines

-   **Code Style**: Follow PEP 8 (handled by Ruff).
-   **Tests**: New features need tests. Bug fixes need regression tests.
    - Use `NoRateLimitTestBase` for tests that don't test rate limiting
    - See [Testing Guide](docs/testing.md) for detailed guidelines
-   **Docs**: Update docstrings and README if you change behavior.

## Reviewing Dependabot PRs

Dependabot automatically creates pull requests for dependency updates. Here's how to review and handle them:

### Types of Updates

1. **Security Updates**: Automatically created when vulnerabilities are detected
   - These are **high priority** and should be reviewed promptly
   - **Security**: See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

2. **Version Updates**: Created monthly for non-security dependency updates
   - These can be reviewed during regular maintenance windows
   - Focus on patch and minor updates first

### Review Workflow

1. **Check CI Status**: Ensure all CI checks pass (tests, linting, type checking)
2. **Review Changelog**: Check the dependency's changelog for breaking changes
3. **Test Locally** (if needed):
   ```bash
   git checkout <dependabot-branch>
   uv sync --all-extras
   uv run pytest
   ```
4. **Verify Compatibility**: Ensure the update doesn't break existing functionality
5. **Merge**: If everything looks good, merge the PR

### Target Review Times

These are target times for review, not strict commitments. As a solo maintainer, we aim to review updates within these windows:

- **Critical Security**: Aim for 3-5 business days
- **High Security**: Aim for 1-2 weeks
- **Medium Security**: Aim for 2-3 weeks
- **Low Security**: Aim for 1 month
- **Version Updates**: Review within the next monthly batch (or within 1 month)

### When to Defer

- If the update introduces breaking changes that require code modifications
- If CI tests fail and the failure is not related to the dependency update
- If the update conflicts with other ongoing work (coordinate with maintainers)

### Auto-merge

Currently, auto-merge is disabled. All Dependabot PRs require manual review to ensure compatibility and maintain code quality.

## Project Structure

-   `src/asap/models`: Core Pydantic models.
-   `src/asap/transport`: HTTP/JSON-RPC layer.
-   `src/asap/state`: State machine logic.
-   `tests/`: Where the magic is verified.

## Architecture & Design

Understanding the "why" behind our code is crucial. Please review:

-   **Tech Stack Decisions**: [engineering/architecture/tech-stack-decisions.md](engineering/architecture/tech-stack-decisions.md)
-   **ADRs**: [docs/adr](docs/adr)

## Need Help?

Check [Discussions](https://github.com/asap-protocol/asap-protocol/discussions) or open an [Issue](https://github.com/asap-protocol/asap-protocol/issues).

For **general, non-security inquiries** (coordination, integrations, press), you can reach maintainers at **[info@asap-protocol.com](mailto:info@asap-protocol.com)**. **Do not** use this address for vulnerability reports — follow [SECURITY.md](SECURITY.md) instead.

**Using AI coding tools?** See [AGENTS.md](AGENTS.md) for project-specific instructions optimized for Cursor, Copilot, Codex and other AI assistants.

By contributing, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md) and license your code under Apache 2.0.
