#!/usr/bin/env python3
"""Process agent registration from a GitHub Issue Form body (IssueOps).

Parses the issue body (markdown with ### headers from the register_agent template),
validates the submission against the Manifest schema and registry rules,
and either updates registry.json (valid) or writes validation errors to result.json (invalid).

Usage (from GitHub Actions):
  python scripts/process_registration.py --body "$ISSUE_BODY" --issue-number N --author "$GITHUB_ACTOR" --output result.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

# Add repo src and scripts/lib to sys.path when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from asap.discovery.registry import (  # noqa: E402
    derive_registry_hardware_fields,
    generate_registry_entry,
)
from asap.discovery.validation import (  # noqa: E402
    ManifestValidationError,
    validate_signed_manifest_response,
)
from asap.errors import SignatureVerificationError  # noqa: E402
from asap.models.entities import Manifest  # noqa: E402
from lib.debug_id import generate_debug_id  # noqa: E402
from lib.registry_io import (  # noqa: E402
    load_registry,
    sanitize_input,
    save_registry,
    write_validation_result,
)
from lib.safe_url import is_safe_endpoint_url, is_safe_http_url  # noqa: E402

logger = logging.getLogger(__name__)


def _fail_registration(output_path: str, errors: list[str], issue_number: str) -> None:
    debug_id = generate_debug_id()
    err_str = "; ".join(errors)
    logger.info(
        json.dumps(
            {
                "event": "registration.validation_failed",
                "debug_id": debug_id,
                "errors": err_str,
                "issue_number": issue_number,
            }
        )
    )
    write_validation_result(output_path, errors=err_str, debug_id=debug_id)


# GitHub Issue Form body uses ### <label> as section headers; labels from register_agent.yml
_HEADER_TO_FIELD = {
    "Agent name (slug-friendly)": "name",
    "Description": "description",
    "Manifest URL": "manifest_url",
    "HTTP Endpoint": "http_endpoint",
    "WebSocket Endpoint (optional)": "websocket_endpoint",
    "Skills": "skills",
    "Built with (framework)": "built_with",
    "Category": "category",
    "Tags": "tags",
    "Repository URL (optional)": "repository_url",
    "Documentation URL (optional)": "documentation_url",
    "Confirmation": "confirm",
}


def parse_issue_body(body: str) -> dict[str, str]:
    if not body or not body.strip():
        return {}

    fields: dict[str, str] = {}

    # Extract sections matching "### Header Name\n...content..."
    # We split by '### ' and then safely process chunks.
    parts = re.split(r"(?im)^###\s+", body)
    for part in parts:
        part = part.strip()
        if not part:
            continue

        first_line, _, rest = part.partition("\n")
        header = first_line.strip()
        value = rest.strip()

        field = _HEADER_TO_FIELD.get(header)
        if field:
            max_len = 2000 if field == "description" else 500
            fields[field] = sanitize_input(value, max_length=max_len)

    return fields


def _append_unsafe_endpoint_errors(
    errors: list[str], http_endpoint: str, websocket_endpoint: str
) -> None:
    """Reject endpoints that would send registry consumers to private hosts.

    Manifest fetch already uses ``is_safe_http_url``; HTTP/WS fields were only
    equality-checked against the manifest and then stored. The web register
    path allowlists these URLs; IssueOps must match that bar.

    DNS is checked at validation time (time-of-check); TTL changes before a
    consumer connects are the same TOCTOU class as manifest fetch.
    """
    if not is_safe_endpoint_url(http_endpoint):
        errors.append(f"Blocked URL (private/metadata): {http_endpoint}")
    if websocket_endpoint and not is_safe_endpoint_url(websocket_endpoint):
        errors.append(f"Blocked URL (private/metadata): {websocket_endpoint}")


def fetch_manifest(url: str, timeout: float = 15.0) -> Manifest:
    if not is_safe_http_url(url):
        raise ValueError(f"Blocked URL (private/metadata): {url}")
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ValueError(f"Manifest response is not JSON: {url}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Manifest JSON must be an object: {url}")
    try:
        return validate_signed_manifest_response(data, verify_signature=True)
    except SignatureVerificationError as exc:
        raise ManifestValidationError(str(exc), field="signature") from exc


def run(
    body: str,
    issue_number: str,
    author: str,
    output_path: str,
    registry_path: str = "registry.json",
) -> None:
    errors: list[str] = []

    parsed = parse_issue_body(body)
    name = (parsed.get("name") or "").strip()
    manifest_url = (parsed.get("manifest_url") or "").strip()
    http_endpoint = (parsed.get("http_endpoint") or "").strip()
    websocket_endpoint = (parsed.get("websocket_endpoint") or "").strip()
    skills_str = (parsed.get("skills") or "").strip()
    built_with = (parsed.get("built_with") or "").strip() or None
    category = (parsed.get("category") or "").strip() or None
    tags_str = (parsed.get("tags") or "").strip()
    repository_url = (parsed.get("repository_url") or "").strip() or None
    documentation_url = (parsed.get("documentation_url") or "").strip() or None

    if not name:
        errors.append("Missing required field: name")
    if not manifest_url:
        errors.append("Missing required field: manifest_url")
    if not http_endpoint:
        errors.append("Missing required field: http_endpoint")
    if not skills_str:
        errors.append("Missing required field: skills")

    if errors:
        _fail_registration(output_path, errors, issue_number)
        return

    skills = [s.strip() for s in skills_str.split(",") if s.strip()]

    # Expected agent URN: urn:asap:agent:<github_username>:<name>
    # Normalize author to lowercase for URN (GitHub usernames are case-insensitive)
    expected_id = f"urn:asap:agent:{author.lower()}:{name}"

    try:
        manifest = fetch_manifest(manifest_url)
    except ManifestValidationError as e:
        errors.append(
            f"Manifest failed schema validation. {e.message} "
            "Ensure it follows the ASAP Manifest format."
        )
        _fail_registration(output_path, errors, issue_number)
        return
    except ValidationError as e:
        error_count = e.error_count()
        errors.append(
            f"Manifest failed schema validation ({error_count} error(s)). "
            "Ensure it follows the ASAP Manifest format."
        )
        _fail_registration(output_path, errors, issue_number)
        return
    except ValueError as e:
        msg = str(e)
        if "Blocked URL" in msg or "private/metadata" in msg:
            errors.append(f"Blocked URL (private/metadata): {manifest_url}")
        else:
            errors.append(msg)
        _fail_registration(output_path, errors, issue_number)
        return
    except httpx.HTTPError:
        errors.append(f"Manifest URL unreachable: {manifest_url}")
        _fail_registration(output_path, errors, issue_number)
        return

    if manifest.id != expected_id:
        errors.append(f"Manifest id must be {expected_id!r}, got {manifest.id!r}")
    if manifest.name != name:
        errors.append(f"Manifest name must match issue name {name!r}, got {manifest.name!r}")

    manifest_skills = [s.id for s in manifest.capabilities.skills]
    for sk in skills:
        if sk not in manifest_skills:
            errors.append(f"Skill {sk!r} not declared in manifest (manifest has {manifest_skills})")

    if manifest.endpoints.asap != http_endpoint:
        errors.append(
            f"HTTP endpoint must match manifest (manifest has {manifest.endpoints.asap!r})"
        )
    if websocket_endpoint and manifest.endpoints.events != websocket_endpoint:
        errors.append(
            f"WebSocket endpoint must match manifest (manifest has {manifest.endpoints.events!r})"
        )

    if errors:
        _fail_registration(output_path, errors, issue_number)
        return

    _append_unsafe_endpoint_errors(errors, http_endpoint, websocket_endpoint)
    if errors:
        _fail_registration(output_path, errors, issue_number)
        return

    # Uniqueness: id must not already exist in registry
    agents = load_registry(registry_path)
    existing_ids = {a.get("id") for a in agents if isinstance(a, dict)}
    if manifest.id in existing_ids:
        errors.append(f"Agent id {manifest.id!r} is already registered")
        _fail_registration(output_path, errors, issue_number)
        return

    endpoints = {
        "http": http_endpoint,
        "manifest": manifest_url,
    }
    if websocket_endpoint:
        endpoints["ws"] = websocket_endpoint

    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    try:
        entry = generate_registry_entry(
            manifest,
            endpoints,
            repository_url=repository_url,
            documentation_url=documentation_url,
            built_with=built_with,
            category=category,
            tags=tags,
        ).model_copy(update=derive_registry_hardware_fields(manifest))
    except ValidationError as e:
        error_count = e.error_count()
        errors.append(
            f"Registry entry validation failed ({error_count} error(s)). "
            "Check manifest and endpoint format."
        )
        _fail_registration(output_path, errors, issue_number)
        return

    agents.append(entry.model_dump(mode="json"))
    save_registry(registry_path, agents)
    write_validation_result(output_path, valid=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process agent registration from issue body")
    parser.add_argument("--body", required=True, help="Issue body (markdown)")
    parser.add_argument("--issue-number", required=True, help="Issue number (for logging)")
    parser.add_argument("--author", required=True, help="GitHub username (issue author)")
    parser.add_argument("--output", required=True, help="Path to write result.json")
    parser.add_argument(
        "--registry",
        default="registry.json",
        help="Path to registry.json (default: registry.json)",
    )
    args = parser.parse_args()
    try:
        run(
            body=args.body,
            issue_number=args.issue_number,
            author=args.author,
            output_path=args.output,
            registry_path=args.registry,
        )
    except Exception as err:
        debug_id = generate_debug_id()
        logger.info(
            json.dumps(
                {
                    "event": "registration.unexpected_error",
                    "debug_id": debug_id,
                    "issue_number": args.issue_number,
                }
            )
        )
        logger.exception("Unexpected error processing registration: %s", err)
        try:
            write_validation_result(
                args.output, errors="Internal processing error", debug_id=debug_id
            )
        except OSError:
            logger.exception("Failed to write error output")
        sys.exit(1)


if __name__ == "__main__":
    main()
