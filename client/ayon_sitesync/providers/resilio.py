import os.path
import time
from datetime import datetime
from sys import platform
import platform

from ayon_core.lib import Logger
from ayon_core.pipeline import Anatomy
from ayon_sitesync.providers.abstract_provider import AbstractProvider
from ayon_sitesync.providers.vendor.resilio import ConnectApi, Path, Job

log = Logger.get_logger("SiteSync")


class ResilioHandler(AbstractProvider):
    CODE = "resilio"
    LABEL = "Resilio"

    _log = None

    def __init__(self, project_name, site_name, tree=None, presets=None):
        self.active = False
        self.project_name = project_name
        self.site_name = site_name
        self._conn = None
        self.root = None

        self.presets = presets
        if not self.presets:
            self.log.info(
                "Sync Server: There are no presets for {}.".format(site_name)
            )
            return

        if not self.presets.get("enabled"):
            self.log.debug(
                "Sync Server: Site {} not enabled for {}.".format(
                    site_name, project_name
                )
            )
            return

        host = self.presets.get("host", "")
        if not host:
            msg = "Sync Server: No host to Resilio Management Console"
            self.log.info(msg)
            return

        port = self.presets.get("port", "")
        if not port:
            msg = "Sync Server: No port to Resilio Management Console"
            self.log.info(msg)
            return

        token = self.presets.get("token", "")
        if not token:
            msg = (
                "Sync Server: No access token for to "
                "Resilio Management Console"
            )
            self.log.info(msg)
            return

        agent_id = self.presets.get("agent_id", "")
        if not agent_id:
            msg = (
                "Sync Server: No agent id for to "
                "Resilio Management Console"
            )
            self.log.info(msg)
            return
        self.agent_id = agent_id

        address = f"{host}:{port}"
        self._conn = ConnectApi(address, token)


    def is_active(self):
        """
            Returns True if provider is activated, eg. has working credentials.
        Returns:
            (boolean)
        """
        return self.presets.get("enabled") and self._conn is not None

    def upload_file(
        self,
        source_path,
        target_path,
        addon,
        project_name,
        file,
        repre_status,
        site_name,
        overwrite=False
    ):
        """
            Copy file from 'source_path' to 'target_path' on provider.
            Use 'overwrite' boolean to rewrite existing file on provider

        Args:
            source_path (string): absolute path on provider
            target_path (string): absolute path with or without name of the file
            addon (SiteSyncAddon): addon instance to call update_db on
            project_name (str):
            file (dict): info about uploaded file (matches structure from db)
            repre_status (dict): complete representation containing
                sync progress
            site_name (str): target site name
            overwrite (boolean): replace existing file
        Returns:
            (string) file_id of created/modified file ,
                throws FileExistsError, FileNotFoundError exceptions
        """
        project_settings = addon.sync_project_settings[project_name]

        # Access the sites configuration
        sites = project_settings.get("sites", {})

        # Get agent_id for a specific site_name
        site_config = sites.get(site_name, {})
        if not site_config:
            msg = (f"Sync Server: No configuration found for site '{site_name}'"
                   f" in project '{project_name}'.")
            self.log.error(msg)
            raise ValueError(msg)
        dest_agent_id = site_config.get("agent_id")

        src_agent_id = project_settings["local_setting"]["resilio"]["agent_id"]
        job_data = {
            "name": f"Sync Job via API  {datetime.now().strftime('%Y%m%d%H%M%S')}",
            "description": "Created using the connect_api module",
            "type": "distribution",  # 'transfer' is used for Distribution jobs
            "agents": [
                {
                    "id": src_agent_id,
                    "path":  Path(source_path).get_object(),
                    "permission": "rw"  # Sources are read_write
                },
                {
                    "id": dest_agent_id,
                    "path": Path(os.path.dirname(target_path)).get_object(),
                    "permission": "ro"   # Targets are read_only
                }
            ]
        }

        return self._upload_download_process(
            project_name,
            addon,
            file,
            repre_status,
            site_name,
            target_path,
            job_data,
            "local"
        )

    def download_file(
        self,
        source_path,
        local_path,
        addon,
        project_name,
        file,
        repre_status,
        site_name,
        overwrite=False
    ):
        """
            Download file from provider into local system

        Args:
            source_path (string): absolute path on provider
            local_path (string): absolute path with or without name of the file
            addon (SiteSyncAddon): addon instance to call update_db on
            project_name (str):
            file (dict): info about uploaded file (matches structure from db)
            repre_status (dict): complete representation containing
                sync progress
            site_name (str): site name
            overwrite (boolean): replace existing file
        Returns:
            (string) file_id of created/modified file ,
                throws FileExistsError, FileNotFoundError exceptions
        """
        project_settings = addon.sync_project_settings[project_name]
        sites = project_settings.get("sites", {})

        # Get agent_id for a specific site_name
        site_config = sites.get(site_name, {})
        if not site_config:
            msg = (f"Sync Server: No configuration found for site '{site_name}'"
                   f" in project '{project_name}'.")
            self.log.error(msg)
            raise ValueError(msg)
        src_agent_id = site_config.get("agent_id")
        target_agent_id = project_settings["local_setting"]["resilio"]["agent_id"]
        job_data = {
            "name": f"Sync Job via API  {datetime.now().strftime('%Y%m%d%H%M%S')}",
            "description": "Created using the connect_api module",
            "type": "distribution",  # 'transfer' is used for Distribution jobs
            "agents": [
                {
                    "id": src_agent_id,
                    "path":  Path(source_path).get_object(),
                    "permission": "rw"  # Sources are read_write
                },
                {
                    "id": target_agent_id,
                    "path": Path(os.path.dirname(local_path)).get_object(),
                    "permission": "ro"   # Targets are read_only
                }
            ]
        }

        return self._upload_download_process(
            project_name,
            addon,
            file,
            repre_status,
            site_name,
            local_path,
            job_data,
            "remote"
        )

    def _upload_download_process(
            self,
            project_name,
            addon,
            file,
            repre_status,
            site_name,
            target_path,
            job_data,
            side
    ):
        new_job = Job(self._conn, job_data)
        new_job.save()

        self.log.debug(f"Job '{new_job.name}' created successfully.")
        job_run_id = new_job.start()

        last_tick = None
        job_run = None
        while (
            job_run is None or
            job_run.status not in ["finished", "failed", "aborted"]
        ):
            job_run = self._conn.get_job_run(job_run_id)

            if addon.is_representation_paused(
                    repre_status["representationId"],
                    check_parents=True,
                    project_name=project_name):
                raise ValueError("Paused during process, please redo.")

            progress_value = (
                    float(job_run.attrs["transferred"] / job_run.attrs["size_total"])
                    if job_run.attrs["size_total"]
                    else 0.0
            )

            if not last_tick or \
                    time.time() - last_tick >= addon.LOG_PROGRESS_SEC:
                last_tick = time.time()
                self.log.debug("Uploaded %d%%." % int(progress_value * 100))
                addon.update_db(
                    project_name=project_name,
                    new_file_id=None,
                    file=file,
                    repre_status=repre_status,
                    site_name=site_name,
                    side=side,
                    progress=progress_value
                )
            time.sleep(10)

        if job_run.status == "finished":
            return target_path

    def delete_file(self, path):
        """
            Deletes file from 'path'. Expects path to specific file.

        Args:
            path (string): absolute path to particular file

        Returns:
            None
        """
        raise NotImplementedError("This provider does not support folders")

    def list_folder(self, folder_path):
        """
            List all files and subfolders of particular path non-recursively.
        Args:
            folder_path (string): absolut path on provider

        Returns:
            (list)
        """
        pass

    def create_folder(self, folder_path):
        """
            Create all nonexistent folders and subfolders in 'path'.

        Args:
            path (string): absolute path

        Returns:
            (string) folder id of lowest subfolder from 'path'
        """
        # Resilio creates folder path automatically
        return os.path.basename(folder_path)

    def get_tree(self):
        """
            Creates folder structure for providers which do not provide
            tree folder structure (GDrive has no accessible tree structure,
            only parents and their parents)
        """
        pass

    def get_roots_config(self, anatomy=None):
        """
            Returns root values for path resolving

            Takes value from Anatomy which takes values from Settings
            overridden by Local Settings

        Returns:
            (dict) - {"root": {"root": "/My Drive"}}
                     OR
                     {"root": {"root_ONE": "value", "root_TWO":"value}}
            Format is importing for usage of python's format ** approach
        """
        platform_name = platform.system().lower()
        root_configs = {}
        for root_info in self.presets["root"]:
            root_configs[root_info["name"]] = root_info.get(platform_name)
        return {"root": root_configs}

    def resolve_path(self, path, root_config=None, anatomy=None):
        """
            Replaces all root placeholders with proper values

            Args:
                path(string): root[work]/folder...
                root_config (dict): {'work': "c:/..."...}
                anatomy (Anatomy): object of Anatomy
            Returns:
                (string): proper url
        """
        if not root_config:
            root_config = self.get_roots_config(anatomy)

        if root_config and not root_config.get("root"):
            root_config = {"root": root_config}

        try:
            if not root_config:
                raise KeyError

            path = path.format(**root_config)
        except KeyError:
            try:
                path = anatomy.fill_root(path)
            except KeyError:
                msg = "Error in resolving local root from anatomy"
                self.log.error(msg)
                raise ValueError(msg)

        return path
