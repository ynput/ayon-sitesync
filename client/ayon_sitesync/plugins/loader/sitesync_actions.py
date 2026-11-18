"""Loader actions to download, upload or remove representations locally."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import ayon_api

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

    Sites are changed in a background thread, because it takes a few
    server requests per representation and the UI would wait for them.

    """
    identifier = "sitesync.actions"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._addon = context.get_addons_manager().get("sitesync")
        # Active and remote site by project name, 'None' if disabled
        self._sites_cache = NestedCacheItem(levels=1, lifetime=60)
        # Local and remote sync progress by project name and repre id
        self._repre_status_cache = NestedCacheItem(levels=2, lifetime=20)

        # Single worker to change sites in the order they were triggered
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="SiteSyncActions"
        )
        self._lock = threading.Lock()
        self._running_jobs = 0
        # Increased when a job starts or finishes, so sync status received
        #   meanwhile is not cached
        self._status_generation = 0

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
        repre_ids = set(data["representation_ids"])
        site_name = active_site
        if action == UPLOAD_ACTION:
            site_name = remote_site

        self._on_job_change(project_name, started=True)
        self._executor.submit(
            self._process_action, selection, action, repre_ids, site_name
        )

        count = len(repre_ids)
        if action == REMOVE_ACTION:
            message = f"Removing {count} representation(s) from '{site_name}'."
        else:
            verb = "download" if action == DOWNLOAD_ACTION else "upload"
            message = (
                f"Marking {count} representation(s)"
                f" for {verb} to '{site_name}'."
            )
        return LoaderActionResult(message=message)

    def _process_action(
        self,
        selection: LoaderActionSelection,
        action: str,
        repre_ids: set[str],
        site_name: str,
    ) -> None:
        """Change sites of representations, runs in a background thread."""
        project_name = selection.project_name
        try:
            if action == REMOVE_ACTION:
                for repre_id in repre_ids:
                    self._addon.remove_site(
                        project_name,
                        repre_id,
                        site_name,
                        remove_local_files=True,
                    )
                return

            repre_entities = selection.entities.get_representations(
                repre_ids
            )
            for repre_entity in repre_entities:
                self._addon.add_site(
                    project_name, repre_entity["id"], site_name, force=True
                )

            for repre_id in self._get_linked_repre_ids_to_sync(
                selection, repre_entities, site_name
            ):
                self.log.info(
                    f"Adding {site_name} to linked representation:"
                    f" {repre_id}"
                )
                self._addon.add_site(
                    project_name, repre_id, site_name, force=True
                )

        except Exception:
            self.log.error(
                f"Failed to {action} representations.", exc_info=True
            )

        finally:
            self._on_job_change(project_name, started=False)

    def _on_job_change(self, project_name: str, started: bool) -> None:
        # Linked representations change too, invalidate whole project
        with self._lock:
            self._running_jobs += 1 if started else -1
            self._status_generation += 1
            self._repre_status_cache.clear_key(project_name)

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
        output = {}
        missing_ids = set()
        with self._lock:
            generation = self._status_generation
            project_cache = self._repre_status_cache[project_name]
            for repre_id in repre_ids:
                cache = project_cache[repre_id]
                if cache.is_valid:
                    output[repre_id] = cache.get_data()
                else:
                    missing_ids.add(repre_id)

        if not missing_ids:
            return output

        status_by_repre_id = self._get_sync_state(
            project_name, missing_ids, active_site, remote_site
        )
        with self._lock:
            # Status may be outdated if a job ran meanwhile
            can_cache = (
                not self._running_jobs
                and generation == self._status_generation
            )
            project_cache = self._repre_status_cache[project_name]
            for repre_id in missing_ids:
                status = status_by_repre_id.get(repre_id, (0, 0))
                output[repre_id] = status
                if can_cache:
                    project_cache[repre_id].update_data(status)
        return output

    def _get_sync_state(
        self,
        project_name: str,
        repre_ids: set[str],
        active_site: str,
        remote_site: str,
    ) -> dict[str, tuple[float, float]]:
        """Query sync state in chunks, the server limits output size."""
        repre_ids = list(repre_ids)
        output = {}
        for idx in range(0, len(repre_ids), SYNC_STATE_CHUNK_SIZE):
            output.update(self._addon.get_representations_sync_state(
                project_name,
                repre_ids[idx:idx + SYNC_STATE_CHUNK_SIZE],
                active_site,
                remote_site,
            ))
        return output

    def _get_linked_repre_ids_to_sync(
        self,
        selection: LoaderActionSelection,
        repre_entities: list[dict[str, Any]],
        site_name: str,
    ) -> set[str]:
        """Representations referenced by workfiles, not on the site yet."""
        version_ids = self._get_workfile_version_ids(
            selection, repre_entities
        )
        if not version_ids:
            return set()

        project_name = selection.project_name
        linked_ids = self._get_linked_representation_ids(
            project_name, version_ids, "reference"
        )
        # Selected representations are synced already
        linked_ids -= {repre_entity["id"] for repre_entity in repre_entities}
        if not linked_ids:
            return set()

        # One request per chunk instead of one per representation
        status_by_repre_id = self._get_sync_state(
            project_name, linked_ids, site_name, site_name
        )
        return {
            repre_id
            for repre_id in linked_ids
            if status_by_repre_id.get(repre_id, (0, 0))[0] != 1
        }

    def _get_workfile_version_ids(
        self,
        selection: LoaderActionSelection,
        repre_entities: list[dict[str, Any]],
    ) -> set[str]:
        """Version ids of representations that are 'workfile' products."""
        versions = selection.entities.get_versions({
            repre_entity["versionId"] for repre_entity in repre_entities
        })
        products = selection.entities.get_products({
            version["productId"] for version in versions
        })
        workfile_product_ids = {
            product["id"]
            for product in products
            if product["productType"] == "workfile"
        }
        return {
            version["id"]
            for version in versions
            if version["productId"] in workfile_product_ids
        }

    def _get_linked_representation_ids(
        self,
        project_name: str,
        version_ids: set[str],
        link_type: str,
    ) -> set[str]:
        """Ids of representations of versions linked as inputs."""
        versions_links = ayon_api.get_versions_links(
            project_name,
            version_ids,
            link_types=[link_type],
            link_direction="in",
        )
        linked_version_ids = {
            link["entityId"]
            for version_id, links in versions_links.items()
            for link in links
            if link["entityType"] == "version"
            and link["entityId"] != version_id
        }
        if not linked_version_ids:
            return set()
        return {
            repre["id"]
            for repre in ayon_api.get_representations(
                project_name,
                version_ids=linked_version_ids,
                fields={"id"},
            )
        }
