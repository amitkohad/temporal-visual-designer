"""Portable logical digest excludes ZIP headers and its own declared digest."""

import hashlib
import json

from nexusflow.modules.activity_catalog.types import ActivityManifest


def bundle_digest(manifest: ActivityManifest) -> str:
    value = manifest.model_dump(mode="json")
    value["descriptor"]["activity_ref"].pop("artifact_digest")
    value["files"].sort(key=lambda item: item["path"])
    for dependency in value["runtime"]["dependencies"]:
        dependency["files"].sort()
    value["runtime"]["dependencies"].sort(key=lambda item: item["name"])
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
