from typing import Any, Type

from openpype.addons import BaseServerAddon

from .settings.settings import SiteSyncSettings


class SiteSyncAddon(BaseServerAddon):
    name = "sitesync"
    title = "Site Sync Addon"
    version = "1.0.0"

    settings_model: Type[SiteSyncSettings] = SiteSyncSettings

    frontend_scopes: dict[str, Any] = {"project": {}}

