"""Local contract conformance entry point; never executes supplied workflows."""

import argparse
import json
from pathlib import Path

from nexusflow.contracts import CONTRACTS, validate_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", choices=sorted(CONTRACTS))
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        payload = args.file.read_bytes()
    except OSError:
        print(json.dumps({"valid": False, "issues": [{"path": "", "code": "unreadable_file"}]}))
        return 2
    result = validate_contract(args.contract, payload)
    print(
        json.dumps(
            {
                "valid": result.value is not None,
                "issues": [
                    {"path": issue.path, "code": issue.code, "message": issue.message}
                    for issue in result.issues
                ],
            }
        )
    )
    return 0 if result.value is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
