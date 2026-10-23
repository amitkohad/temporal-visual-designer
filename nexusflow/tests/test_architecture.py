"""Keep foundation contracts independent of application composition and test fakes."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1]
SOURCES = sorted((PACKAGE / "contracts").glob("*.py")) + sorted((PACKAGE / "apps").glob("*.py"))


@pytest.mark.parametrize("source", SOURCES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_import_boundaries(source):
    forbidden = {"tests"}
    if source.parent.name == "contracts":
        forbidden.update({"apps", "modules"})
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import climbing to the package root can address a forbidden layer.
                if node.level >= 2:
                    modules = ["nexusflow." + (node.module or "")]
                    if node.module is None:
                        modules = ["nexusflow." + alias.name for alias in node.names]
            else:
                modules = [node.module or ""]
        for module in modules:
            parts = module.split(".")
            if len(parts) > 1 and parts[0] == "nexusflow":
                assert parts[1] not in forbidden, (
                    f"{source.name}:{node.lineno}: forbidden import {module}"
                )
