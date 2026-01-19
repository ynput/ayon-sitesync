import os
import time

from ayon_core.lib import Logger
from webdav3.client import Client
from .abstract_provider import AbstractProvider
from email.utils import parsedate_to_datetime

log = Logger.get_logger("SiteSync-NextCloudHandler")


class NextcloudHandler(AbstractProvider):
    """Nextcloud provider using WebDAV protocol."""

    CODE = "nextcloud"
    LABEL = "nextcloud"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        self.active = False
        self.project_name = project_name
        self.site_name = site_name
        self.presets = presets

        if not self.presets:
            self.log.info(
                "Sync Server: There are no presets for {}.".format(site_name)
            )
            return

        if not self.presets.get("enabled"):
            self.log.debug("Sync Server: Site {} not enabled for {}.".
                           format(site_name, project_name))
            return

        # Site config should contain: url, username, password
        # Nextcloud WebDAV URL is usually:
        # https://your-nextcloud.com/remote.php/dav/files/USERNAME/
        self._url = self.presets.get("url")
        self._username = self.presets.get("username")
        self._password = self.presets.get("password")
        self._root = self.presets.get("root")

        # weird constructor
        options = {
            'webdav_hostname': self._url,
            'webdav_login': self._username,
            'webdav_password': self._password,
            'webdav_root': self._root,
        }
        self.client = Client(options)
        self._dir_cache = set()
        super(AbstractProvider, self).__init__()

    def is_active(self):
        return True

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
        """Upload local file to Nextcloud."""
        # Check source path.
        if not os.path.exists(source_path):
            raise FileNotFoundError(
                "Source file {} doesn't exist.".format(source_path)
            )
        if not target_path.startswith("/"):
            target_path = "/" + target_path

        if self._path_exists(target_path) and not overwrite:
            raise FileExistsError(
                "File already exists, use 'overwrite' argument"
            )

        # Ensure remote directory exists
        remote_dir = os.path.dirname(target_path)
        if remote_dir and remote_dir != "/":
            if remote_dir.endswith("/"):
                remote_dir = remote_dir[:-1]
            self.create_folder(remote_dir)

        self.client.upload_sync(remote_path=target_path, local_path=source_path)
        # addon.update_db(
        #     project_name=project_name,
        #     new_file_id=None,
        #     file=file,
        #     repre_status=repre_status,
        #     site_name=site_name,
        #     side="remote",
        #     progress=100
        # )

    def download_file(self,
                      source_path,
                      local_path,
                      addon,
                      project_name,
                      file,
                      repre_status,
                      site_name,
                      overwrite=False
                      ):
        # Check source path.
        if not self._path_exists(source_path):
            raise FileNotFoundError(
                "Source file {} doesn't exist.".format(source_path)
            )

        if os.path.exists(local_path) and not overwrite:
            raise FileExistsError(
                "File already exists, use 'overwrite' argument"
            )

        if os.path.exists(local_path) and overwrite:
            os.unlink(local_path)

        local_dir = os.path.dirname(local_path)
        os.makedirs(local_dir, exist_ok=True)

        self.client.download_sync(remote_path=source_path,
                                  local_path=local_path)
        # addon.update_db(
        #     project_name=project_name,
        #     new_file_id=None,
        #     file=file,
        #     repre_status=repre_status,
        #     site_name=site_name,
        #     side="local",
        #     progress=100
        # )

        return os.path.basename(source_path)

    def delete_file(self, path):
        """
            Deletes file from 'path'. Expects path to specific file.

        Args:
            path (string): absolute path to particular file

        Returns:
            None
        """
        if not self._path_exists(path):
            raise FileExistsError("File {} doesn't exist".format(path))

        self.client.clean(path)

    def list_folder(self, folder_path):
        """
            List all files and subfolders of particular path non-recursively.
        Args:
            folder_path (string): absolut path on provider

        Returns:
            (list)
        """
        return self.client.list(folder_path)

    def create_folder(self, folder_path):
        """
            Create all nonexistent folders and subfolders in 'path'.

        Args:
            folder_path (string): absolute path

        Returns:
            (string) folder id of lowest subfolder from 'path'
        """
        self._ensure_dir_exists(folder_path)

    def get_tree_old(self, remote_path="", get_info=False):
        """Fetch file metadata from Nextcloud.

        Returns:
            dict: {relative_path: {"size": int, "mtime": float}}
        """
        files_info = self.client.list(remote_path=remote_path,
                                      get_info=get_info)
        tree = {}
        for info in files_info:
            path = info.get("path")
            # WebDAV returns absolute paths like '/remote.php/dav/files/jhezer/test/'
            # We need to strip the base URL and the root to get the relative path

            # 1. Strip the standard Nextcloud WebDAV prefix if present
            # (You might need to adjust this depending on how your URL is configured)
            prefix = "/remote.php/dav/files/" + self._username
            rel_path = path.replace(prefix, "")

            # 2. Strip the provider root
            if rel_path.startswith(self._root):
                rel_path = rel_path[len(self._root):]

            # Clean up slashes
            rel_path = rel_path.strip("/")

            # If it's the root of our search, skip or handle as empty string
            if not rel_path and info.get("isdir"):
                continue

            # Convert WebDAV modified string to timestamp
            mtime = None
            if info.get("modified"):
                try:
                    dt = parsedate_to_datetime(info.get("modified"))
                    mtime = time.mktime(dt.timetuple())
                except Exception:
                    pass

            tree[rel_path] = {
                "size": int(info.get("size") or 0),
                "mtime": mtime,
                "is_dir": info.get("isdir", False)
            }

        self.log.debug(
            f"Retrieved {len(tree)} items from Nextcloud at {remote_path}")
        return tree

    def get_tree(self, remote_path="", get_info=True):
        """Fetch file metadata from Nextcloud recursively.

        Returns:
            dict: {relative_path: {"size": int, "mtime": float, "is_dir": bool}}
        """
        tree = {}
        # Start the recursive crawl from the provided remote_path
        self._recurse_remote_tree(remote_path, tree, get_info)

        self.log.debug(
            f"Retrieved {len(tree)} items recursively from Nextcloud")
        return tree

    def _recurse_remote_tree(self, remote_path, tree, get_info):
        """Internal helper to crawl WebDAV directories recursively."""
        # Ensure path is formatted correctly for the client
        search_path = remote_path
        if not search_path.startswith("/"):
            search_path = "/" + search_path

        #full_search_path = (self._root + search_path).replace("//", "/")

        try:
            files_info = self.client.list(remote_path=search_path,
                                          get_info=get_info)
        except Exception as e:
            self.log.error(f"Failed to list {search_path}: {e}")
            return

        prefix = "/remote.php/dav/files/" + self._username

        for info in files_info:
            path = info.get("path")
            # Strip prefix and root to get the relative path for AYON
            rel_path = path.replace(prefix, "")
            if rel_path.startswith(self._root):
                rel_path = rel_path[len(self._root):]

            rel_path = rel_path.strip("/")

            # Skip the directory we are currently listing
            current_search_rel = search_path.strip("/")
            if rel_path == current_search_rel:
                continue

            # Avoid empty keys for the root itself
            if not rel_path:
                continue

            # Process time
            mtime = None
            if info.get("modified"):
                try:
                    dt = parsedate_to_datetime(info.get("modified"))
                    mtime = time.mktime(dt.timetuple())
                except Exception:
                    pass

            # Add to tree
            tree[rel_path] = {
                "size": int(info.get("size") or 0),
                "mtime": mtime,
                "is_dir": info.get("isdir", False)
            }

            # If it's a directory, dive deeper
            if info.get("isdir"):
                # Use the relative path we just calculated as the next search point
                self._recurse_remote_tree(rel_path, tree, get_info)

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
        return {"root": {"work": self.presets['root']}}

    ### helper methods

    def _ensure_dir_exists(self, path):
        """Recursively ensure that the directory structure exists with caching."""
        if path in self._dir_cache or path == "" or path == "/":
            return

        parts = path.strip("/").split("/")
        current_path = ""
        for part in parts:
            current_path += "/" + part
            if current_path in self._dir_cache:
                continue

            if not self.client.check(current_path):
                self.client.mkdir(current_path)

            self._dir_cache.add(current_path)

    def _path_exists(self, path):
        """Check if a path exists on the remote server."""
        return self.client.check(path)
