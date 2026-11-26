"""Loader actions to download, upload or remove representations locally."""
from __future__ import annotations

from typing import Any, Optional

from ayon_core.lib import NestedCacheItem
from ayon_core.lib.icon_definitions import MaterialSymbolsIcon
from ayon_core.pipeline.actions import (
    LoaderActionItem,
    LoaderActionPlugin,
    LoaderActionResult,
    LoaderActionSelection,
)

DOWNLOAD_ACTION = "download"
UPLOAD_ACTION = "upload"
REMOVE_ACTION = "remove"

# Server returns sync state of at most 50 representations by default
SYNC_STATE_CHUNK_SIZE = 50


class SiteSyncActionPlugin(LoaderActionPlugin):
    """Mark representations to be synced to the active or remote site.

    The addon registers the plugin only if it is enabled. Site Sync can
    still be disabled per project.

    """
    identifier = "sitesync.actions"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._addon = context.get_addons_manager().get("sitesync")
        # Active and remote site by project name, 'None' if disabled
        self._sites_cache = NestedCacheItem(levels=1, lifetime=60)
        # Local and remote sync progress by project name and repre id
        self._repre_status_cache = NestedCacheItem(levels=2, lifetime=20)

    def get_action_items(
        self, selection: LoaderActionSelection
    ) -> list[LoaderActionItem]:
        # Called on each right-click, so do not query the server
        #   unless Site Sync is enabled for the project
        if not selection.representations_selected():
            return []

        sites = self._get_project_sites(selection)
        if sites is None:
            return []

        repre_ids = selection.selected_ids
        status_by_repre_id = self._get_repres_sync_status(
            selection.project_name, repre_ids, *sites
        )
        download_ids = set()
        local_ids = set()
        for repre_id in repre_ids:
            local_status, remote_status = status_by_repre_id[repre_id]
            if local_status:
                local_ids.add(repre_id)
            if remote_status:
                download_ids.add(repre_id)

        output = []
        if download_ids:
            output.append(self._create_action_item(
                "Download", "download", DOWNLOAD_ACTION, download_ids
            ))
        if local_ids:
            output.append(self._create_action_item(
                "Upload", "upload", UPLOAD_ACTION, local_ids
            ))
            output.append(self._create_action_item(
                "Remove from local", "delete", REMOVE_ACTION, local_ids
            ))
        return output

    def execute_action(
        self,
        selection: LoaderActionSelection,
        data: dict[str, Any],
        form_values: dict[str, Any],
    ) -> Optional[LoaderActionResult]:
        project_name = selection.project_name
        sites = self._get_project_sites(selection)
        if sites is None:
            return LoaderActionResult(
                message="Site Sync is not enabled for the project.",
                success=False,
            )
        active_site, remote_site = sites

        action = data["action"]
        repre_ids = data["representation_ids"]
        try:
            if action == REMOVE_ACTION:
                changed_ids = self._addon.remove_site_from_representations(
                    project_name,
                    repre_ids,
                    active_site,
                    remove_local_files=True,
                )
                message = (
                    f"Removed {len(changed_ids)} representation(s)"
                    f" from '{active_site}'."
                )
            else:
                site_name = active_site
                if action == UPLOAD_ACTION:
                    site_name = remote_site
                changed_ids = self._addon.add_site_to_representations(
                    project_name, repre_ids, site_name, add_referenced=True
                )
                verb = "download" if action == DOWNLOAD_ACTION else "upload"
                message = (
                    f"Marked {len(changed_ids)} representation(s)"
                    f" for {verb} to '{site_name}'."
                )
        finally:
            # Representations referenced by workfiles change too
            self._repre_status_cache.clear_key(project_name)
        return LoaderActionResult(message=message)

    def _create_action_item(
        self,
        label: str,
        icon_name: str,
        action: str,
        repre_ids: set[str],
    ) -> LoaderActionItem:
        return LoaderActionItem(
            label=label,
            order=1,
            icon=MaterialSymbolsIcon(icon_name, color="#999999"),
            data={
                "action": action,
                "representation_ids": list(repre_ids),
            },
        )

    def _get_project_sites(
        self, selection: LoaderActionSelection
    ) -> Optional[tuple[str, str]]:
        """Active and remote site of the selected project.

        Checks if Site Sync is enabled for the project from project settings
        first. They're cached by ayon-core and usually already received by
        other action plugins.

        Returns:
            Optional[tuple[str, str]]: Active and remote site names, or
                'None' if Site Sync is not enabled for the project.

        """
        project_name = selection.project_name
        cache = self._sites_cache[project_name]
        if not cache.is_valid:
            sites = None
            project_settings = selection.get_project_settings()
            sitesync_settings = project_settings.get(self._addon.name) or {}
            if sitesync_settings.get("enabled"):
                sites = (
                    self._addon.get_active_site(project_name),
                    self._addon.get_remote_site(project_name),
                )
            cache.update_data(sites)
        return cache.get_data()

    def _get_repres_sync_status(
        self,
        project_name: str,
        repre_ids: set[str],
        active_site: str,
        remote_site: str,
    ) -> dict[str, tuple[float, float]]:
        """Local and remote sync progress of representations."""
        project_cache = self._repre_status_cache[project_name]
        missing_ids = [
            repre_id
            for repre_id in repre_ids
            if not project_cache[repre_id].is_valid
        ]
        for idx in range(0, len(missing_ids), SYNC_STATE_CHUNK_SIZE):
            chunk_ids = missing_ids[idx:idx + SYNC_STATE_CHUNK_SIZE]
            status_by_repre_id = self._addon.get_representations_sync_state(
                project_name, chunk_ids, active_site, remote_site
            )
            for repre_id in chunk_ids:
                project_cache[repre_id].update_data(
                    status_by_repre_id.get(repre_id, (0, 0))
                )
        return {
            repre_id: project_cache[repre_id].get_data()
            for repre_id in repre_ids
        }
