"""Deterministic snapshot encoding and structural revision comparison."""

import hashlib
import json
from typing import Any

from nexusflow.modules.project_versioning.types import Change, ProjectContent


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


def compare_json(before: str, after: str, include_layout: bool = False) -> tuple[Change, ...]:
    left, right = json.loads(before), json.loads(after)
    if not include_layout:
        left.pop("layout", None)
        right.pop("layout", None)
    changes: list[Change] = []

    def visit(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in a:
                    changes.append(Change(child, None, canonical(b[key])))
                elif key not in b:
                    changes.append(Change(child, canonical(a[key]), None))
                else:
                    visit(a[key], b[key], child)
        elif canonical(a) != canonical(b):
            changes.append(Change(path, canonical(a), canonical(b)))

    visit(left, right, "")
    return tuple(changes)
