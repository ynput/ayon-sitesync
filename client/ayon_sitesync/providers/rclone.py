import os
import json
import subprocess
import platform
from datetime import datetime
from .abstract_provider import AbstractProvider


class RCloneHandler(AbstractProvider):
    CODE = "rclone"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        super().__init__(project_name, site_name, tree, presets)
        self.presets = presets or {}
        self.rclone_path = self.presets.get("rclone_executable_path", "rclone").get(platform.system().lower())
        self._config_path = self.presets.get("rclone_config_path").get(platform.system().lower())
        self.log.info(f"Using rclone config from {self._config_path}")
        self.remote_name = self.presets.get("remote_name", "nextcloud")
        self._root = self.presets.get("root", "").strip("/")
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

        remote_path = self._get_remote_path(target_path)

        args = [
            "copyto",
            source_path,
            remote_path,
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

        if os.path.exists(local_path) and not overwrite:
            raise FileExistsError(
                "File already exists, use 'overwrite' argument"
            )

        source_path = self._get_remote_path(source_path)
        args = [
            "copyto",
            source_path,
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
        remote_path = self._get_remote_path(path)
        args = ["deletefile", f"{remote_path}"]
        try:
            self._run_rclone(args)
            self.log.info(f"Successfully deleted {path} on {self.remote_name}")
        except subprocess.CalledProcessError as e:
            self.log.error(f"Failed to delete {path}: {e}")
            raise FileNotFoundError(
                f"Failed to delete {path} on {self.remote_name}")

    def list_folder(self, folder_path):
        """List all files in a folder non-recursively."""
        remote_folder_path = self._get_remote_path(folder_path)
        args = ["lsjson", remote_folder_path]
        raw_json = self._run_rclone(args)
        items = json.loads(raw_json)
        return [item["Name"] for item in items]

    def create_folder(self, folder_path):
        """Create a directory on the remote."""
        remote_folder_path = self._get_remote_path(folder_path)
        args = ["mkdir", remote_folder_path]
        self._run_rclone(args)
        return folder_path

    def get_tree(self, remote_path=""):
        """Fetch full recursive metadata tree."""
        remote_path = self._get_remote_path(remote_path)
        args = [
            "lsjson", "-R",
            remote_path,
            "--files-only"
        ]
        raw_json = self._run_rclone(args)
        data = self._parse_rclone_json(raw_json)
        items = json.loads(data)

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
        env = os.environ.copy()
        if self.presets.get("password"):
            # Format: RCLONE_CONFIG_<UPPERCASE_REMOTE_NAME>_PASS
            env_key = f"RCLONE_CONFIG_{self.remote_name.upper()}_PASS"
            env[env_key] = self._obscure_pass(self.presets.get("password"))
        self.log.info(f"Running rclone: {' '.join(cmd)}")
        p = subprocess.check_output(' '.join(cmd), stderr=subprocess.STDOUT, env=env)
        self.log.info(f"rclone output: {p}")
        return p

    def _get_remote_path(self, path):
        """Helper to format the path for rclone with the root prefix."""
        clean_path = path.strip("/")
        if self._root:
            return f"{self.remote_name}:{self._root}/{clean_path}"
        return f"{self.remote_name}:{clean_path}"

    def _parse_time(self, rclone_time_str):
        """Parse rclone's ISO8601 time string to timestamp."""
        dt = datetime.fromisoformat(rclone_time_str.replace("Z", "+00:00"))
        return dt.timestamp()

    def _obscure_pass(self, password):
        # Rclone expects passwords in env vars to be obscured
        # You can call 'rclone obscure' via subprocess to get this string
        cmd = [self.rclone_path, "obscure", password]
        return subprocess.check_output(cmd).decode().strip()

    @staticmethod
    def _parse_rclone_json(output:dict):
        """Parses JSON from rclone output, stripping leading/trailing non-JSON text."""
        if not output:
            return None

        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")

        # Find the first occurrence of '[' or '{' to skip headers/notices
        start_index = -1
        for i, char in enumerate(output):
            if char in ('[', '{'):
                start_index = i
                break

        if start_index == -1:
            raise ValueError(f"No JSON object found in output: {output}")

        # Find the last occurrence of ']' or '}'
        end_index = -1
        for i, char in enumerate(reversed(output)):
            if char in (']', '}'):
                end_index = len(output) - i
                break

        return output[start_index:end_index]