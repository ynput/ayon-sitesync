import os
import json
import subprocess
from datetime import datetime
from .abstract_provider import AbstractProvider


class RCloneProvider(AbstractProvider):
    CODE = "rclone"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        super().__init__(project_name, site_name, tree, presets)
        self.rclone_path = "rclone"
        self.presets = presets or {}
        self._config_path = self.presets.get("config_file")
        self.remote_name = self.presets.get("remote_name", "ayon_remote")
        # Get extra flags from settings (e.g. ["--webdav-nextcloud-chunk-size", "0"])
        self.extra_args = self.presets.get("additional_args", [])

    def is_active(self):
        """Check if rclone is available and config exists."""
        if not self._config_path or not os.path.exists(self._config_path):
            return False
        try:
            self._run_rclone(["version"])
            return True
        except Exception:
            return False

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
        """High-speed upload using rclone copyto."""
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source file {source_path} doesn't exist.")

        args = [
            "copyto",
            source_path,
            f"{self.remote_name}:{target_path}",
            "--fast-list",
            "--contimeout", "60s"
        ]
        if not overwrite:
            args.append("--ignore-existing")

        self._run_rclone(args)

        # SiteSync status update
        if addon:
            addon.update_db(
                project_name=project_name,
                new_file_id=None,
                file=file,
                repre_status=repre_status,
                site_name=site_name,
                side="remote",
                progress=100
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
        """High-speed download using rclone copyto."""
        args = [
            "copyto",
            f"{self.remote_name}:{source_path}",
            local_path
        ]
        if not overwrite:
            args.append("--ignore-existing")

        self._run_rclone(args)

        if addon:
            addon.update_db(
                project_name=project_name,
                new_file_id=None,
                file=file,
                repre_status=repre_status,
                site_name=site_name,
                side="local",
                progress=100
            )
        return os.path.basename(source_path)

    def delete_file(self, path):
        """Delete a file from the remote."""
        args = ["deletefile", f"{self.remote_name}:{path}"]
        try:
            self._run_rclone(args)
            self.log.info(f"Successfully deleted {path} on {self.remote_name}")
        except subprocess.CalledProcessError as e:
            self.log.error(f"Failed to delete {path}: {e}")
            raise FileNotFoundError(
                f"Failed to delete {path} on {self.remote_name}")

    def list_folder(self, folder_path):
        """List all files in a folder non-recursively."""
        args = ["lsjson", f"{self.remote_name}:{folder_path}"]
        raw_json = self._run_rclone(args)
        items = json.loads(raw_json)
        return [item["Name"] for item in items]

    def create_folder(self, folder_path):
        """Create a directory on the remote."""
        args = ["mkdir", f"{self.remote_name}:{folder_path}"]
        self._run_rclone(args)
        return folder_path

    def get_tree(self, remote_path=""):
        """Fetch full recursive metadata tree."""
        args = [
            "lsjson", "-R",
            f"{self.remote_name}:{remote_path}",
            "--files-only"
        ]
        raw_json = self._run_rclone(args)
        items = json.loads(raw_json)

        tree = {}
        for item in items:
            tree[item["Path"]] = {
                "size": item["Size"],
                "mtime": self._parse_time(item["ModTime"]),
                "is_dir": item["IsDir"]
            }
        return tree

    def get_roots_config(self, anatomy=None):
        """Returns root values for path resolving."""
        return {"root": {"work": self.presets.get('root', '/')}}

    # Helper methods
    def _run_rclone(self, args):
        """Internal helper to execute rclone commands with extra args."""
        # Insert extra args before the specific command arguments
        cmd = [self.rclone_path, "--config", self._config_path] + self.extra_args + args
        self.log.debug(f"Running rclone: {' '.join(cmd)}")
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT)

    def _parse_time(self, rclone_time_str):
        """Parse rclone's ISO8601 time string to timestamp."""
        dt = datetime.fromisoformat(rclone_time_str.replace("Z", "+00:00"))
        return dt.timestamp()