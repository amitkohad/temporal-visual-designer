"""Deterministic snapshot encoding and structural revision comparison."""

import hashlib
import json
from typing import Any

from nexusflow.modules.project_versioning.types import Change, ProjectContent, VersioningError


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def executable(content: ProjectContent) -> dict[str, Any]:
    value = content.model_dump(mode="json")
    value.pop("layout")
    value.pop("tests")
    for collection, key in (
        ("workflows", "workflow_id"),
        ("forms", "form_id"),
        ("connections", "connection_id"),
    ):
        value[collection].sort(key=lambda item: item[key])
    value["activity_refs"].sort(
        key=lambda item: (item["activity_id"], item["version"], item["artifact_digest"])
    )
    return value


def compare_json(
    before: str,
    after: str,
    include_layout: bool = False,
    *,
    max_changes: int | None = None,
    max_bytes: int | None = None,
) -> tuple[Change, ...]:
    left, right = json.loads(before), json.loads(after)
    if not include_layout:
        left.pop("layout", None)
        right.pop("layout", None)
    changes: list[Change] = []

    encoded_bytes = 0

    def append(change: Change) -> None:
        nonlocal encoded_bytes
        if max_changes is not None and len(changes) >= max_changes:
            raise VersioningError("comparison_limit", "Comparison exceeds response limits")
        if max_bytes is not None:
            encoded_bytes += len(
                canonical(
                    {
                        "path": change.path,
                        "before_json": change.before_json,
                        "after_json": change.after_json,
                    }
                ).encode("utf-8")
            )
            if encoded_bytes > max_bytes:
                raise VersioningError("comparison_limit", "Comparison exceeds response limits")
        changes.append(change)

    def visit(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in a:
                    append(Change(child, None, canonical(b[key])))
                elif key not in b:
                    append(Change(child, canonical(a[key]), None))
                else:
                    visit(a[key], b[key], child)
        elif canonical(a) != canonical(b):
            append(Change(path, canonical(a), canonical(b)))

    visit(left, right, "")
    return tuple(changes)
