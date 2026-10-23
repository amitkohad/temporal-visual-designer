"""Export portable JSON Schema contracts for React and other consumers."""

import argparse
import json
from pathlib import Path

from nexusflow.contracts.models import CONTRACTS


def export_schemas(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, model in sorted(CONTRACTS.items()):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:nexus-flow-designer:contracts:0.1:{name}"
        (destination / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/schemas/0.1"))
    export_schemas(parser.parse_args().output)


if __name__ == "__main__":
    main()
