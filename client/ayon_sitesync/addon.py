
import os
import sys
import time
import inspect
from datetime import datetime
import threading
import copy
import signal
from collections import deque, defaultdict

import platform

from ayon_core.settings import get_studio_settings
from ayon_core.addon import AYONAddon, ITrayAddon, IPluginPaths, click_wrap
from ayon_core.lib import get_local_site_id

import ayon_api
from ayon_api import (
    get_representation_by_id,
    get_representations,
    get_project_names,
    get_addon_project_settings,
    get_project_root_overrides_by_site_id
)

from .version import __version__
from .providers.local_drive import LocalDriveHandler

from .utils import (
    time_function,
    SyncStatus,
    SiteAlreadyPresentError,
    SiteSyncStatus,
)

SYNC_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


class SiteSyncAddon(AYONAddon, ITrayAddon, IPluginPaths):
    """Addon handling sync of representation files between sites.

    Synchronization server that is syncing published files from local to
    any of implemented providers (like GDrive, S3 etc.)
    Runs in the background and checks all representations, looks for files
    that are marked to be in different location than 'studio' (temporary),
    checks if 'created_dt' field is present denoting successful sync
    with provider destination.
    Sites structure is created during publish OR by calling 'add_site'
    method.

    State of synchronization is being persisted on the server
    in `sitesync_files_status` table.

    By default it will always contain 1 record with
    "name" ==  self.presets["active_site"] per representation_id with state
    of all its files

    Each Tray app has assigned its own  self.presets["local_id"]
    used in sites as a name.
    Tray is searching only for records where name matches its
    self.presets["active_site"] + self.presets["remote_site"].
    "active_site" could be storage in studio ('studio'), or specific
    "local_id" when user is working disconnected from home.
    If the local record has its "created_dt" filled, it is a source and
    process will try to upload the file to all defined remote sites.

    Remote files "id" is real id that could be used in appropriate API.
    Local files have "id" too, for conformity, contains just file name.
    It is expected that multiple providers will be implemented in separate
    classes and registered in 'providers.py'.

    """
    # limit querying DB to look for X number of representations that should
    # be sync, we try to run more loops with less records
    # actual number of files synced could be lower as providers can have
    # different limits imposed by its API
    # set 0 to no limit
    REPRESENTATION_LIMIT = 100
    DEFAULT_SITE = "studio"
    LOCAL_SITE = "local"
    LOG_PROGRESS_SEC = 5  # how often log progress to DB
    DEFAULT_PRIORITY = 50  # higher is better, allowed range 1 - 1000

    name = "sitesync"
    version = __version__

    def initialize(self, addon_settings):
        """Called during Addon Manager creation.

        Collects needed data, checks asyncio presence.
        Sets 'enabled' according to global settings for the addon.
        Shouldn't be doing any initialization, that's a job for 'tray_init'
        """

        # some parts of code need to run sequentially, not in async
        self.lock = None
        self._sync_studio_settings = None
        # settings for all enabled projects for sync
        self._sync_project_settings = None
        self.sitesync_thread = None  # asyncio requires new thread

        self._paused = False
        self._paused_projects = set()
        self._paused_representations = set()
        self._anatomies = {}

        # list of long blocking tasks
        self.long_running_tasks = deque()
        # projects that long tasks are running on
        self.projects_processed = set()

    @property
    def endpoint_prefix(self):
        return "addons/{}/{}".format(self.name, self.version)

    def get_plugin_paths(self):
        return {
            "publish": os.path.join(SYNC_ADDON_DIR, "plugins", "publish")
        }

    def get_site_icons(self):
        """Icons for sites.

        Returns:
            dict[str, str]: Path to icon by site.

        """
        resource_path = os.path.join(
            SYNC_ADDON_DIR, "providers", "resources"
        )
        icons = {}
        for file_path in os.listdir(resource_path):
            if not file_path.endswith(".png"):
                continue
            provider_name, _ = os.path.splitext(os.path.basename(file_path))
            icons[provider_name] = {
                "type": "path",
                "path": os.path.join(resource_path, file_path)
            }
        return icons

    def get_launch_hook_paths(self):
        """Implementation for applications launch hooks.

        Returns:
            str: full absolut path to directory with hooks for the addon

        """
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "launch_hooks"
        )

    # --- Public API ---
    def add_site(
        self,
        project_name,
        representation_id,
        site_name=None,
        file_id=None,
        force=False,
        status=SiteSyncStatus.QUEUED
    ):
        """Adds new site to representation to be synced.

        'project_name' must have synchronization enabled (globally or
        project only)

        Used as a API endpoint from outside applications (Loader etc).

        Use 'force' to reset existing site.

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            site_name (str): Site name of configured site.
            file_id (str): File id.
            force (bool): Reset site if exists.
            status (SiteSyncStatus): Current status,
                default SiteSyncStatus.QUEUED

        Raises:
            SiteAlreadyPresentError: If adding already existing site and
                not 'force'
            ValueError: other errors (repre not found, misconfiguration)

        """
        if not self.get_sync_project_setting(project_name):
            raise ValueError("Project not configured")

        if not site_name:
            site_name = self.DEFAULT_SITE

        representation = get_representation_by_id(
            project_name, representation_id
        )

        files = representation.get("files", [])
        if not files:
            self.log.debug("No files for {}".format(representation_id))
            return

        if not force:
            existing = self.get_repre_sync_state(
                project_name,
                representation_id,
                site_name
            )
            if existing:
                failure = True
                if file_id:
                    file_exists = existing.get("files", {}).get(file_id)
                    if not file_exists:
                        failure = False

                if failure:
                    msg = "Site {} already present".format(site_name)
                    self.log.info(msg)
                    raise SiteAlreadyPresentError(msg)

        new_site_files = [
            {
                "size": repre_file["size"],
                "status": status,
                "timestamp": datetime.now().timestamp(),
                "id": repre_file["id"],
                "fileHash": repre_file["hash"]
            }
            for repre_file in files
        ]

        payload_dict = {"files": new_site_files}
        representation_id = representation_id.replace("-", "")

        self._set_state_sync_state(
            project_name, representation_id, site_name, payload_dict, force
        )

    def remove_site(
        self,
        project_name,
        representation_id,
        site_name,
        remove_local_files=False
    ):
        """Removes site for particular representation in project.

        Args:
            project_name (str): project name (must match DB)
            representation_id (str): MongoDB _id value
            site_name (str): name of configured and active site
            remove_local_files (bool): remove only files for 'local_id'
                site

        Raises:
            ValueError: Throws if any issue.

        """
        if not self.get_sync_project_setting(project_name):
            raise ValueError("Project not configured")

        sync_info = self.get_repre_sync_state(
            project_name,
            representation_id,
            site_name
        )
        if not sync_info:
            msg = "Site {} not found".format(site_name)
            self.log.warning(msg)
            return

        endpoint = "{}/{}/state/{}/{}".format(
            self.endpoint_prefix,
            project_name,
            representation_id,
            site_name
        )

        response = ayon_api.delete(endpoint)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

        if remove_local_files:
            self._remove_local_file(project_name, representation_id, site_name)

    def compute_resource_sync_sites(self, project_name):
        """Get available resource sync sites state for publish process.

        Returns dict with prepared state of sync sites for 'project_name'.
        It checks if Site Sync is enabled, handles alternative sites.
        Publish process stores this dictionary as a part of representation
        document in DB.

        Example:
        [
            {
                'name': '42abbc09-d62a-44a4-815c-a12cd679d2d7',
                'status': SiteSyncStatus.OK
            },
            {'name': 'studio', 'status': SiteSyncStatus.QUEUED},
            {'name': 'SFTP', 'status': SiteSyncStatus.QUEUED}
        ] -- representation is published locally, artist or Settings have set
        remote site as 'studio'. 'SFTP' is alternate site to 'studio'. Eg.
        whenever file is on 'studio', it is also on 'SFTP'.
        """

        def create_metadata(name, created=True):
            """Create sync site metadata for site with `name`"""
            if created:
                status = SiteSyncStatus.OK
            else:
                status = SiteSyncStatus.QUEUED
            return {"name": name, "status": status}

        if (
            not self.sync_studio_settings["enabled"]
            or not self.sync_project_settings[project_name]["enabled"]
        ):
            return [create_metadata(self.DEFAULT_SITE)]

        local_site = self.get_active_site(project_name)
        remote_site = self.get_remote_site(project_name)

        # Attached sites metadata by site name
        # That is the local site, remote site, the always accesible sites
        # and their alternate sites (alias of sites with different protocol)
        attached_sites = {
            local_site: create_metadata(local_site)
        }
        if remote_site and remote_site not in attached_sites:
            attached_sites[remote_site] = create_metadata(
                remote_site, created=False
            )

        attached_sites = self._add_alternative_sites(
            project_name, attached_sites)
        # add skeleton for sites where it should be always synced to
        # usually it would be a backup site which is handled by separate
        # background process
        for site_name in self._get_always_accessible_sites(project_name):
            if site_name not in attached_sites:
                attached_sites[site_name] = (
                    create_metadata(site_name, created=False))
        unique_sites = {
            site["name"]: site
            for site in attached_sites.values()
        }
        return list(unique_sites.values())

    def _get_always_accessible_sites(self, project_name):
        """Sites that synced to as a part of background process.

        Artist machine doesn't handle those, explicit Tray with that site name
        as a local id must be running.
        Example is dropbox site serving as a backup solution

        Returns:
            (list[str]): list of site names
        """
        sync_settings = self.get_sync_project_setting(project_name)
        always_accessible_sites = (
            sync_settings["config"].get("always_accessible_on", [])
        )
        return [site_name.strip() for site_name in always_accessible_sites]

    def _add_alternative_sites(self, project_name, attached_sites):
        """Add skeleton document for alternative sites

        Each new configured site in System Setting could serve as a alternative
        site, it's a kind of alias. It means that files on 'a site' are
        physically accessible also on 'a alternative' site.
        Example is sftp site serving studio files via sftp protocol, physically
        file is only in studio, sftp server has this location mounted.

        Returns:
            (dict[str, dict])
        """
        sync_project_settings = self.get_sync_project_setting(project_name)
        all_sites = sync_project_settings["sites"]

        alt_site_pairs = self._get_alt_site_pairs(all_sites)

        for site_name in all_sites.keys():
            # Get alternate sites (stripped names) for this site name
            alt_sites = {
                site.strip()
                for site in alt_site_pairs.get(site_name)
            }

            # If no alternative sites we don't need to add
            if not alt_sites:
                continue

            # Take a copy of data of the first alternate site that is already
            # defined as an attached site to match the same state.
            match_meta = next(
                (
                    attached_sites[site]
                    for site in alt_sites
                    if site in attached_sites
                ),
                None
            )
            if not match_meta:
                continue

            alt_site_meta = copy.deepcopy(match_meta)
            alt_site_meta["name"] = site_name

            # Note: We change mutable `attached_site` dict in-place
            attached_sites[site_name] = alt_site_meta

        return attached_sites

    def _get_alt_site_pairs(self, conf_sites):
        """Returns dict of site and its alternative sites.

        If `site` has alternative site, it means that alt_site has 'site' as
        alternative site

        Args:
            conf_sites (dict)

        Returns:
            dict[str, list[str]]: {'site': [alternative sites]...}

        """
        alt_site_pairs = defaultdict(set)
        for site_name, site_info in conf_sites.items():
            alt_sites = set(site_info.get("alternative_sites", []))
            alt_site_pairs[site_name].update(alt_sites)

            for alt_site in alt_sites:
                alt_site_pairs[alt_site].add(site_name)

        for site_name, alt_sites in alt_site_pairs.items():
            sites_queue = deque(alt_sites)
            while sites_queue:
                alt_site = sites_queue.popleft()

                # safety against wrong config
                # {"SFTP": {"alternative_site": "SFTP"}
                if alt_site == site_name or alt_site not in alt_site_pairs:
                    continue

                for alt_alt_site in alt_site_pairs[alt_site]:
                    if (
                        alt_alt_site != site_name
                        and alt_alt_site not in alt_sites
                    ):
                        alt_sites.add(alt_alt_site)
                        sites_queue.append(alt_alt_site)

        return alt_site_pairs

    def clear_project(self, project_name, site_name):
        """
            Clear 'project_name' of 'site_name' and its local files

            Works only on real local sites, not on 'studio'
        """

        # TODO implement
        self.log.warning("Method 'clear_project' is not implemented.")

        # query = {
        #     "type": "representation",
        #     "files.sites.name": site_name
        # }
        #
        # # TODO currently not possible to replace with get_representations
        # representations = list(
        #     self.connection.database[project_name].find(query))
        # if not representations:
        #     self.log.debug("No repre found")
        #     return
        #
        # for repre in representations:
        #     self.remove_site(project_name, repre.get("_id"), site_name, True)

    # TODO hook to some trigger - no Sync Queue anymore
    def validate_project(self, project_name, site_name, reset_missing=False):
        """Validate 'project_name' of 'site_name' and its local files

        If file present and not marked with a 'site_name' in DB, DB is
        updated with site name and file modified date.

        Args:
            project_name (str): project name
            site_name (str): active site name
            reset_missing (bool): if True reset site in DB if missing
                physically to be resynched
        """
        self.log.debug("Validation of {} for {} started".format(
            project_name, site_name
        ))
        repre_entities = list(get_representations(project_name))
        if not repre_entities:
            self.log.debug("No repre found")
            return

        sites_added = 0
        sites_reset = 0
        repre_ids = [repre["id"] for repre in repre_entities]
        repre_states = self.get_representations_sync_state(
            project_name, repre_ids, site_name, site_name)

        for repre_entity in repre_entities:
            repre_id = repre_entity["id"]
            is_on_site = False
            repre_state = repre_states.get(repre_id)
            if repre_state:
                is_on_site = repre_state[0] == SiteSyncStatus.OK
            for repre_file in repre_entity.get("files", []):
                file_path = repre_file.get("path", "")
                local_file_path = self.get_local_file_path(
                    project_name, site_name, file_path
                )

                file_exists = (
                    local_file_path and os.path.exists(local_file_path)
                )
                if not is_on_site:
                    if file_exists:
                        self.log.debug(
                            f"Adding presence on site '{site_name}' for "
                            f"'{repre_id}'"
                        )
                        self.add_site(
                            project_name,
                            repre_id,
                            site_name=site_name,
                            file_id=repre_file["id"],
                            force=True,
                            status=SiteSyncStatus.OK
                        )
                        sites_added += 1
                else:
                    if not file_exists and reset_missing:
                        self.log.debug(
                            "Resetting site {} for {}".format(
                                site_name, repre_id
                            ))
                        self.reset_site_on_representation(
                            project_name,
                            repre_id,
                            site_name=site_name,
                            file_id=repre_file["_id"]
                        )
                        sites_reset += 1

        if sites_added % 100 == 0:
            self.log.debug("Sites added {}".format(sites_added))

        self.log.debug("Validation of {} for {} ended".format(
            project_name, site_name
        ))
        self.log.info("Sites added {}, sites reset {}".format(
            sites_added, reset_missing
        ))

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_representation(
        self, project_name, representation_id, site_name
    ):
        """Pause sync of representation entity on site.

        Sets 'representation_id' as paused, eg. no syncing should be
            happening on it.

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            site_name (str): Site name 'gdrive', 'studio' etc.

        """
        self.log.info("Pausing SiteSync for {}".format(representation_id))
        self._paused_representations.add(representation_id)
        repre_entity = get_representation_by_id(
            project_name, representation_id
        )
        self.update_db(project_name, repre_entity, site_name, pause=True)

    # TODO hook to some trigger - no Sync Queue anymore
    def unpause_representation(
        self, project_name, representation_id, site_name
    ):
        """Unpause sync of representation entity on site.

        Does not fail or warn if repre wasn't paused.

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            site_name (str): Site name 'gdrive', 'studio' etc.
        """
        self.log.info("Unpausing SiteSync for {}".format(representation_id))
        try:
            self._paused_representations.remove(representation_id)
        except KeyError:
            pass
        # self.paused_representations is not persistent
        repre_entity = get_representation_by_id(
            project_name, representation_id
        )
        self.update_db(project_name, repre_entity, site_name, pause=False)

    def is_representation_paused(
        self, representation_id, check_parents=False, project_name=None
    ):
        """Is representation paused.

        Args:
            representation_id (str): Representation id.
            check_parents (bool): Check if parent project or server itself
                are not paused.
            project_name (str): Project to check if paused.

            if 'check_parents', 'project_name' should be set too

        Returns:
            bool: Is representation paused now.

        """
        is_paused = representation_id in self._paused_representations
        if check_parents and project_name:
            is_paused = (
                is_paused
                or self.is_project_paused(project_name)
                or self.is_paused()
            )
        return is_paused

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_project(self, project_name):
        """Pause sync of whole project.

        Args:
            project_name (str): Project name.

        """
        self.log.info("Pausing SiteSync for {}".format(project_name))
        self._paused_projects.add(project_name)

    # TODO hook to some trigger - no Sync Queue anymore
    def unpause_project(self, project_name):
        """Unpause sync of whole project.

        Does not fail or warn if project wasn't paused.

        Args:
            project_name (str): Project name.

        """
        self.log.info("Unpausing SiteSync for {}".format(project_name))
        try:
            self._paused_projects.remove(project_name)
        except KeyError:
            pass

    def is_project_paused(self, project_name, check_parents=False):
        """Is project sync paused.

        Args:
            project_name (str):
            check_parents (bool): check if server itself
                is not paused

        Returns:
            bool: Is project paused.

        """
        is_paused = project_name in self._paused_projects
        if check_parents:
            is_paused = is_paused or self.is_paused()
        return is_paused

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_server(self):
        """Pause sync server.

        It won't check anything, not uploading/downloading...
        """
        self.log.info("Pausing SiteSync")
        self._paused = True

    def unpause_server(self):
        """Unpause server sync."""
        self.log.info("Unpausing SiteSync")
        self._paused = False

    def is_paused(self):
        """ Is server paused """
        return self._paused

    def get_active_site_type(self, project_name, local_settings=None):
        """Active site which is defined by artist.

        Unlike 'get_active_site' is this method also checking local settings
        where might be different active site set by user. The output is limited
        to "studio" and "local".

        This method is used by Anatomy.

        Todos:
            Check if sync server is enabled for the project.
            - To be able to do that the sync settings MUST NOT be cached for
                all projects at once. The sync settings preparation for all
                projects is reasonable only in sync server loop.
            `local_settings` is probably obsolete in AYON

        Args:
            project_name (str): Name of project where to look for active site.
            local_settings (Optional[dict[str, Any]]): Prepared local settings.

        Returns:
            Literal["studio", "local"]: Active site.
        """
        if not self.enabled:
            return "studio"

        sync_project_settings = self.get_sync_project_setting(project_name)

        if not sync_project_settings["enabled"]:
            return "studio"

        return (
            sync_project_settings["local_setting"].get("active_site")
            or sync_project_settings["config"]["active_site"]
        )

    def get_active_site(self, project_name):
        """Returns active (mine) site for project from settings.

        Output logic:
            - 'studio' if Site Sync is disabled
            - value from 'get_local_site_id' if active site is 'local'
            - any other site name from local settings
                or project settings (site could be forced from PS)

        Returns:
            str: Site name.

        """
        active_site_type = self.get_active_site_type(project_name)
        if active_site_type == self.LOCAL_SITE:
            return get_local_site_id()
        return active_site_type

    # remote site
    def get_remote_site(self, project_name):
        """Remote (theirs) site for project from settings."""
        sync_project_settings = self.get_sync_project_setting(project_name)
        remote_site = (
            sync_project_settings["local_setting"].get("remote_site")
            or sync_project_settings["config"]["remote_site"]
        )
        if remote_site == self.LOCAL_SITE:
            return get_local_site_id()

        return remote_site

    def get_site_root_overrides(
        self, project_name, site_name, local_settings=None
    ):
        """Get root overrides for project on a site.

        Implemented to be used in 'Anatomy' for other than 'studio' site.

        Args:
            project_name (str): Project for which root overrides should be
                received.
            site_name (str): Name of site for which should be received roots.
            local_settings (Optional[dict[str, Any]]): Prepare local settigns
                values.

        Returns:
            Union[dict[str, Any], None]: Root overrides for this machine.

            {"work": "c:/projects_local"}
        """

        # Validate that site name is valid
        if site_name not in ("studio", "local"):
            # Consider local site id as 'local'
            if site_name != get_local_site_id():
                raise ValueError((
                    "Root overrides are available only for"
                    " default sites not for \"{}\""
                ).format(site_name))
            site_name = "local"

        sitesync_settings = self.get_sync_project_setting(project_name)

        roots = {}
        if not sitesync_settings["enabled"]:
            return roots
        local_project_settings = sitesync_settings["local_setting"]
        if site_name == "local":
            for root_info in local_project_settings["local_roots"]:
                roots[root_info["name"]] = root_info["path"]

        return roots

    def get_local_normalized_site(self, site_name):
        """Normlize local site name.

         Return 'local' if 'site_name' is local id.

        In some places Settings or Local Settings require 'local' instead
        of real site name.

        Returns:
            str: Normalized site name.

        """
        if site_name == get_local_site_id():
            site_name = self.LOCAL_SITE

        return site_name

    def is_representation_on_site(
        self, project_name, representation_id, site_name, max_retries=None
    ):
        """Check if representation has all files available on site.

        Args:
            project_name (str)
            representation_id (str)
            site_name (str)
            max_retries (int) (optional) - provide only if method used in while
                loop to bail out

        Returns:
            bool: True if representation has all files correctly on the site.

        Raises:
              ValueError  Only If 'max_retries' provided if upload/download
                failed too many times to limit infinite loop check.

        """
        representation_status = self.get_repre_sync_state(
            project_name, representation_id, site_name)
        if not representation_status:
            return False

        if site_name == get_local_site_id():
            status = representation_status["localStatus"]
        else:
            status = representation_status["remoteStatus"]

        if max_retries:
            tries = status.get("retries", 0)
            if tries >= max_retries:
                raise ValueError("Failed too many times")

        return status["status"] == SiteSyncStatus.OK

    def _reset_timer_with_rest_api(self):
        # POST to webserver sites to add to representations
        webserver_url = os.environ.get("AYON_WEBSERVER_URL")
        if not webserver_url:
            self.log.warning("Couldn't find webserver url")
            return

        rest_api_url = "{}/sitesync/reset_timer".format(
            webserver_url
        )

        try:
            import requests
        except Exception:
            self.log.warning(
                "Couldn't add sites to representations "
                "('requests' is not available)"
            )
            return

        requests.post(rest_api_url)

    def get_enabled_projects(self):
        """Returns list of projects which have SiteSync enabled."""
        enabled_projects = []

        if self.enabled:
            for project_name in get_project_names():
                if self.is_project_enabled(project_name):
                    enabled_projects.append(project_name)

        return enabled_projects

    def is_project_enabled(self, project_name, single=False):
        """Checks if 'project_name' is enabled for syncing.
        'get_sync_project_setting' is potentially expensive operation (pulls
        settings for all projects if cached version is not available), using
        project_settings for specific project should be faster.
        Args:
            project_name (str)
            single (bool): use 'get_addon_project_settings' method
        """
        if self.enabled:
            if single:
                project_settings = get_addon_project_settings(
                    self.name, self.version, project_name
                )
            else:
                project_settings = self.get_sync_project_setting(project_name)
            if project_settings and project_settings.get("enabled"):
                return True
        return False

    def handle_alternate_site(
        self, project_name, representation_id, processed_site, file_id
    ):
        """
        For special use cases where one site vendors another.

        Current use case is sftp site vendoring (exposing) same data as
        regular site (studio). Each site is accessible for different
        audience. 'studio' for artists in a studio, 'sftp' for externals.

        Change of file status on one site actually means same change on
        'alternate' site. (eg. artists publish to 'studio', 'sftp' is using
        same location >> file is accessible on 'sftp' site right away.

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            processed_site (str): Real site_name of published/uploaded file
            file_id (str): File id of file handled.

        """
        sites = self._transform_sites_from_settings(self.sync_studio_settings)
        sites[self.DEFAULT_SITE] = {
            "provider": "local_drive",
            "alternative_sites": []
        }

        alternate_sites = []
        for site_name, site_info in sites.items():
            conf_alternative_sites = site_info.get("alternative_sites", [])
            if processed_site in conf_alternative_sites:
                alternate_sites.append(site_name)
                continue
            if processed_site == site_name and conf_alternative_sites:
                alternate_sites.extend(conf_alternative_sites)
                continue

        if not alternate_sites:
            return

        sync_state = self.get_repre_sync_state(
            project_name,
            representation_id,
            processed_site
        )
        # not yet available on processed_site, wont update alternate site yet
        if not sync_state:
            return
        for file_info in sync_state["files"]:
            # expose status of remote site, it is expected on the server
            file_info["status"] = file_info["remoteStatus"]["status"]

        payload_dict = {"files": sync_state["files"]}

        alternate_sites = set(alternate_sites)
        for alt_site in alternate_sites:
            self.log.debug("Adding alternate {} to {}".format(
                alt_site, representation_id))

            self._set_state_sync_state(
                project_name,
                representation_id,
                alt_site,
                payload_dict
            )

    # TODO - for Loaders
    def get_repre_info_for_versions(
        self, project_name, version_ids, active_site, remote_site
    ):
        """Returns representation for versions and sites combi

        Args:
            project_name (str): Project name
            version_ids (Iterable[str]): Version ids.
            active_site (str): 'local', 'studio' etc
            remote_site (str): dtto

        Returns:

        """
        version_ids = set(version_ids)
        endpoint = "{}/projects/{}/sitesync/state".format(
            self.endpoint_prefix, project_name
        )

        # get to upload
        kwargs = {
            "localSite": active_site,
            "remoteSite": remote_site,
            "versionIdFilter": list(version_ids)
        }

        # kwargs["representationId"] = "94dca33a-7705-11ed-8c0a-34e12d91d510"

        response = ayon_api.get(endpoint, **kwargs)
        repre_states = response.data.get("representations", [])
        repre_info_by_version_id = {
            version_id: {
                "id": version_id,
                "repre_count": 0,
                "avail_repre_local": 0,
                "avail_repre_remote": 0,
            }
            for version_id in version_ids
        }
        repre_states_by_version_id = defaultdict(list)
        for repre_state in repre_states:
            version_id = repre_state["versionId"]
            repre_states_by_version_id[version_id].append(repre_state)

        for version_id, repre_states in repre_states_by_version_id.items():
            repre_info = repre_info_by_version_id[version_id]
            repre_info["repre_count"] = len(repre_states)
            repre_info["avail_repre_local"] = sum(
                self._is_available(repre_state, "localStatus")
                for repre_state in repre_states
            )
            repre_info["avail_repre_remote"] = sum(
                self._is_available(repre_state, "remoteStatus")
                for repre_state in repre_states
            )

        return list(repre_info_by_version_id.values())
    # --- End of Public API ---

    def _is_available(self, repre, status):
        """Helper to decide if repre is download/uploaded on site.

        Returns:
            int: 1 if available, 0 if not.

        """
        return int(repre[status]["status"] == SiteSyncStatus.OK)

    def get_local_file_path(self, project_name, site_name, file_path):
        """Externalized for app.

        Args:
            project_name (str): Project name.
            site_name (str): Site name.
            file_path (str): File path from other site.

        Returns:
            str: Resolved local path.

        """
        handler = LocalDriveHandler(project_name, site_name)
        local_file_path = handler.resolve_path(file_path)

        return local_file_path

    def tray_init(self):
        """Initialization of Site Sync Server for Tray.

        Called when tray is initialized, it checks if addon should be
        enabled. If not, no initialization necessary.
        """
        self.server_init()

    def server_init(self):
        """Actual initialization of Sync Server."""
        # import only in tray or Python3, because of Python2 hosts
        if not self.enabled:
            return

        from .sitesync import SiteSyncThread

        self.lock = threading.Lock()

        self.sitesync_thread = SiteSyncThread(self)

    def tray_start(self):
        """Triggered when Tray is started.

        Checks if configuration presets are available and if there is
        any provider ('gdrive', 'S3') that is activated
        (eg. has valid credentials).
        """
        self.server_start()

    def server_start(self):
        if self.enabled:
            self.sitesync_thread.start()
        else:
            self.log.info(
                "SiteSync is not enabled. Site Sync server was not started."
            )

    def tray_exit(self):
        """Stops sync thread if running.

        Called from Addon Manager
        """
        self.server_exit()

    def server_exit(self):
        if not self.sitesync_thread:
            return

        if not self.is_running:
            return
        try:
            self.log.info("Stopping sync server server")
            self.sitesync_thread.is_running = False
            self.sitesync_thread.stop()
            self.log.info("Sync server stopped")
        except Exception:
            self.log.warning(
                "Error has happened during Killing sync server",
                exc_info=True
            )

    def tray_menu(self, parent_menu):
        pass

    @property
    def is_running(self):
        return self.sitesync_thread.is_running

    def get_anatomy(self, project_name):
        """Get already created or newly created anatomy for project

        Args:
            project_name (str): Project name.

        Return:
            Anatomy: Project anatomy object.
        """
        from ayon_core.pipeline import Anatomy

        return self._anatomies.get(project_name) or Anatomy(project_name)

    @property
    def sync_studio_settings(self):
        if self._sync_studio_settings is None:
            self._sync_studio_settings = (
                get_studio_settings().get(self.name)
            )

        return self._sync_studio_settings

    @property
    def sync_project_settings(self):
        if self._sync_project_settings is None:
            self.set_sync_project_settings()

        return self._sync_project_settings

    def set_sync_project_settings(self, exclude_locals=False):
        """
            Set sync_project_settings for all projects (caching)
            Args:
                exclude_locals (bool): ignore overrides from Local Settings
            For performance
        """
        sync_project_settings = self._prepare_sync_project_settings(
            exclude_locals)

        self._sync_project_settings = sync_project_settings

    def _prepare_sync_project_settings(self, exclude_locals):
        sync_project_settings = {}

        sites = self._transform_sites_from_settings(
            self.sync_studio_settings)

        project_names = get_project_names()
        for project_name in project_names:
            project_sites = copy.deepcopy(sites)
            project_settings = get_addon_project_settings(
                self.name, self.version, project_name)

            project_sites.update(self._get_default_site_configs(
                project_settings["enabled"], project_name, project_settings
            ))

            project_sites.update(
                self._transform_sites_from_settings(project_settings))

            project_settings["sites"] = project_sites

            sync_project_settings[project_name] = project_settings

        if not sync_project_settings:
            self.log.info("No enabled and configured projects for sync.")
        return sync_project_settings

    def get_sync_project_setting(
        self, project_name, exclude_locals=False, cached=True
    ):
        """ Handles pulling sitesync's settings for enabled 'project_name'

        Args:
            project_name (str): used in project settings
            exclude_locals (bool): ignore overrides from Local Settings
            cached (bool): use pre-cached values, or return fresh ones
                cached values needed for single loop (with all overrides)
                fresh values needed for Local settings (without overrides)

        Returns:
            dict: settings dictionary for the enabled project,
                empty if no settings or sync is disabled

        """
        # presets set already, do not call again and again
        # self.log.debug("project preset {}".format(self.presets))
        if not cached:
            return self._prepare_sync_project_settings(exclude_locals)\
                [project_name]

        if (
            not self.sync_project_settings
            or not self.sync_project_settings.get(project_name)
        ):
            self.set_sync_project_settings(exclude_locals)
        return self.sync_project_settings.get(project_name)

    def _transform_sites_from_settings(self, settings):
        """Transforms list of 'sites' from Setting to dict.

        It processes both System and Project Settings as they have same format.
        """
        sites = {}
        if not self.enabled:
            return sites

        for whole_site_info in settings.get("sites", []):
            site_name = whole_site_info["name"]
            provider_specific = copy.deepcopy(
                whole_site_info[whole_site_info["provider"]]
            )
            configured_site = {
                "enabled": True,
                "alternative_sites": whole_site_info["alternative_sites"],
                "root": provider_specific.pop("roots", None)
            }
            configured_site.update(provider_specific)

            sites[site_name] = configured_site
        return sites

    def _get_project_root_overrides_by_site_id(
            self, project_name, site_name=None):
        """Returns projects roots and their overrides."""
        # overrides for Studio site for particular user
        # TODO temporary to get roots without overrides
        # ayon_api.get_project_roots_by_site returns only overrides.
        # Should be replaced when ayon_api implements `siteRoots` method
        if not site_name:
            site_name = get_local_site_id()
        platform_name = platform.system().lower()
        roots = ayon_api.get(
            f"projects/{project_name}/siteRoots",
            platform=platform_name
        ).data
        root_overrides = get_project_root_overrides_by_site_id(
            project_name, site_name
        )
        for key, value in roots.items():
            override = root_overrides.get(key)
            if override:
                roots[key] = override

        return roots

    def _get_default_site_configs(
        self, sync_enabled=True, project_name=None, project_settings=None
    ):
        """Settings for 'studio' and user's local site

        Returns base values from setting, not overridden by Local Settings,
        eg. value used to push TO LS not to get actual value for syncing.

        Args:
            sync_enabled (Optional[bool]): Is sync enabled.
            project_name (Optional[str]): Project name.
            project_settings (Optional[dict]): Project settings.

        """
        local_site_id = get_local_site_id()
        roots = self._get_project_root_overrides_by_site_id(
            project_name, local_site_id
        )
        studio_config = {
            "enabled": True,
            "provider": "local_drive",
            "root": roots
        }
        all_sites = {self.DEFAULT_SITE: studio_config}
        if sync_enabled:
            roots = project_settings["local_setting"]["local_roots"]
            local_site_dict = {
                "enabled": True,
                "provider": "local_drive",
                "root": roots
            }
            all_sites[local_site_id] = local_site_dict
            # duplicate values for normalized local name
            all_sites["local"] = local_site_dict
        return all_sites

    def get_provider_for_site(self, project_name=None, site=None):
        """Get provider name for site (unique name across all projects)."""
        sites = {
            self.DEFAULT_SITE: "local_drive",
            self.LOCAL_SITE: "local_drive",
            get_local_site_id(): "local_drive"
        }

        if site in sites.keys():
            return sites[site]

        # backward compatibility
        if project_name:
            proj_settings = self.get_sync_project_setting(project_name)
            provider = (
                proj_settings
                .get("sites", {})
                .get(site, {})
                .get("provider")
            )
            if provider:
                return provider

        sync_sett = self.sync_studio_settings
        for site_config in sync_sett.get("sites"):
            sites[site_config["name"]] = site_config["provider"]

        return sites.get(site, "N/A")

    @time_function
    def get_sync_representations(
        self, project_name, active_site, remote_site, limit=10
    ):
        """
            Get representations that should be synced, these could be
            recognised by presence of document in 'files.sites', where key is
            a provider (GDrive, S3) and value is empty document or document
            without 'created_dt' field. (Don't put null to 'created_dt'!).

            Querying of 'to-be-synched' files is offloaded to Mongod for
            better performance. Goal is to get as few representations as
            possible.
        Args:
            project_name (str):
            active_site (str): identifier of current active site (could be
                'local_0' when working from home, 'studio' when working in the
                studio (default)
            remote_site (str): identifier of remote site I want to sync to

        Returns:
            list[dict]: Representation states.

        """
        self.log.debug("Check representations for: {}-{}".format(
            active_site, remote_site
        ))

        endpoint = "{}/{}/state".format(
            self.endpoint_prefix, project_name
        )

        # get to upload
        kwargs = {
            "localSite": active_site,
            "remoteSite": remote_site,
            "localStatusFilter": [SiteSyncStatus.OK],
            "remoteStatusFilter": [SiteSyncStatus.QUEUED],
        }

        response = ayon_api.get(endpoint, **kwargs)
        if response.status_code not in [200, 204]:
            raise RuntimeError(
                "Cannot get representations for sync with code {}".format(
                    response.status_code
                )
            )

        repre_states = response.data["representations"]

        # get to download
        if len(repre_states) < limit:
            kwargs["localStatusFilter"] = [SiteSyncStatus.QUEUED]
            kwargs["remoteStatusFilter"] = [SiteSyncStatus.OK]

            response = ayon_api.get(endpoint, **kwargs)
            repre_states.extend(response.data["representations"])

        return repre_states

    def check_status(self, file_state, local_site, remote_site, config_preset):
        """Check synchronization status of a file.

        The file is on representation status is checked for single 'provider'.
            (Eg. check if 'scene.ma' of lookdev.v10 should be synced to GDrive

        Always is comparing local record, eg. site with
            'name' == self.presets[PROJECT_NAME]["config"]["active_site"]

        This leads to trigger actual upload or download, there is
            a use case 'studio' <> 'remote' where user should publish
            to 'studio', see progress in Tray GUI, but do not do
            physical upload/download
            (as multiple user would be doing that).

            Do physical U/D only when any of the sites is user's local, in that
            case only user has the data and must U/D.

        Args:
            file_state (dict): File info from site sync database.
            local_site (str): Local site of compare (usually 'studio').
            remote_site (str): Remote site (gdrive etc).
            config_preset (dict): Config about active site, retries.

        Returns:
            int: Sync status value of representation.

        """
        if get_local_site_id() not in (local_site, remote_site):
            # don't do upload/download for studio sites
            self.log.debug(
                "No local site {} - {}".format(local_site, remote_site)
            )
            return SyncStatus.DO_NOTHING

        local_status = file_state["localStatus"]["status"]
        remote_status = file_state["remoteStatus"]["status"]

        if (
            local_status != SiteSyncStatus.OK
            and remote_status == SiteSyncStatus.OK
        ):
            retries = file_state["localStatus"]["retries"]
            if retries < int(config_preset["retry_cnt"]):
                return SyncStatus.DO_DOWNLOAD

        if (
            remote_status != SiteSyncStatus.OK
            and local_status == SiteSyncStatus.OK
        ):
            retries = file_state["remoteStatus"]["retries"]
            if retries < int(config_preset["retry_cnt"]):
                return SyncStatus.DO_UPLOAD

        return SyncStatus.DO_NOTHING

    def update_db(
        self,
        project_name,
        repre_status,
        site_name,
        new_file_id=None,
        file=None,
        side=None,
        error=None,
        progress=None,
        priority=None,
        pause=None
    ):
        """Update 'provider' portion of records in DB.

        Args:
            project_name (str): Project name. Force to db connection as
                each file might come from different collection.
            repre_status (dict): Representation status from sitesync database.
            site_name (str): Site name.
            new_file_id (Optional[str]): File id of new file.
            file (dict[str, Any]): info about processed file (pulled from DB)
            side (str): 'local' | 'remote'
            error (str): exception message
            progress (float): 0-1 of progress of upload/download
            priority (int): 0-100 set priority
            pause (bool): stop synchronizing (only before starting of download,
                upload)

        Returns:
            None
        """
        files_status = []
        for file_status in repre_status["files"]:
            status_entity = copy.deepcopy(
                file_status["{}Status".format(side)]
            )
            status_entity["fileHash"] = file_status["fileHash"]
            status_entity["id"] = file_status["id"]
            if file_status["fileHash"] == file["fileHash"]:
                if new_file_id:
                    status_entity["status"] = SiteSyncStatus.OK
                    status_entity.pop("message")
                    status_entity.pop("retries")
                elif progress is not None:
                    status_entity["status"] = SiteSyncStatus.IN_PROGRESS
                    status_entity["progress"] = progress
                elif error:
                    max_retries = int(
                        self.sync_project_settings
                        [project_name]
                        ["config"]
                        ["retry_cnt"]
                    )
                    tries = status_entity.get("retries", 0)
                    tries += 1
                    status_entity["retries"] = tries
                    status_entity["message"] = error
                    if tries >= max_retries:
                        status_entity["status"] = SiteSyncStatus.FAILED
                elif pause is not None:
                    if pause:
                        status_entity["pause"] = True
                    else:
                        status_entity.remove("pause")
                files_status.append(status_entity)

        representation_id = repre_status["representationId"]

        endpoint = "{}/{}/state/{}/{}".format(
            self.endpoint_prefix,
            project_name,
            representation_id,
            site_name)

        # get to upload
        kwargs = {
            "files": files_status
        }

        if priority:
            kwargs["priority"] = priority

        response = ayon_api.post(endpoint, **kwargs)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

        if progress is not None or priority is not None:
            return

        status = "failed"
        error_str = "with error {}".format(error)
        if new_file_id:
            status = "succeeded with id {}".format(new_file_id)
            error_str = ""

        source_file = file.get("path", "")

        self.log.debug(
            "File for {} - {source_file} process {status} {error_str}".format(
                representation_id,
                status=status,
                source_file=source_file,
                error_str=error_str
            )
        )

    def reset_site_on_representation(
        self,
        project_name,
        representation_id,
        side=None,
        file_id=None,
        site_name=None
    ):
        """
            Reset information about synchronization for particular 'file_id'
            and provider.
            Useful for testing or forcing file to be reuploaded.

            'side' and 'site_name' are disjunctive.

            'side' is used for resetting local or remote side for
            current user for repre.

            'site_name' is used to set synchronization for particular site.
            Should be used when repre should be synced to new site.

        Args:
            project_name (str): name of project (eg. collection) in DB
            representation_id (str): Representation id.
            file_id (str): File id in representation.
            side (str): Local or remote side.
            site_name (str): for adding new site

        Raises:
            SiteAlreadyPresentError - if adding already existing site and
                not 'force'
            ValueError - other errors (repre not found, misconfiguration)
        """
        representation = get_representation_by_id(
            project_name, representation_id
        )
        if not representation:
            raise ValueError(
                "Representation {} not found in {}".format(
                    representation_id, project_name
                )
            )

        if side and site_name:
            raise ValueError(
                "Misconfiguration, only one of side and"
                " site_name arguments should be passed."
            )

        if side:
            if side == "local":
                site_name = self.get_active_site(project_name)
            else:
                site_name = self.get_remote_site(project_name)

        self.add_site(
            project_name, representation_id, site_name, file_id, force=True
        )

    def _get_progress_for_repre_new(
        self,
        project_name,
        representation,
        local_site_name,
        remote_site_name=None
    ):
        representation_id = representation["id"]
        sync_status = self.get_repre_sync_state(
            project_name,
            representation_id,
            local_site_name,
            remote_site_name
        )

        progress = {
            local_site_name: -1,
            remote_site_name: -1
        }
        if not sync_status:
            return progress

        mapping = {
            "localStatus": local_site_name,
            "remoteStatus": remote_site_name
        }
        files = {local_site_name: 0, remote_site_name: 0}
        file_states = sync_status.get("files") or []
        for file_state in file_states:
            for status in mapping.keys():
                status_info = file_state[status]
                site_name = mapping[status]
                files[site_name] += 1
                norm_progress = max(progress[site_name], 0)
                if status_info["status"] == SiteSyncStatus.OK:
                    progress[site_name] = norm_progress + 1
                elif status_info.get("progress"):
                    progress[site_name] = norm_progress + status_info[
                        "progress"]
                else:  # site exists, might be failed, do not add again
                    progress[site_name] = 0

        # for example 13 fully avail. files out of 26 >> 13/26 = 0.5
        return {
            local_site_name: (
                progress[local_site_name] / max(files[local_site_name], 1)
            ),
            remote_site_name: (
                progress[remote_site_name] / max(files[remote_site_name], 1)
            )
        }

    def _get_progress_for_repre_old(
        self,
        representation,
        local_site_name,
        remote_site_name=None
    ):
        return self._get_progress_for_repre_new(
            representation["context"]["project"]["name"],
            representation,
            local_site_name,
            remote_site_name
        )

    def get_progress_for_repre(self, *args, **kwargs):
        """Calculates average progress for representation.

        If site has created_dt >> fully available >> progress == 1

        Could be calculated in aggregate if it would be too slow

        Returns:
            (dict) with active and remote sites progress
            {'studio': 1.0, 'gdrive': -1} - gdrive site is not present
                -1 is used to highlight the site should be added
            {'studio': 1.0, 'gdrive': 0.0} - gdrive site is present, not
                uploaded yet

        """
        sig_new = inspect.signature(self._get_progress_for_repre_new)
        sig_old = inspect.signature(self._get_progress_for_repre_old)
        try:
            sig_new.bind(*args, **kwargs)
            return self._get_progress_for_repre_new(*args, **kwargs)
        except TypeError:
            pass

        try:
            sig_old.bind(*args, **kwargs)
            print(
                "Using old signature of 'get_progress_for_repre'"
                " please add project name as first argument."
            )
            return self._get_progress_for_repre_old(*args, **kwargs)
        except TypeError:
            pass

        return self._get_progress_for_repre_new(*args, **kwargs)

    def _set_state_sync_state(
        self,
        project_name,
        representation_id,
        site_name,
        payload_dict,
        force=False,
    ):
        """Calls server endpoint to store sync info for 'representation_id'."""
        endpoint = "{}/{}/state/{}/{}".format(
            self.endpoint_prefix, project_name, representation_id, site_name
        )
        if force:
            endpoint = f"{endpoint}?reset=true"

        response = ayon_api.post(endpoint, **payload_dict)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

    def get_repre_sync_state(
        self,
        project_name,
        representation_id,
        local_site_name,
        remote_site_name=None,
        **kwargs
    ):
        """Use server endpoint to get synchronization info for representation.

        Warning:
            Logic of this

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            local_site_name (str)
            remote_site_name (str)
            all other parameters for `Get Site Sync State` endpoint if
                necessary

        """
        repre_states = self._get_repres_state(
            project_name,
            {representation_id},
            local_site_name,
            remote_site_name,
            **kwargs
        )
        if repre_states:
            repre_state = repre_states[0]
            if repre_state["localStatus"]["status"] != -1:
                return repre_state

    def get_representations_sites_sync_state(
        self,
        project_name,
        representation_ids,
        site_names=None,
    ):
        """ Returns all site states for representation ids.

        Args:
            project_name (str):
            representation_ids (list[str]): even single repre should be in []
            site_names (list[str]): sub-selection of states

        Returns:
            list[dict]: dicts follow RepresentationSiteStateModel
        """
        endpoint = "{}/{}/state/representations".format(
            self.endpoint_prefix, project_name
        )

        payload_dict = {
            "representationIds": representation_ids
        }
        if site_names:
            payload_dict["siteNames"] = site_names

        response = ayon_api.get(endpoint, **payload_dict)
        if response.status_code != 200:
            raise RuntimeError(
                "Can't get all sites sync state for representations {}".format(
                    representation_ids
                )
            )

        return response.data

    def get_representations_sync_state(
        self,
        project_name,
        representation_ids,
        local_site_name,
        remote_site_name=None,
        **kwargs
    ):
        """Use server endpoint to get synchronization info for representations.

        Calculates float progress based on progress of all files for repre.
        If repre is fully synchronized it returns 1, 0 for any other state.

        Args:
            project_name (str):
            representation_ids (list): even single repre should be in []
            local_site_name (str)
            remote_site_name (str)
            all other parameters for `Get Site Sync State` endpoint if
                necessary.

        Returns:
            dict[str, tuple[float, float]]: Progress by representation id.

        """
        repre_states = self._get_repres_state(
            project_name,
            representation_ids,
            local_site_name,
            remote_site_name,
            **kwargs
        )
        states = {}
        for repre_state in repre_states:
            repre_files_count = len(repre_state["files"])

            repre_local_status = repre_state["localStatus"]["status"]
            repre_local_progress = 0
            if repre_local_status == SiteSyncStatus.OK:
                repre_local_progress = 1
            elif repre_local_status == SiteSyncStatus.IN_PROGRESS:
                local_sum = sum(
                    file_info["localStatus"].get("progress", 0)
                    for file_info in repre_state["files"]
                )
                repre_local_progress = local_sum / repre_files_count

            repre_remote_status = repre_state["remoteStatus"]["status"]
            repre_remote_progress = 0
            if repre_remote_status == SiteSyncStatus.OK:
                repre_remote_progress = 1
            elif repre_remote_status == SiteSyncStatus.IN_PROGRESS:
                remote_sum = sum(
                    file_info["remoteStatus"].get("progress", 0)
                    for file_info in repre_state["files"]
                )
                repre_remote_progress = remote_sum / repre_files_count

            states[repre_state["representationId"]] = (
                repre_local_progress,
                repre_remote_progress
            )

        return states

    def _get_repres_state(
        self,
        project_name,
        representation_ids,
        local_site_name,
        remote_site_name=None,
        **kwargs
    ):
        """Use server endpoint to get sync info for representations.

        Args:
            project_name (str): Project name.
            representation_ids (Iterable[str]): Representation ids.
            local_site_name (str): Local site name.
            remote_site_name (str): Remote site name.
            kwargs: All other parameters for `Get Site Sync State` endpoint if
                necessary

        """
        if not remote_site_name:
            remote_site_name = local_site_name
        payload_dict = {
            "localSite": local_site_name,
            "remoteSite": remote_site_name,
            "representationIds": representation_ids
        }
        if kwargs:
            payload_dict.update(kwargs)

        endpoint = "{}/{}/state".format(
            self.endpoint_prefix, project_name
        )

        response = ayon_api.get(endpoint, **payload_dict)
        if response.status_code != 200:
            raise RuntimeError(
                "Cannot get sync state for representations {}".format(
                    representation_ids
                )
            )

        return response.data["representations"]

    def get_version_availability(
        self,
        project_name,
        version_ids,
        local_site_name,
        remote_site_name,
        **kwargs
    ):
        """Returns aggregated state for version ids.

        Args:
            project_name (str): Project name.
            version_ids (Iterable[str]): Version ids.
            local_site_name (str): Local site name.
            remote_site_name (str): Remote site name.
            kwargs: All other parameters for `Get Site Sync State` endpoint if
                necessary.

        Returns:
            dict[str, tuple[float, float]]: Status by version id.
                Example: {version_id: (local_status, remote_status)}

        """
        version_ids = list(version_ids)
        payload_dict = {
            "localSite": local_site_name,
            "remoteSite": remote_site_name,
            "versionIdsFilter": version_ids
        }
        payload_dict.update(kwargs)

        endpoint = "{}/{}/state".format(
            self.endpoint_prefix, project_name
        )

        response = ayon_api.get(endpoint, **payload_dict)
        if response.status_code != 200:
            raise RuntimeError(
                "Cannot get sync state for versions {}".format(
                    version_ids
                )
            )

        version_statuses = {
            version_id: (0, 0)
            for version_id in version_ids
        }

        repre_avail_by_version_id = defaultdict(list)
        for repre_avail in response.data["representations"]:
            version_id = repre_avail["versionId"]
            repre_avail_by_version_id[version_id].append(repre_avail)

        for version_id, repre_avails in repre_avail_by_version_id.items():
            avail_local = sum(
                int(
                    repre_avail["localStatus"]["status"] == SiteSyncStatus.OK
                )
                for repre_avail in repre_avails
            )
            avail_remote = sum(
                int(
                    repre_avail["remoteStatus"]["status"] == SiteSyncStatus.OK
                )
                for repre_avail in repre_avails
            )
            version_statuses[version_id] = (avail_local, avail_remote)

        return version_statuses

    def _remove_local_file(self, project_name, representation_id, site_name):
        """Removes all local files for 'site_name' of 'representation_id'

        Args:
            project_name (str): Project name.
            representation_id (str): Representation id.
            site_name (str): name of configured and active site

        """
        my_local_site = get_local_site_id()
        if my_local_site != site_name:
            self.log.warning(
                "Cannot remove non local file for {}".format(site_name)
            )
            return

        provider_name = self.get_provider_for_site(site=site_name)

        if provider_name != "local_drive":
            return

        representation = get_representation_by_id(
            project_name, representation_id
        )
        if not representation:
            self.log.debug(
                "Representation with id {} was not found".format(
                    representation_id
                )
            )
            return

        for file in representation["files"]:
            local_file_path = self.get_local_file_path(
                project_name,
                site_name,
                file.get("path")
            )
            if local_file_path is None:
                raise ValueError("Missing local file path")

            try:
                self.log.debug("Removing {}".format(local_file_path))
                os.remove(local_file_path)
            except IndexError:
                msg = "No file set for {}".format(representation_id)
                self.log.debug(msg)
                raise ValueError(msg)
            except OSError:
                msg = "File {} cannot be removed".format(file["path"])
                self.log.warning(msg)
                raise ValueError(msg)

            folder = os.path.dirname(local_file_path)
            if os.listdir(folder):  # folder is not empty
                continue

            try:
                os.rmdir(folder)
            except OSError:
                msg = "folder {} cannot be removed".format(folder)
                self.log.warning(msg)
                raise ValueError(msg)

    def reset_timer(self):
        """
            Called when waiting for next loop should be skipped.

            In case of user's involvement (reset site), start that right away.
        """

        if not self.enabled:
            return

        if self.sitesync_thread is None:
            self._reset_timer_with_rest_api()
        else:
            self.sitesync_thread.reset_timer()

    def get_loop_delay(self, project_name):
        """
            Return count of seconds before next synchronization loop starts
            after finish of previous loop.

        Returns:
            (int): in seconds
        """
        if not project_name:
            return 60

        # TODO this is used in global loop it should not be based on
        #   project settings.
        ld = self.sync_project_settings[project_name]["config"]["loop_delay"]
        return int(ld)

    def cli(self, click_group):
        main = click_wrap.group(
            self._cli_main,
            name=self.name,
            help="SiteSync addon related commands."
        )

        main.command(
            self._cli_command_syncservice,
            name="syncservice",
            help="Launch Site Sync under entered site."
        ).option(
            "-a",
            "--active_site",
            help="Name of active site",
            required=True
        )
        click_group.add_command(main.to_click_obj())

    def _cli_main(self):
        pass

    def _cli_command_syncservice(self, active_site):
        """Launch sync server under entered site.

        This should be ideally used by system service (such us systemd or upstart
        on linux and window service).
        """

        os.environ["AYON_SITE_ID"] = active_site

        def signal_handler(sig, frame):
            print("You pressed Ctrl+C. Process ended.")
            self.server_exit()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        self.server_init()
        self.server_start()

        while True:
            time.sleep(1.0)
