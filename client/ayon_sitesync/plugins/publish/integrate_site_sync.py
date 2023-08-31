"""Adds state of published representations for syncing.

Each published representation might be marked to be synced to multiple
sites. On some might be present (by default 'studio'), on some needs to
be synchronized.

It always state at least for 'studio' (even if SiteSync is disabled, in case
of enabling, every artists can start synchronizing without any updates to DB).

This plugins depends on logic in `integrate`. It is meant to be backward
compatible. Approach should be refactored after v3 is gone.
"""
import os
import pyblish.api

from ayon_sitesync.utils import SiteSyncStatus


class IntegrateSiteSync(pyblish.api.InstancePlugin):
    """Adds state of published representations for syncing."""

    order = pyblish.api.IntegratorOrder + 0.2
    label = "Integrate Site Sync state"

    def process(self, instance):
        if not os.environ.get("USE_AYON_SERVER"):
            return

        project_name = instance.context.data["projectEntity"]["name"]
        modules_by_name = instance.context.data["openPypeModules"]
        sync_server_module = modules_by_name["sync_server"]

        for repre_id, inst in instance.data["published_representations"].items():  # noqa
            new_site_files_status = {}
            self.log.info("repre_id {}".format(repre_id))
            for repre_file in inst["representation"]["files"]:
                for site_info in repre_file["sites"]:
                    site_name = site_info["name"]
                    status = SiteSyncStatus.OK
                    self.log.info(f"site_info::{site_info}")
                    if not site_info.get("created_dt"):
                        status = SiteSyncStatus.QUEUED
                    new_site_files_status[site_name] = status
                break  # after integrate status is same for all files

            for site_name, status in new_site_files_status.items():
                self.log.info(f"status::{status}")
                sync_server_module.add_site(project_name, repre_id, site_name,
                                            status=status)
