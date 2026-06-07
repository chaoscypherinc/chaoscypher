# Fixture for cc-036-internal-settings-import.
# Outside Core, get_settings must come from chaoscypher_core.app_config,
# not from the internal chaoscypher_core.settings module.

# ruleid: cc-036-internal-settings-import
from chaoscypher_core.settings import get_settings  # noqa: F401

# ok: cc-036-internal-settings-import
from chaoscypher_core.app_config import get_settings as _ok_getter  # noqa: F401
