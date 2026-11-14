"""Same-origin pilot UI mounting for the local-demo launcher.

Serves a built React workspace from the API process so browser clients keep
same-origin `/api` calls. This is a trusted-workstation composition aid, not a
production reverse proxy or Entra host.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class PilotUiError(Exception):
    """Safe pilot UI status suitable for printing without provider details."""


def resolve_ui_directory(explicit: Path | None = None) -> Path:
    """Return the built UI directory; default is ``web/dist`` under the CWD."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    return (Path.cwd() / "web" / "dist").resolve()


def require_ui_directory(directory: Path) -> Path:
    root = directory.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise PilotUiError(
            "Pilot UI build is missing at "
            f"{root}. From the repository root run: cd web && npm ci && npm run build"
        )
    return root


def attach_pilot_ui(app: FastAPI, directory: Path) -> Path:
    """Mount built static assets and SPA index after API routes are registered."""
    root = require_ui_directory(directory)
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="pilot_assets")

    @app.get("/")
    async def pilot_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    async def pilot_spa(full_path: str) -> FileResponse:
        # API and /health remain registered first and win for those paths.
        if full_path:
            candidate = (root / full_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return FileResponse(index)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)

    return root
