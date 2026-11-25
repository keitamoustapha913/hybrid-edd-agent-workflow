#!/usr/bin/env python3
"""Deterministic validation of proposed Codex specialist -> handoff results.

This is a narrow safety gate, not verification of software correctness.
The Coordinator must independently validate execution logs, approvals and source
revision before using a READY result to dispatch the next specialist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATUS_BY_ROLE = {
    "DISCOVERY": {"READY_FOR_REVIEW", "BLOCKED"},
    "PROTOTYPE": {"READY_FOR_REVIEW", "BLOCKED"},
    "SPECIFICATION": {"READY_FOR_REVIEW", "BLOCKED"},
    "MVP_DESIGN": {"READY_FOR_REVIEW", "BLOCKED"},
    "UIUX": {"DESIGN_READY", "INSPECTION_RECORDED", "UX_VERIFIED", "UX_FAILED", "INCONCLUSIVE", "BLOCKED"},
    "REQUIREMENTS": {"READY", "BLOCKED"},
    "RED": {"RED_CONFIRMED", "ALREADY_GREEN", "BLOCKED"},
    "GREEN": {"GREEN_CONFIRMED", "BLOCKED"},
    "REFACTOR": {"REFACTOR_COMPLETE", "NO_REFACTOR_NEEDED", "BLOCKED"},
    "VERIFY": {"VERIFIED", "FAILED", "INCONCLUSIVE"},
    "PROVE": {"PASS", "FAIL", "INCONCLUSIVE"},
}


def check_schema(value: Any, schema: dict[str, Any], where: str = "$") -> list[str]:
    """Validate only the JSON Schema constructs used by the bundled contracts."""
    errors: list[str] = []
    kind = schema.get("type")
    kinds = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
    }
    if kind not in kinds or not kinds[kind](value):
        return [f"{where}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: unsupported value")
    if kind == "object":
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{where}: missing {field}")
        if schema.get("additionalProperties") is False:
            for field in value:
                if field not in properties:
                    errors.append(f"{where}: unexpected {field}")
        for field, item in value.items():
            if field in properties:
                errors.extend(check_schema(item, properties[field], f"{where}.{field}"))
    elif kind == "array":
        for index, item in enumerate(value):
            errors.extend(check_schema(item, schema["items"], f"{where}[{index}]"))
    return errors


def checked_relative_path(root: Path, raw: str, allowed_prefixes: list[str]) -> tuple[Path | None, str | None]:
    if not raw or "\\" in raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        return None, "path must be a nonempty repository-relative POSIX path"
    rel = PurePosixPath(raw)
    if any(part in (".", "..") for part in raw.split("/")) or rel.as_posix() != raw:
        return None, "path has noncanonical segments"
    if not any(rel == PurePosixPath(prefix) or PurePosixPath(prefix) in rel.parents for prefix in allowed_prefixes):
        return None, "path outside authorized roots"
    try:
        resolved = (root / raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "path does not exist"
    if root not in resolved.parents and resolved != root:
        return None, "path resolves outside repository (symlink escape)"
    return resolved, None


def file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(
    root: Path,
    producer: dict[str, Any],
    handoff: dict[str, Any],
    allowed_prefixes: list[str],
) -> dict[str, Any]:
    """Return a deterministic decision; no AI judgement or authority transfer."""
    root = root.resolve(strict=True)
    producer_schema = json.loads((PACKAGE_ROOT / "schemas/specialist-result.schema.json").read_text())
    handoff_schema = json.loads((PACKAGE_ROOT / "schemas/handoff.schema.json").read_text())
    errors = [
        *check_schema(producer, producer_schema, "producer"),
        *check_schema(handoff, handoff_schema, "handoff"),
    ]
    if errors:
        return {"valid": False, "errors": errors, "checked_files": {}}

    if producer["role"] not in STATUS_BY_ROLE:
        errors.append("unknown specialist role")
    elif producer["status"] not in STATUS_BY_ROLE[producer["role"]]:
        errors.append("producer status invalid for specialist role")
    if producer["role"] == "UIUX":
        # One role, two different invocations: a design status must never be
        # relabelled as final visual inspection (or vice versa) by handoff text.
        report = producer["report_path"]
        status = producer["status"]
        if report.endswith("/ux/design-review.md"):
            allowed = {"DESIGN_READY", "BLOCKED"}
        elif report.endswith("/ux/inspection.md"):
            allowed = {"UX_VERIFIED", "UX_FAILED", "INCONCLUSIVE", "INSPECTION_RECORDED", "BLOCKED"}
        else:
            allowed = set()
        if status not in allowed:
            errors.append("UIUX report path and mode-specific status do not agree")
        if not producer["mvp_id"] or not producer["criterion_ids"]:
            errors.append("UIUX handoff requires an MVP ID and UX criterion IDs")
    if not producer["run_id"].strip() or not producer["role"].strip():
        errors.append("empty producer identity")
    if (producer["role"], producer["run_id"], producer["mvp_id"], producer["status"]) != (
        handoff["producer_role"], handoff["producer_run_id"],
        handoff["mvp_id"], handoff["reported_status"]
    ):
        errors.append("handoff identity or status does not match producer result")
    if set(producer["criterion_ids"]) != set(handoff["criterion_ids"]):
        errors.append("acceptance criterion IDs differ from producer result")
    if handoff["recommendation"] != "READY_FOR_VALIDATION":
        errors.append("handoff requires producer correction or human input")
    if any(issue["severity"] == "ERROR" for issue in handoff["issues"]):
        errors.append("handoff contains blocking issues")

    observed_source_paths = set(producer["output_paths"]) | {producer["report_path"]}
    normalized_source_paths = set(handoff["output_paths"]) | {handoff["producer_report_path"]}
    if normalized_source_paths != observed_source_paths:
        if not any(i["code"] == "PATH_REMAP" for i in handoff["issues"]):
            errors.append("changed producer paths require a PATH_REMAP issue")

    checked: dict[str, str | None] = {}
    for raw in sorted(normalized_source_paths | set(handoff["evidence_paths"])):
        path, issue = checked_relative_path(root, raw, allowed_prefixes)
        if issue is not None:
            errors.append(f"{raw!r}: {issue}")
        else:
            checked[raw] = file_digest(path)
    if not handoff["producer_report_path"]:
        errors.append("producer report path missing")
    return {"valid": not errors, "errors": errors, "checked_files": checked}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Approved project root")
    parser.add_argument("--producer-result", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--allowed-prefix", action="append", required=True,
                        help="Approved repository-relative root (repeat for each)")
    args = parser.parse_args()
    try:
        producer = json.loads(args.producer_result.read_text(encoding="utf-8"))
        handoff = json.loads(args.handoff.read_text(encoding="utf-8"))
        prefixes = []
        for value in args.allowed_prefix:
            p = PurePosixPath(value)
            if not value or value.startswith("/") or "\\" in value or ".." in value.split("/") or p.as_posix() != value:
                raise ValueError("invalid --allowed-prefix")
            prefixes.append(value)
        result = validate(args.root, producer, handoff, prefixes)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"valid": False, "errors": [str(error)], "checked_files": {}}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
