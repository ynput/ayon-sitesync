from __future__ import annotations
import os
from typing import Any, Type
from nxtools import logging

from openpype.addons import BaseServerAddon

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

    def get_local_client_info(
            self,
            base_url: str | None = None,
    ) -> dict[str, Any] | None:
        """Returns information on local copy of the client code."""
        logging.info("private:: {}".format(self.get_private_dir()))
        if (pdir := self.get_private_dir()) is None:
            return None
        if base_url is None:
            base_url = ""
        client_zip = "{}_{}.zip".format(self.name, self.version)
        logging.info("client_zip:: {}".format(client_zip))
        local_path = os.path.join(pdir, client_zip)
        logging.info("local_path:: {}".format(local_path))
        if not os.path.exists(local_path):
            return None
        return {
            "type": "http",
            "path": f"{base_url}/addons/{self.name}/{self.version}/private/{client_zip}",  # noqa
        }

