"""Export server form rules and the JSON-only preview contract."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from nexusflow.modules.designer_forms.form_types import FormView, ValidationRule, VisibilityRule


def main() -> None:
    target = Path("artifacts/schemas/0.1")
    target.mkdir(parents=True, exist_ok=True)
    schemas = {
        "form-visibility-rule": VisibilityRule.model_json_schema(),
        "form-validation-rule": ValidationRule.model_json_schema(),
        "form-view": TypeAdapter(FormView).json_schema(),
    }
    for name, schema in schemas.items():
        (target / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
