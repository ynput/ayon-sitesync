"""Adds state of published representations for syncing.

Each published representation might be marked to be synced to multiple
sites. On some might be present (by default 'studio'), on some needs to
be synchronized.

"""
from collections import defaultdict

import pyblish.api

from ayon_core.addon import AYONAddon
from ayon_api import get_representations

from ayon_sitesync.utils import SiteSyncStatus


class IntegrateSiteSync(pyblish.api.InstancePlugin):
    """Adds state of published representations for syncing."""

    order = pyblish.api.IntegratorOrder + 0.2
    label = "Integrate Site Sync state"

    def process(self, instance):
        published_representations = instance.data.get(
            "published_representations")
        if not published_representations:
            self.log.debug("Instance does not have published representations")
            return

        context = instance.context
        project_name = context.data["projectEntity"]["name"]
        addons_manager = context.data["ayonAddonsManager"]
        sitesync_addon = addons_manager.get_enabled_addon("sitesync")
        if sitesync_addon is None:
            return

        published_sites = sitesync_addon.compute_resource_sync_sites(
            project_name=project_name
        )
        for repre_id, inst in published_representations.items():
            for site_info in published_sites:
                sitesync_addon.add_site(
                    project_name,
                    repre_id,
                    site_info["name"],
                    status=site_info["status"]
                )

        hero_version_entity = instance.data.get("heroVersionEntity")
        self.log.info(f"hero_version_entity::{hero_version_entity}")
        if not hero_version_entity:
            return

        self._reset_hero_representations(
            project_name,
            sitesync_addon,
            hero_version_entity,
            published_sites
        )

    def _reset_hero_representations(
        self,
        project_name: str,
        sitesync_addon: AYONAddon,
        hero_version_entity,
        sites: list[dict]
    ) -> None:
        """Hero representations must be refreshed for all sites

        Re sync of all downloaded hero version is necessary to update locally
        cached instances.
        """
        hero_repres = list(get_representations(
            project_name, version_ids=[hero_version_entity["id"]]
        ))

        hero_repre_ids = [repre["id"] for repre in hero_repres]
        repres_sites_state = (
            sitesync_addon.get_representations_sites_sync_state(
                project_name, hero_repre_ids
            )
        )
        repre_sites_by_id = defaultdict(list)
        for repre_site in repres_sites_state:
            repre_sites_by_id[repre_site["representationId"]].append(repre_site)

        publish_site_status = {site["name"]: site["status"] for site in sites}
        for hero_repre in hero_repres:
            # completely new
            repre_on_sites = repre_sites_by_id.get(hero_repre["id"])
            if not repre_on_sites:
                for site_name, site_status in publish_site_status.items():
                    sitesync_addon.add_site(
                        project_name,
                        hero_repre["id"],
                        site_name,
                        status=site_status,
                    )
            else:
                # update existing synced
                for repre_on_site in repre_on_sites:
                    site_status = (
                        publish_site_status.get(
                            repre_on_site["siteName"],SiteSyncStatus.QUEUED
                        )
                    )
                    sitesync_addon.add_site(
                        project_name,
                        repre_on_site["representationId"],
                        repre_on_site["siteName"],
                        status=site_status,
                        force=True,
                    )
