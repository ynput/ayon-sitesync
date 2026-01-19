import json
import subprocess
from .abstract_provider import AbstractProvider, Logger

log = Logger.get_logger("SiteSync-NextCloudHandler")


class RCloneProvider(AbstractProvider):
    CODE = "rclone"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        super().__init__(project_name, site_name, tree, presets)
        self.rclone_path = "rclone"  # Or path from settings

        self.presets = presets
        self._config_path = self.presets["config_file"]


    def _run_rclone(self, args):
        """Helper to run rclone commands with the temp config."""
        cmd = [self.rclone_path, "--config", self._config_path] + args
        self.log.debug(f"Running rclone: {' '.join(cmd)}")
        return subprocess.check_output(cmd)

    def upload_file(self, source_path, target_path, **kwargs):
        """High-speed upload using rclone copyto."""
        args = [
            "copyto",
            source_path,
            f"{self.remote_name}:{target_path}",
            "--fast-list",
            "--contimeout", "60s"
        ]
        self._run_rclone(args)

    def download_file(self, source_path, local_path, **kwargs):
        """High-speed download using rclone copyto."""
        args = [
            "copyto",
            f"{self.remote_name}:{source_path}",
            local_path
        ]
        self._run_rclone(args)

    def get_tree(self, remote_path=""):
        """Use lsjson to get a full recursive tree in one request."""
        args = [
            "lsjson", "-R",
            f"{self.remote_name}:{remote_path}",
            "--files-only"
        ]
        raw_json = self._run_rclone(args)
        items = json.loads(raw_json)

        tree = {}
        for item in items:
            # item['Path'] is relative to the search root
            tree[item["Path"]] = {
                "size": item["Size"],
                "mtime": self._parse_time(item["ModTime"]),
                "is_dir": item["IsDir"]
            }
        return tree

    def delete_file(self, path):
        """Delete a file or directory from the remote."""
        args = [
            "delete",
            f"{self.remote_name}:{path}"
        ]
        self._run_rclone(args)

        if not self._run_rclone(args):
            raise FileNotFoundError(f"Failed to delete {path} on {self.remote_name}")
        else:
            (self.log.info(f"Successfully deleted {path} on {self.remote_name}"))

