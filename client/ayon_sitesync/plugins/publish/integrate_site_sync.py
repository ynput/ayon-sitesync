"""Adds state of published representations for syncing.

Each published representation might be marked to be synced to multiple
sites. On some might be present (by default 'studio'), on some needs to
be synchronized.

"""
import pyblish.api


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

        sites = sitesync_addon.compute_resource_sync_sites(
            project_name=project_name
        )
        for repre_id, inst in published_representations.items():
            for site_info in sites:
                sitesync_addon.add_site(
                    project_name,
                    repre_id,
                    site_info["name"],
                    status=site_info["status"],
                    force=True
                )
