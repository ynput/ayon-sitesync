from typing import Any, Type

from openpype.addons import BaseServerAddon

from .settings.settings import SiteSyncSettings
from .version import __version__


class SiteSyncAddon(BaseServerAddon):
    name = "sitesync"
    title = "Site Sync Addon"
    version = __version__

    settings_model: Type[SiteSyncSettings] = SiteSyncSettings

    frontend_scopes: dict[str, Any] = {"project": {}}

