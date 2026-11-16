"""Local webhook enablement profile; never starts Temporal or HTTP."""

from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_webhooks import enable_webhooks, load_webhook_profile


def test_enable_webhooks_is_idempotent_and_dev_only(tmp_path):
    initialize(tmp_path)
    first = enable_webhooks(tmp_path)
    second = enable_webhooks(tmp_path)
    assert first == second
    settings = __import__(
        "nexusflow.apps.local_bootstrap", fromlist=["load_settings"]
    ).load_settings(tmp_path)
    loaded = load_webhook_profile(tmp_path, settings)
    assert loaded == first
    assert loaded.scope.environment_id == "dev"
    assert (tmp_path / "webhooks.db").is_file()
    assert (tmp_path / "webhooks.json").is_file()
