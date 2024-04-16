import os
import sys
import time
from datetime import datetime
import threading
import copy
import signal
from collections import deque, defaultdict
import click

from ayon_core.settings import get_studio_settings
from ayon_core.addon import AYONAddon, ITrayAddon, IPluginPaths
from ayon_common.utils import get_local_site_id
from ayon_core.pipeline import Anatomy

from ayon_api import (
    get,
    post,
    delete,
    get_representation_by_id,
    get_representations,
    get_project_names,
    get_addon_project_settings,
    get_project_roots_for_site
)

from .version import __version__
from .providers.local_drive import LocalDriveHandler

from .utils import (
    time_function,
    SyncStatus,
    SiteAlreadyPresentError,
    SiteSyncStatus,
    SITE_SYNC_ROOT
)

SYNC_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


class SiteSyncAddon(AYONAddon, ITrayAddon, IPluginPaths):
    """
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
    DEFAULT_SITE = 'studio'
    LOCAL_SITE = 'local'
    LOG_PROGRESS_SEC = 5  # how often log progress to DB
    DEFAULT_PRIORITY = 50  # higher is better, allowed range 1 - 1000

    name = "sitesync"
    version = __version__

    def initialize(self, module_settings):
        """
            Called during Module Manager creation.

            Collects needed data, checks asyncio presence.
            Sets 'enabled' according to global settings for the module.
            Shouldnt be doing any initialization, thats a job for 'tray_init'
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
        return {"publish": os.path.join(SYNC_ADDON_DIR, "plugins", "publish")}

    def get_site_icons(self):
        """Icons for sites.

        Returns:
            dict[str, str]: Path to icon by site.
        """

        resource_path = os.path.join(
            SITE_SYNC_ROOT, "providers", "resources"
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
            (str): full absolut path to directory with hooks for the module
        """

        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "launch_hooks"
        )

    """ Start of Public API """
    def add_site(self, project_name, representation_id, site_name=None,
                 file_id=None, force=False, status=SiteSyncStatus.QUEUED):
        """
        Adds new site to representation to be synced.

        'project_name' must have synchronization enabled (globally or
        project only)

        Used as a API endpoint from outside applications (Loader etc).

        Use 'force' to reset existing site.

        Args:
            project_name (string): project name (must match DB)
            representation_id (string): MongoDB _id value
            site_name (string): name of configured and active site
            file_id (uuid): add file to site info
            force (bool): reset site if exists
            status (SiteSyncStatus): current status,
                default SiteSyncStatus.QUEUED

        Throws:
            SiteAlreadyPresentError - if adding already existing site and
                not 'force'
            ValueError - other errors (repre not found, misconfiguration)
        """
        if not self.get_sync_project_setting(project_name):
            raise ValueError("Project not configured")

        if not site_name:
            site_name = self.DEFAULT_SITE

        representation = get_representation_by_id(project_name,
                                                  representation_id)

        files = representation.get("files", [])
        if not files:
            self.log.debug("No files for {}".format(representation_id))
            return

        if not force:
            existing = self.get_repre_sync_state(project_name,
                                           [representation_id],
                                           site_name)
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

        new_site_files = []
        for repre_file in files:
            new_site_files.append({
                "size": repre_file["size"],
                "status": status,
                "timestamp": datetime.now().timestamp(),
                "id": repre_file["id"],
                "fileHash": repre_file["hash"]
            })

        payload_dict = {"files": new_site_files}
        representation_id = representation_id.replace("-", '')

        self._set_state_sync_state(project_name, representation_id, site_name,
                                   payload_dict)

    def remove_site(self, project_name, representation_id, site_name,
                    remove_local_files=False):
        """
            Removes 'site_name' for particular 'representation_id' on
            'project_name'

            Args:
                project_name (string): project name (must match DB)
                representation_id (string): MongoDB _id value
                site_name (string): name of configured and active site
                remove_local_files (bool): remove only files for 'local_id'
                    site

            Returns:
                throws ValueError if any issue
        """
        if not self.get_sync_project_setting(project_name):
            raise ValueError("Project not configured")

        sync_info = self.get_repre_sync_state(project_name, [representation_id],
                                              site_name)
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


        response = delete(endpoint)
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
            metadata = {"name": name}
            if created:
                metadata["status"] = SiteSyncStatus.OK
            else:
                metadata["status"] = SiteSyncStatus.QUEUED
            return metadata

        if (not self.sync_studio_settings["enabled"] or
                not self.sync_project_settings[project_name]["enabled"]):
            return [create_metadata(self.DEFAULT_SITE)]

        local_site = self.get_active_site(project_name)
        remote_site = self.get_remote_site(project_name)

        # Attached sites metadata by site name
        # That is the local site, remote site, the always accesible sites
        # and their alternate sites (alias of sites with different protocol)
        attached_sites = dict()
        attached_sites[local_site] = create_metadata(local_site)

        if remote_site and remote_site not in attached_sites:
            attached_sites[remote_site] = create_metadata(remote_site,
                                                          created=False)

        attached_sites = self._add_alternative_sites(attached_sites)
        # add skeleton for sites where it should be always synced to
        # usually it would be a backup site which is handled by separate
        # background process
        for site in self._get_always_accessible_sites(project_name):
            if site not in attached_sites:
                attached_sites[site] = create_metadata(site, created=False)

        return list(attached_sites.values())

    def _get_always_accessible_sites(self, project_name):
        """Sites that synced to as a part of background process.

        Artist machine doesn't handle those, explicit Tray with that site name
        as a local id must be running.
        Example is dropbox site serving as a backup solution
        """
        always_accessible_sites = (
            self.get_sync_project_setting(project_name)["config"].
            get("always_accessible_on", [])
        )
        return [site.strip() for site in always_accessible_sites]

    def _add_alternative_sites(self, attached_sites):
        """Add skeleton document for alternative sites

        Each new configured site in System Setting could serve as a alternative
        site, it's a kind of alias. It means that files on 'a site' are
        physically accessible also on 'a alternative' site.
        Example is sftp site serving studio files via sftp protocol, physically
        file is only in studio, sftp server has this location mounted.
        """
        additional_sites = self._transform_sites_from_settings(
            self.sync_studio_settings)

        alt_site_pairs = self._get_alt_site_pairs(additional_sites)

        for site_name in additional_sites.keys():
            # Get alternate sites (stripped names) for this site name
            alt_sites = alt_site_pairs.get(site_name)
            alt_sites = [site.strip() for site in alt_sites]
            alt_sites = set(alt_sites)

            # If no alternative sites we don't need to add
            if not alt_sites:
                continue

            # Take a copy of data of the first alternate site that is already
            # defined as an attached site to match the same state.
            match_meta = next((attached_sites[site] for site in alt_sites
                               if site in attached_sites), None)
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
            (dict): {'site': [alternative sites]...}
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
            project_name (string): project name
            site_name (string): active site name
            reset_missing (bool): if True reset site in DB if missing
                physically
        """
        self.log.debug("Validation of {} for {} started".format(project_name,
                                                                site_name))
        representations = list(get_representations(project_name))
        if not representations:
            self.log.debug("No repre found")
            return

        sites_added = 0
        sites_reset = 0
        for repre in representations:
            repre_id = repre["_id"]
            for repre_file in repre.get("files", []):
                try:
                    is_on_site = site_name in [site["name"]
                                               for site in repre_file["sites"]
                                               if (site.get("created_dt") and
                                               not site.get("error"))]
                except (TypeError, AttributeError):
                    self.log.debug("Structure error in {}".format(repre_id))
                    continue

                file_path = repre_file.get("path", "")
                local_file_path = self.get_local_file_path(project_name,
                                                           site_name,
                                                           file_path)

                file_exists = (local_file_path and
                               os.path.exists(local_file_path))
                if not is_on_site:
                    if file_exists:
                        self.log.debug(
                            "Adding site {} for {}".format(site_name,
                                                           repre_id))

                        created_dt = datetime.fromtimestamp(
                            os.path.getmtime(local_file_path))
                        self.add_site(project_name, repre,
                                      site_name=site_name,
                                      file_id=repre_file["_id"],
                                      force=True)
                        sites_added += 1
                else:
                    if not file_exists and reset_missing:
                        self.log.debug("Resetting site {} for {}".
                                       format(site_name, repre_id))
                        self.reset_site_on_representation(
                            project_name, repre_id, site_name=site_name,
                            file_id=repre_file["_id"])
                        sites_reset += 1

        if sites_added % 100 == 0:
            self.log.debug("Sites added {}".format(sites_added))

        self.log.debug("Validation of {} for {} ended".format(project_name,
                                                              site_name))
        self.log.info("Sites added {}, sites reset {}".format(sites_added,
                                                              reset_missing))

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_representation(self, project_name, representation_id, site_name):
        """
            Sets 'representation_id' as paused, eg. no syncing should be
            happening on it.

            Args:
                project_name (string): project name
                representation_id (string): MongoDB objectId value
                site_name (string): 'gdrive', 'studio' etc.
        """
        self.log.info("Pausing SyncServer for {}".format(representation_id))
        self._paused_representations.add(representation_id)
        representation = get_representation_by_id(project_name,
                                                  representation_id)
        self.update_db(project_name, representation, site_name, pause=True)

    # TODO hook to some trigger - no Sync Queue anymore
    def unpause_representation(self, project_name,
                               representation_id, site_name):
        """
            Sets 'representation_id' as unpaused.

            Does not fail or warn if repre wasn't paused.

            Args:
                project_name (string): project name
                representation_id (string): MongoDB objectId value
                site_name (string): 'gdrive', 'studio' etc.
        """
        self.log.info("Unpausing SyncServer for {}".format(representation_id))
        try:
            self._paused_representations.remove(representation_id)
        except KeyError:
            pass
        # self.paused_representations is not persistent
        representation = get_representation_by_id(project_name,
                                                  representation_id)
        self.update_db(project_name, representation, site_name, pause=False)

    def is_representation_paused(self, representation_id,
                                 check_parents=False, project_name=None):
        """
            Returns if 'representation_id' is paused or not.

            Args:
                representation_id (string): MongoDB objectId value
                check_parents (bool): check if parent project or server itself
                    are not paused
                project_name (string): project to check if paused

                if 'check_parents', 'project_name' should be set too
            Returns:
                (bool)
        """
        condition = representation_id in self._paused_representations
        if check_parents and project_name:
            condition = condition or \
                self.is_project_paused(project_name) or \
                self.is_paused()
        return condition

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_project(self, project_name):
        """
            Sets 'project_name' as paused, eg. no syncing should be
            happening on all representation inside.

            Args:
                project_name (string): project_name name
        """
        self.log.info("Pausing SyncServer for {}".format(project_name))
        self._paused_projects.add(project_name)

    # TODO hook to some trigger - no Sync Queue anymore
    def unpause_project(self, project_name):
        """
            Sets 'project_name' as unpaused

            Does not fail or warn if project wasn't paused.

            Args:
                project_name (string):
        """
        self.log.info("Unpausing SyncServer for {}".format(project_name))
        try:
            self._paused_projects.remove(project_name)
        except KeyError:
            pass

    def is_project_paused(self, project_name, check_parents=False):
        """
            Returns if 'project_name' is paused or not.

            Args:
                project_name (string):
                check_parents (bool): check if server itself
                    is not paused
            Returns:
                (bool)
        """
        condition = project_name in self._paused_projects
        if check_parents:
            condition = condition or self.is_paused()
        return condition

    # TODO hook to some trigger - no Sync Queue anymore
    def pause_server(self):
        """
            Pause sync server

            It won't check anything, not uploading/downloading...
        """
        self.log.info("Pausing SyncServer")
        self._paused = True

    def unpause_server(self):
        """
            Unpause server
        """
        self.log.info("Unpausing SyncServer")
        self._paused = False

    def is_paused(self):
        """ Is server paused """
        return self._paused

    def get_active_site(self, project_name):
        """
            Returns active (mine) site for 'project_name' from settings

            Returns:
                (string) ['studio' - if Site Sync disabled
                          get_local_site_id - if 'local'
                          any other site name from local settings or
                          project settings (site could be forced from PS)
        """
        active_site_type = self.get_active_site_type(project_name)
        if active_site_type == self.LOCAL_SITE:
            return get_local_site_id()
        return active_site_type

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

        active_site = (
                sync_project_settings["local_setting"].get("active_site") or
                sync_project_settings["config"]["active_site"])

        return active_site

    # remote site
    def get_remote_site(self, project_name):
        """
            Returns remote (theirs) site for 'project_name' from settings
        """
        sync_project_settings = self.get_sync_project_setting(project_name)
        remote_site = (
                sync_project_settings["local_setting"].get("remote_site") or
                sync_project_settings["config"]["remote_site"])
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
        local_project_settings = sitesync_settings["local_setting"]
        if site_name == "local":
            for root_info in local_project_settings["local_roots"]:
                roots[root_info["name"]] = root_info["path"]

        return roots

    def get_local_normalized_site(self, site_name):
        """
            Return 'site_name' or 'local' if 'site_name' is local id.

            In some places Settings or Local Settings require 'local' instead
            of real site name.
        """
        if site_name == get_local_site_id():
            site_name = self.LOCAL_SITE

        return site_name

    def is_representation_on_site(
        self, project_name, representation_id, site_name, max_retries=None
    ):
        """Checks if 'representation_id' has all files avail. on 'site_name'

        Args:
            project_name (str)
            representation_id (str)
            site_name (str)
            max_retries (int) (optional) - provide only if method used in while
                loop to bail out
        Returns:
            (bool): True if 'representation_id' has all files correctly on the
            'site_name'
        Raises:
              (ValueError)  Only If 'max_retries' provided if upload/download
        failed too many times to limit infinite loop check.
        """
        representation = get_representation_by_id(
            project_name, representation_id, fields=["_id", "files"])
        if not representation:
            return False

        on_site = False
        for file_info in representation.get("files", []):
            for site in file_info.get("sites", []):
                if site["name"] != site_name:
                    continue

                if max_retries:
                    tries = self._get_tries_count_from_rec(site)
                    if tries >= max_retries:
                        raise ValueError("Failed too many times")

                if (site.get("progress") or site.get("error") or
                        not site.get("created_dt")):
                    return False
                on_site = True

        return on_site

    def _reset_timer_with_rest_api(self):
        # POST to webserver sites to add to representations
        webserver_url = os.environ.get("OPENPYPE_WEBSERVER_URL")
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
        """Returns list of projects which have SyncServer enabled."""
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
                project_settings = get_addon_project_settings(self.name,
                                                              self.version,
                                                              project_name)
            else:
                project_settings = self.get_sync_project_setting(project_name)
            if project_settings and project_settings.get("enabled"):
                return True
        return False

    def handle_alternate_site(self, project_name, representation_id,
                              processed_site, file_id):
        """
            For special use cases where one site vendors another.

            Current use case is sftp site vendoring (exposing) same data as
            regular site (studio). Each site is accessible for different
            audience. 'studio' for artists in a studio, 'sftp' for externals.

            Change of file status on one site actually means same change on
            'alternate' site. (eg. artists publish to 'studio', 'sftp' is using
            same location >> file is accesible on 'sftp' site right away.

            Args:
                project_name (str): name of project
                representation_id (uuid)
                processed_site (str): real site_name of published/uploaded file
                file_id (uuid): DB id of file handled
        """
        sites = self._transform_sites_from_settings(self.sync_studio_settings)
        sites[self.DEFAULT_SITE] = {"provider": "local_drive",
                                    "alternative_sites": []}

        alternate_sites = []
        for site_name, site_info in sites.items():
            conf_alternative_sites = site_info.get("alternative_sites", [])
            if processed_site in conf_alternative_sites:
                alternate_sites.append(site_name)
                continue
            if processed_site == site_name and conf_alternative_sites:
                alternate_sites.extend(conf_alternative_sites)
                continue

        sync_state = self.get_repre_sync_state(project_name,
                                               [representation_id],
                                               processed_site)
        if not sync_state:
            raise RuntimeError("Cannot find repre with '{}".format(representation_id))  # noqa
        payload_dict = {"files": sync_state["files"]}

        alternate_sites = set(alternate_sites)
        for alt_site in alternate_sites:
            self.log.debug("Adding alternate {} to {}".format(
                alt_site, representation_id))
            self._set_state_sync_state(project_name, representation_id,
                                       site_name,
                                       payload_dict)

    # TODO - for Loaders
    def get_repre_info_for_versions(self, project_name, version_ids,
                                    active_site, remote_site):
        """Returns representation documents for versions and sites combi

        Args:
            project_name (str)
            version_ids (list): of version[_id]
            active_site (string): 'local', 'studio' etc
            remote_site (string): dtto
        Returns:

        """
        endpoint = "{}/projects/{}/sitesync/state".format(self.endpoint_prefix,
                                                          project_name)

        # get to upload
        kwargs = {"localSite": active_site,
                  "remoteSite": remote_site,
                  "versionIdFilter": version_ids}

        # kwargs["representationId"] = "94dca33a-7705-11ed-8c0a-34e12d91d510"

        response = get(endpoint, **kwargs)
        representations = response.data.get("representations", [])
        repinfo_by_version_id = defaultdict(dict)
        for repre in representations:
            version_id = repre["versionId"]
            repre_info = repinfo_by_version_id.get(version_id)
            if repre_info:
                repinfo_by_version_id[version_id]["repre_count"] += 1
                repinfo_by_version_id[version_id]["avail_repre_local"] += \
                    self._is_available(repre, "localStatus")
                repinfo_by_version_id[version_id]["avail_repre_remote"] += \
                    self._is_available(repre, "remoteStatus")
            else:
                repinfo_by_version_id[version_id] = {
                    "_id": version_id,
                    "repre_count": 1,
                    'avail_repre_local': self._is_available(repre,
                                                            "localStatus"),
                    'avail_repre_remote': self._is_available(repre,
                                                             "remoteStatus"),
                }

        return repinfo_by_version_id.values()
    """ End of Public API """

    def _is_available(self, repre, status):
        """Helper to decide if repre is download/uploaded on site"""
        return int(repre[status]["status"] == SiteSyncStatus.OK)

    def get_local_file_path(self, project_name, site_name, file_path):
        """
            Externalized for app
        """
        handler = LocalDriveHandler(project_name, site_name)
        local_file_path = handler.resolve_path(file_path)

        return local_file_path

    def tray_init(self):
        """
            Actual initialization of Sync Server for Tray.

            Called when tray is initialized, it checks if module should be
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
        """
            Triggered when Tray is started.

            Checks if configuration presets are available and if there is
            any provider ('gdrive', 'S3') that is activated
            (eg. has valid credentials).

        Returns:
            None
        """
        self.server_start()

    def server_start(self):
        if self.enabled:
            self.sitesync_thread.start()
        else:
            self.log.info("No presets or active providers. " +
                     "Synchronization not possible.")

    def tray_exit(self):
        """
            Stops sync thread if running.

            Called from Module Manager
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
        """
            Get already created or newly created anatomy for project

            Args:
                project_name (string):

            Return:
                (Anatomy)
        """
        return self._anatomies.get('project_name') or Anatomy(project_name)

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
            proj_settings = get_addon_project_settings(
                self.name, self.version, project_name)

            project_sites.update(self._get_default_site_configs(
                proj_settings["enabled"], project_name, proj_settings
            ))

            project_sites.update(
                self._transform_sites_from_settings(proj_settings))

            proj_settings["sites"] = project_sites

            sync_project_settings[project_name] = proj_settings
        if not sync_project_settings:
            self.log.info("No enabled and configured projects for sync.")
        return sync_project_settings

    def get_sync_project_setting(self, project_name, exclude_locals=False,
                                 cached=True):
        """ Handles pulling sitesync's settings for enabled 'project_name'

            Args:
                project_name (str): used in project settings
                exclude_locals (bool): ignore overrides from Local Settings
                cached (bool): use pre-cached values, or return fresh ones
                    cached values needed for single loop (with all overrides)
                    fresh values needed for Local settings (without overrides)
            Returns:
                (dict): settings dictionary for the enabled project,
                    empty if no settings or sync is disabled
        """
        # presets set already, do not call again and again
        # self.log.debug("project preset {}".format(self.presets))
        if not cached:
            return self._prepare_sync_project_settings(exclude_locals)\
                [project_name]

        if not self.sync_project_settings or \
               not self.sync_project_settings.get(project_name):
            self.set_sync_project_settings(exclude_locals)

        return self.sync_project_settings.get(project_name)

    def _transform_sites_from_settings(self, settings):
        """Transforms list of 'sites' from Setting to dict.

        It processes both System and Project Settings as they have same format.
        """
        sites = {}
        if self.enabled:
            for whole_site_info in settings.get("sites", []):
                configured_site = {}
                site_name = whole_site_info["name"]
                configured_site["enabled"] = True

                provider_specific = whole_site_info[whole_site_info["provider"]]
                configured_site["root"] = provider_specific.pop("roots",
                                                                None)
                configured_site.update(provider_specific)

                sites[site_name] = configured_site
        return sites

    def _get_default_site_configs(self, sync_enabled=True, project_name=None,
                                  proj_settings=None):
        """
            Returns settings for 'studio' and user's local site

            Returns base values from setting, not overridden by Local Settings,
            eg. value used to push TO LS not to get actual value for syncing.
        """
        local_site_id = get_local_site_id()
        # overrides for Studio site for particular user
        roots = get_project_roots_for_site(project_name, local_site_id)
        studio_config = {
            'enabled': True,
            'provider': 'local_drive',
            "root": roots
        }
        all_sites = {self.DEFAULT_SITE: studio_config}
        if sync_enabled:
            roots = proj_settings["local_setting"]["local_roots"]
            local_site_dict = {"enabled": True,
                               "provider": "local_drive",
                               "root": roots}
            all_sites[local_site_id] = local_site_dict
            # duplicate values for normalized local name
            all_sites["local"] = local_site_dict
        return all_sites

    def get_provider_for_site(self, project_name=None, site=None):
        """
            Return provider name for site (unique name across all projects.
        """
        sites = {self.DEFAULT_SITE: "local_drive",
                 self.LOCAL_SITE: "local_drive",
                 get_local_site_id(): "local_drive"}

        if site in sites.keys():
            return sites[site]

        if project_name:  # backward compatibility
            proj_settings = self.get_sync_project_setting(project_name)
            provider = proj_settings.get("sites", {}).get(site, {}).\
                get("provider")
            if provider:
                return provider

        sync_sett = self.sync_studio_settings
        for site_config in sync_sett.get("sites"):
            sites[site_config["name"]] = site_config["provider"]

        return sites.get(site, 'N/A')

    @time_function
    def get_sync_representations(self, project_name, active_site, remote_site,
                                 limit=10):
        """
            Get representations that should be synced, these could be
            recognised by presence of document in 'files.sites', where key is
            a provider (GDrive, S3) and value is empty document or document
            without 'created_dt' field. (Don't put null to 'created_dt'!).

            Querying of 'to-be-synched' files is offloaded to Mongod for
            better performance. Goal is to get as few representations as
            possible.
        Args:
            project_name (string):
            active_site (string): identifier of current active site (could be
                'local_0' when working from home, 'studio' when working in the
                studio (default)
            remote_site (string): identifier of remote site I want to sync to

        Returns:
            (list) of dictionaries
        """
        self.log.debug("Check representations for: {}-{}".format(active_site,
                                                                 remote_site))

        endpoint = "{}/{}/state".format(self.endpoint_prefix, project_name) # noqa

        # get to upload
        kwargs = {"localSite": active_site,
                  "remoteSite": remote_site,
                  "localStatusFilter": [SiteSyncStatus.OK],
                  "remoteStatusFilter": [SiteSyncStatus.QUEUED,
                                         SiteSyncStatus.FAILED]}

        response = get(endpoint, **kwargs)
        if response.status_code not in [200, 204]:
            raise RuntimeError(
                "Cannot get representations for sync with code {}"
                .format(response.status_code))
        representations = response.data["representations"]

        # get to download
        if len(representations) < limit:
            kwargs["localStatusFilter"] = [SiteSyncStatus.QUEUED,
                                           SiteSyncStatus.FAILED]
            kwargs["remoteStatusFilter"] = [SiteSyncStatus.OK]

            response = get(endpoint, **kwargs)
            representations.extend(response.data["representations"])

        return representations

    def check_status(self, file, local_site, remote_site, config_preset):
        """
            Check synchronization status for single 'file' of single
            'representation' by single 'provider'.
            (Eg. check if 'scene.ma' of lookdev.v10 should be synced to GDrive

            Always is comparing local record, eg. site with
            'name' == self.presets[PROJECT_NAME]['config']["active_site"]

            This leads to trigger actual upload or download, there is
            a use case 'studio' <> 'remote' where user should publish
            to 'studio', see progress in Tray GUI, but do not do
            physical upload/download
            (as multiple user would be doing that).

            Do physical U/D only when any of the sites is user's local, in that
            case only user has the data and must U/D.

        Args:
            file (dictionary):  of file from representation in Mongo
            local_site (string):  - local side of compare (usually 'studio')
            remote_site (string):  - gdrive etc.
            config_preset (dict): config about active site, retries
        Returns:
            (string) - one of SyncStatus
        """
        if get_local_site_id() not in (local_site, remote_site):
            # don't do upload/download for studio sites
            self.log.debug(
                "No local site {} - {}".format(local_site, remote_site)
            )
            return SyncStatus.DO_NOTHING

        local_status = file["localStatus"]["status"]
        remote_status = file["remoteStatus"]["status"]

        if (local_status != SiteSyncStatus.OK and
                remote_status == SiteSyncStatus.OK):
            retries = file["localStatus"]["retries"]
            if retries < int(config_preset["retry_cnt"]):
                return SyncStatus.DO_DOWNLOAD

        if (remote_status != SiteSyncStatus.OK and
                local_status == SiteSyncStatus.OK):
            retries = file["remoteStatus"]["retries"]
            if retries < int(config_preset["retry_cnt"]):
                return SyncStatus.DO_UPLOAD

        return SyncStatus.DO_NOTHING

    def update_db(self, project_name, representation, site_name,
                  new_file_id=None, file=None,
                  side=None, error=None, progress=None, priority=None,
                  pause=None):
        """
            Update 'provider' portion of records in DB with success (file_id)
            or error (exception)

        Args:
            project_name (string): name of project - force to db connection as
              each file might come from different collection
            new_file_id (string):
            file (dictionary): info about processed file (pulled from DB)
            representation (dict): representation from DB
            site_name (str):
            side (string): 'local' | 'remote'
            error (string): exception message
            progress (float): 0-1 of progress of upload/download
            priority (int): 0-100 set priority
            pause (bool): stop synchronizing (only before starting of download,
                upload)

        Returns:
            None
        """
        files_status = []
        for file_info in representation["files"]:
            status_doc = copy.deepcopy(file_info["{}Status".format(side)])
            status_doc["fileHash"] = file_info["fileHash"]
            status_doc["id"] = file_info["id"]
            if file_info["fileHash"] == file["fileHash"]:
                if new_file_id:
                    status_doc["status"] = SiteSyncStatus.OK
                    status_doc.pop("message")
                    status_doc.pop("retries")
                elif progress is not None:
                    status_doc["status"] = SiteSyncStatus.IN_PROGRESS
                    status_doc["progress"] = progress
                elif error:
                    status_doc["status"] = SiteSyncStatus.FAILED
                    tries = status_doc.get("retries", 0)
                    tries += 1
                    status_doc["retries"] = tries
                    status_doc["message"] = error
                elif pause is not None:
                    if pause:
                        status_doc["pause"] = True
                    else:
                        status_doc.remove("pause")
            files_status.append(status_doc)

        representation_id = representation["representationId"]

        endpoint = "{}/{}/state/{}/{}".format(self.endpoint_prefix, project_name, representation_id, site_name)  # noqa

        # get to upload
        kwargs = {
            "files": files_status
        }

        if priority:
            kwargs["priority"] = priority

        response = post(endpoint, **kwargs)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

        if progress is not None or priority is not None:
            return

        status = 'failed'
        error_str = 'with error {}'.format(error)
        if new_file_id:
            status = 'succeeded with id {}'.format(new_file_id)
            error_str = ''

        source_file = file.get("path", "")

        self.log.debug(
            (
                "File for {} - {source_file} process {status} {error_str}"
            ).format(
                representation_id,
                status=status,
                source_file=source_file,
                error_str=error_str
            )
        )

    def reset_site_on_representation(self, project_name, representation_id,
                                     side=None, file_id=None, site_name=None):
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
            project_name (string): name of project (eg. collection) in DB
            representation_id(string): _id of representation
            file_id (string):  file _id in representation
            side (string): local or remote side
            site_name (string): for adding new site

        Raises:
            SiteAlreadyPresentError - if adding already existing site and
                not 'force'
            ValueError - other errors (repre not found, misconfiguration)
        """
        representation = get_representation_by_id(project_name,
                                                  representation_id)
        if not representation:
            raise ValueError("Representation {} not found in {}".
                             format(representation_id, project_name))

        if side and site_name:
            raise ValueError("Misconfiguration, only one of side and " +
                             "site_name arguments should be passed.")

        local_site = self.get_active_site(project_name)
        remote_site = self.get_remote_site(project_name)

        if side:
            if side == 'local':
                site_name = local_site
            else:
                site_name = remote_site

        self.add_site(project_name, representation_id, site_name, file_id,
                      force=True)

    def remove_site(self, project_name, representation, site_name):
        """
            Removes 'site_name' for 'representation' if present.
        """
        representation_id = representation["_id"]
        sync_info = self.get_repre_sync_state(project_name, [representation_id],
                                              site_name)
        if not sync_info:
            msg = "Site {} not found".format(site_name)
            self.log.warning(msg)
            return

        endpoint = "{}/{}/state/{}/{}".format(self.endpoint_prefix, project_name, representation_id, site_name)  # noqa

        response = delete(endpoint)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

    def get_progress_for_repre(self, representation,
                               local_site_name, remote_site_name=None):
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
        project_name = representation["context"]["project"]["name"]
        representation_id = representation["_id"]
        sync_status = self.get_repre_sync_state(project_name, [representation_id],
                                                local_site_name, remote_site_name)

        progress = {local_site_name: -1,
                    remote_site_name: -1}
        if not sync_status:
            return progress

        mapping = {"localStatus": local_site_name,
                   "remoteStatus": remote_site_name}
        files = {local_site_name: 0, remote_site_name: 0}
        doc_files = sync_status.get("files") or []
        for doc_file in doc_files:
            for status in mapping.keys():
                status_info = doc_file[status]
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
        avg_progress = {}
        avg_progress[local_site_name] = \
            progress[local_site_name] / max(files[local_site_name], 1)
        avg_progress[remote_site_name] = \
            progress[remote_site_name] / max(files[remote_site_name], 1)
        return avg_progress

    def _set_state_sync_state(self, project_name, representation_id, site_name,
                              payload_dict):
        """Calls server endpoint to store sync info for 'representation_id'."""
        endpoint = "{}/{}/state/{}/{}".format(self.endpoint_prefix, project_name, representation_id, site_name)  # noqa

        response = post(endpoint, **payload_dict)
        if response.status_code not in [200, 204]:
            raise RuntimeError("Cannot update status")

    def get_repre_sync_state(self, project_name, representation_ids, local_site_name,
                             remote_site_name=None, **kwargs):
        """Use server endpoint to get synchronization info for repre_id(s).

        Args:
            project_name (str):
            representation_ids (list): even single repre should be in []
            local_site_name (str)
            remote_site_name (str)
            all other parameters for `Get Site Sync State` endpoint if
                necessary
        """
        representations = self._get_repres_state(project_name,
                                                 representation_ids,
                                                 local_site_name,
                                                 remote_site_name,
                                                 **kwargs)
        if representations:
            representation = representations[0]
            if representation["localStatus"]["status"] != -1:
                return representation

    def get_representations_sync_state(self, project_name, representation_ids,
                                       local_site_name,remote_site_name=None,
                                       **kwargs):
        """Use server endpoint to get synchronization info for representations.

        Calculates float progress based on progress of all files for repre.
        If repre is fully synchronized it returns 1, 0 for any other state.

        Args:
            project_name (str):
            representation_ids (list): even single repre should be in []
            local_site_name (str)
            remote_site_name (str)
            all other parameters for `Get Site Sync State` endpoint if
                necessary
        Returns:
            (dict) {repre_id: (float, float)}
        """
        representations = self._get_repres_state(project_name,
                                                 representation_ids,
                                                 local_site_name,
                                                 remote_site_name,
                                                 **kwargs)
        states = {}
        repre_local_progress = repre_remote_progress = 0
        no_of_files = 0
        for repre in representations:
            for file_info in repre["files"]:
                no_of_files += 1
                repre_local_progress += file_info["localStatus"].get("progress", 0)
                repre_remote_progress += file_info["remoteStatus"].get("progress", 0)

            repre_local_status = repre["localStatus"]["status"]
            if repre_local_status == SiteSyncStatus.OK:
                repre_local_progress = 1
            elif repre_local_status == SiteSyncStatus.IN_PROGRESS:
                repre_local_progress = repre_local_progress / no_of_files
            else:
                repre_local_progress = 0

            repre_remote_status = repre["remoteStatus"]["status"]
            if repre_remote_status == SiteSyncStatus.OK:
                repre_remote_progress = 1
            elif repre_remote_status == SiteSyncStatus.IN_PROGRESS:
                repre_remote_progress = repre_remote_progress / no_of_files
            else:
                repre_remote_progress = 0

            states[repre["representationId"]] = (repre_local_progress,
                                                 repre_remote_progress)

        return states
            
    def _get_repres_state(self, project_name, representation_ids, local_site_name,
                          remote_site_name=None, **kwargs):
        """Use server endpoint to get synchronization info for representations.

        Args:
            project_name (str):
            representation_ids (list): even single repre should be in []
            local_site_name (str)
            remote_site_name (str)
            all other parameters for `Get Site Sync State` endpoint if
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

        endpoint = "{}/{}/state".format(self.endpoint_prefix, project_name)  # noqa

        response = get(endpoint, **payload_dict)
        if response.status_code != 200:
            msg = "Cannot get sync state for representation ".format(representation_id)  # noqa
            raise RuntimeError(msg)

        return response.data["representations"]
    
    def get_version_availability(self, project_name, version_ids, local_site_name,
                                 remote_site_name, **kwargs):
        """Returns aggregate state for version_ids

        Returns:
            (dict): {version_id: (local_status, remote_status)}
        """
        payload_dict = {
            "localSite": local_site_name,
            "remoteSite": remote_site_name,
            "versionIdsFilter": version_ids
        }
        if kwargs:
            payload_dict.update(kwargs)

        endpoint = "{}/{}/state".format(self.endpoint_prefix, project_name)  # noqa

        response = get(endpoint, **payload_dict)
        if response.status_code != 200:
            msg = "Cannot get sync state for representation ".format(representation_id)  # noqa
            raise RuntimeError(msg)

        version_statuses = defaultdict(tuple)
        dummy_tuple = (0, 0)
        for repre in response.data["representations"]:
            version_id = repre["versionId"]
            avail_local = repre["localStatus"]["status"] == SiteSyncStatus.OK
            avail_remote = repre["remoteStatus"]["status"] == SiteSyncStatus.OK
            version_statuses[version_id] = (
                version_statuses.get(version_id, dummy_tuple)[0] + int(avail_local),
                version_statuses.get(version_id, dummy_tuple)[1] + int(avail_remote)
            )

        return version_statuses

    def _remove_local_file(self, project_name, representation_id, site_name):
        """
            Removes all local files for 'site_name' of 'representation_id'

            Args:
                project_name (string): project name (must match DB)
                representation_id (string): MongoDB _id value
                site_name (string): name of configured and active site

            Returns:
                only logs, catches IndexError and OSError
        """
        my_local_site = get_local_site_id()
        if my_local_site != site_name:
            self.log.warning("Cannot remove non local file for {}".
                             format(site_name))
            return

        provider_name = self.get_provider_for_site(site=site_name)

        if provider_name == 'local_drive':
            representation = get_representation_by_id(
                project_name, representation_id, fields=["files"])
            if not representation:
                self.log.debug("No repre {} found".format(
                    representation_id))
                return

            local_file_path = ''
            for file in representation.get("files"):
                local_file_path = self.get_local_file_path(project_name,
                                                           site_name,
                                                           file.get("path", "")
                                                           )
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

            folder = None
            try:
                folder = os.path.dirname(local_file_path)
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

        ld = self.sync_project_settings[project_name]["config"]["loop_delay"]
        return int(ld)

    def cli(self, click_group):
        click_group.add_command(cli_main)


@click.group(SiteSyncAddon.name, help="SyncServer module related commands.")
def cli_main():
    pass


@cli_main.command()
@click.option(
    "-a",
    "--active_site",
    required=True,
    help="Name of active stie")
def syncservice(active_site):
    """Launch sync server under entered site.

    This should be ideally used by system service (such us systemd or upstart
    on linux and window service).
    """

    from ayon_core.modules import ModulesManager

    os.environ["AYON_SITE_ID"] = active_site

    def signal_handler(sig, frame):
        print("You pressed Ctrl+C. Process ended.")
        sitesync_module.server_exit()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    manager = ModulesManager()
    sitesync_module = manager.modules_by_name["sitesync"]

    sitesync_module.server_init()
    sitesync_module.server_start()

    while True:
        time.sleep(1.0)
