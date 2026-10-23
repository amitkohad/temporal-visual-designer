"""Export the versioned variable-selection binding schema."""

import json
from pathlib import Path

from nexusflow.modules.designer_forms.types import Binding


def main() -> None:
    target = Path("artifacts/schemas/0.1/variable-binding.schema.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(Binding.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
