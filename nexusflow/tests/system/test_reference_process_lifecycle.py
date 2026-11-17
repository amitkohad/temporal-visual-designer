"""SC-01 Dev lifecycle: template → publish → activate → approve → completed run."""

import pytest

from nexusflow.tests.system.helpers import (
    STARTERS,
    _cli,
    _initialized,
    _ports,
    _serve,
    complete_starter_run,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("template", sorted(STARTERS))
def test_reference_starter_completes_approvals_on_local_temporal(tmp_path, template):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        complete_starter_run(
            client,
            organization=organization,
            template=template,
            headers=headers,
            start_key=f"{template}-system-start",
        )
    assert headers["Authorization"].split(" ")[1] not in log.read_text("utf-8", errors="replace")
