"""Export the versioned intake manifest schema for external Python authors."""

import json
from pathlib import Path

from nexusflow.modules.activity_catalog.types import ActivityManifest


def main() -> None:
    target = Path("artifacts/schemas/0.1/activity-bundle-manifest.schema.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(ActivityManifest.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
