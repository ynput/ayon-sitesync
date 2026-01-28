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
        self.project_name = project_name
        self.presets = presets or {}
        self.rclone_path = self.presets.get("rclone_executable_path", {}).get(
            platform.system().lower(), "rclone")
        self._config_path = self.presets.get("rclone_config_path", {}).get(
            platform.system().lower(), "")

        if not self._config_path:
            # We need to check vendor, type, url and user under presets
            # if they exist and are filled we use them in the env to get the config path
            vendor = self.presets.get("vendor", "")
            type = self.presets.get("type", "")
            url = self.presets.get("url", "")
            user = self.presets.get("user", "")
            # check if there is data in all vars
            if vendor and type and url and user:
                self.vendor = vendor
                self.type = type
                self.url = url
                self.user = user
            else:
                raise ValueError(
                    "No rclone.conf is defined nor the needed settings, cannot create a connection.")

        self.log.debug(f"Using rclone config from {self._config_path}")

        self.remote_name = self.presets.get("remote_name", "")
        self._root = self.presets.get("root", "").strip("/")
        self.extra_args = self.presets.get("additional_args", [])
        self._tree = tree

    def is_active(self):
        """Check if rclone is available and config exists."""
        if not self._config_path or not os.path.exists(self._config_path):
            return False
        try:
            self._run_rclone(["version"])
            self.log.info("rclone is healthy")
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

        if self._path_exists(target_path) and not overwrite:
            raise FileExistsError(
                f"File already exists, use 'overwrite' argument: {target_path}"
            )

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
            self.log.info(
                f"Successfully uploaded {source_path} to {remote_path}")
            self.log.info(f"Updating SiteSync status for {file['id']}")
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
        if not self._path_exists(source_path):
            raise FileNotFoundError(f"Source file {source_path} doesn't exist.")

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
            self.log.info(
                f"Successfully downloaded {source_path} to {local_path}")
            self.log.info(f"Updating SiteSync status for {file['id']}")
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
        data = []
        for item in items:
            name = item["Name"]
            if not name.startswith("/"):
                name = "/" + name
            data.append(name)
        self.log.info(f"Fetched list of files in {folder_path}: {data}")
        return data

    def create_folder(self, folder_path):
        """Create a directory on the remote."""
        if self._path_exists(folder_path):
            return folder_path
        remote_folder_path = self._get_remote_path(folder_path)
        args = ["mkdir", remote_folder_path]
        self._run_rclone(args)
        self.log.info(
            f"Successfully created {folder_path} on {self.remote_name}")
        return folder_path

    def get_tree(self):
        """Fetch full recursive metadata tree."""
        if not self._tree:
            remote_path = self._get_remote_path(self._root or "/")
            # remote_path = remote_path + "/" + self.project_name
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
                path = item["Path"]
                full_ayon_path = f"{self._root}/{path}" if self._root else path
                tree[full_ayon_path] = {
                    "size": item["Size"],
                    "mtime": self._parse_time(item["ModTime"]),
                    "is_dir": item["IsDir"]
                }
            if tree:
                self._tree = tree
        self.log.info(f"Tree: {self._tree}")
        return self._tree

    def get_roots_config(self, anatomy=None):
        """Returns root values for path resolving."""
        return {"root": {"work": self.presets.get('root', '/')}}

    # Helper methods
    def _run_rclone(self, args):
        """Internal helper to execute rclone commands with extra args."""
        # Insert extra args before the specific command arguments
        cmd = [self.rclone_path, "--config",
               self._config_path] + self.extra_args + args
        env = os.environ.copy()
        if self.presets.get("password"):
            # Format: RCLONE_CONFIG_<UPPERCASE_REMOTE_NAME>_PASS
            env_key = f"RCLONE_CONFIG_{self.remote_name.upper()}_PASS"
            env[env_key] = self._obscure_pass(self.presets.get("password"))

        if not self._config_path:
            # as we raise an error upon init we can get away with this check alone
            cmd = [self.rclone_path] + self.extra_args + args
            env_vendor = f"RCLONE_CONFIG_{self.remote_name.upper()}_VENDOR"
            env[env_vendor] = self.vendor
            env_type = f"RCLONE_CONFIG_{self.remote_name.upper()}_TYPE"
            env[env_type] = self.type
            env_url = f"RCLONE_CONFIG_{self.remote_name.upper()}_URL"
            env[env_url] = self.url
            env_user = f"RCLONE_CONFIG_{self.remote_name.upper()}_USER"
            env[env_user] = self.user

        self.log.info(f"Running rclone: {' '.join(cmd)}")
        p = subprocess.check_output(' '.join(cmd), stderr=subprocess.STDOUT,
                                    env=env)
        self.log.debug(f"rclone output: {p}")
        return p

    def _path_exists(self, path):
        """Checks if path exists on remote."""
        remote_path = self._get_remote_path(path)
        args = ["lsjson", remote_path]
        try:
            output = self._run_rclone(args)
            parsed = self._parse_rclone_json(output)
            if not parsed:
                self.log.info(
                    f"Path check failed for {remote_path}: empty output")
                return False

            items = json.loads(parsed)
            # If rclone returns a list with items, the path (or its contents) exists.
            # When pointing directly to a file, rclone returns [ { "Path": "filename", ... } ]
            self.log.info(f"{remote_path} exists")
            return len(items) > 0
        except Exception as e:
            self.log.info(f"Path check failed for {remote_path}: {e}")
            return False

    def _get_remote_path(self, path):
        """Helper to format the path for rclone with the remote name."""
        path = f"{self.remote_name}:{path}"
        self.log.info(f"Remote Path: {path}")
        return path

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
    def _parse_rclone_json(output: dict):
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
