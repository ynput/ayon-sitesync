from __future__ import annotations
from typing import Any, Type
from nxtools import logging

from ayon_server.addons import BaseServerAddon

from .settings.settings import SiteSyncSettings
from .version import __version__

from .endpoints import endpoints


class SiteSync(BaseServerAddon):
    name = "sitesync"
    title = "Site Sync"
    version = __version__

    settings_model: Type[SiteSyncSettings] = SiteSyncSettings

    frontend_scopes: dict[str, Any] = {"project": {}}

    def initialize(self) -> None:
        logging.info("Init SiteSync")

        self.add_endpoint(
            "/projects/{project_name}/sitesync/params",
            endpoints.get_site_sync_params,
            method="GET",
        )

        self.add_endpoint(
            "/projects/{project_name}/sitesync/state",
            endpoints.get_site_sync_state,
            method="GET",
        )

        self.add_endpoint(
            "/projects/{project_name}/sitesync/state/{representation_id}/{site_name}",  # noqa
            endpoints.set_site_sync_representation_state,
            method="POST",
        )

        self.add_endpoint(
            "/projects/{project_name}/sitesync/state/{representation_id}/{site_name}",  # noqa
            endpoints.remove_site_sync_representation_state,
            method="DELETE",
        )
        logging.info("added endpoints")
