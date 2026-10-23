"""Export the project snapshot schema without coupling shared contracts to features."""

import json
from pathlib import Path

from nexusflow.modules.project_versioning.types import ProjectContent


def main() -> None:
    target = Path("artifacts/schemas/0.1/project-content.schema.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(ProjectContent.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
