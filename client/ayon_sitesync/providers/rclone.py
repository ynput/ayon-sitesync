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

        self._root = self.presets.get("root", "").strip("/")
        self.extra_args = self.presets.get("additional_args", [])
        self._config_path = self.presets.get("rclone_config_path", {}).get(
            platform.system().lower(), "")

        self.vendor = self.presets.get("vendor", "")
        self.tipe = self.presets.get("type", "")
        self.url = self.presets.get("url", "")
        self.user = self.presets.get("user", "")

        # manage config vs live env
        if not self._config_path:
            # We need to check vendor, type, url and user under presets
            # if they exist and are filled we use them in the env to get the config path
            # check if there is data in all vars
            if self.vendor and self.tipe and self.url and self.user:
                self.log.debug(
                    f"Using rclone with env vars: vendor={self.vendor}, type={self.tipe}, url={self.url}, user={self.user}")

            else:
                raise ValueError(
                    "No rclone.conf is defined nor the needed settings, cannot create a connection.")
        else:
            self.log.debug(f"Using rclone config from {self._config_path}")

        # in case the remote name is not set, use the vendor name works for the env connection
        self.remote_name = self.presets.get("remote_name")
        if not self.remote_name:
            self.remote_name = self.vendor

        self._tree = tree

    def is_active(self):
        """Check if rclone is available and config exists."""
        try:
            self._run_rclone(["version"])
            self.log.debug("rclone is healthy")
            return True
        except Exception as e:
            self.log.exception(f"rclone is not healthy: {e}")
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
            self.log.debug(
                f"Successfully uploaded {source_path} to {remote_path}")
            self.log.debug(f"Updating SiteSync status for {file['id']}")
            addon.update_db(
                project_name=project_name,
                new_file_id=None,
                file=file,
                repre_status=repre_status,
                site_name=site_name,
                side="remote",
                progress=1.0
            )

        return target_path

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
            self.log.debug(
                f"Successfully downloaded {source_path} to {local_path}")
            self.log.debug(f"Updating SiteSync status for {file['id']}")
            addon.update_db(
                project_name=project_name,
                new_file_id=None,
                file=file,
                repre_status=repre_status,
                site_name=site_name,
                side="local",
                progress=1.0
            )
        return os.path.basename(source_path)

    def delete_file(self, path):
        """Delete a file from the remote."""
        remote_path = self._get_remote_path(path)
        args = ["deletefile", f"{remote_path}"]
        try:
            self._run_rclone(args)
            self.log.debug(f"Successfully deleted {path} on {self.remote_name}")
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
        self.log.debug(f"Fetched list of files in {folder_path}: {data}")
        return data

    def create_folder(self, folder_path):
        """Create a directory on the remote."""
        if self._path_exists(folder_path):
            return folder_path
        remote_folder_path = self._get_remote_path(folder_path)
        args = ["mkdir", remote_folder_path]
        self._run_rclone(args)
        self.log.debug(
            f"Successfully created {folder_path} on {self.remote_name}")
        return folder_path

    def get_tree(self):
        """Not needed here."""
        pass

    def get_roots_config(self, anatomy=None):
        """Returns root values for path resolving."""
        return {"root": {"work": self.presets.get('root', '/')}}

    # Helper methods
    def _run_rclone(self, args):
        """Internal helper to execute rclone commands with extra args."""
        # Insert extra args before the specific command arguments
        cmd = [self.rclone_path, "--config",
               self._config_path]

        if self.extra_args:
            cmd += self.extra_args
        cmd += args

        env = os.environ.copy()
        if self.presets.get("password"):
            # Format: RCLONE_CONFIG_<UPPERCASE_REMOTE_NAME>_PASS
            env_key = f"RCLONE_CONFIG_{self.remote_name.upper()}_PASS"
            env[env_key] = self._obscure_pass(self.presets.get("password"))

        if not self._config_path:
            # as we raise an error upon init we can get away with this check alone
            cmd = [self.rclone_path]
            if self.extra_args:
                cmd += self.extra_args
            cmd += args
            env_vendor = f"RCLONE_CONFIG_{self.remote_name.upper()}_VENDOR"
            env[env_vendor] = self.vendor
            env_type = f"RCLONE_CONFIG_{self.remote_name.upper()}_TYPE"
            env[env_type] = self.tipe
            env_url = f"RCLONE_CONFIG_{self.remote_name.upper()}_URL"
            env[env_url] = self.url
            env_user = f"RCLONE_CONFIG_{self.remote_name.upper()}_USER"
            env[env_user] = self.user
            env["RCLONE_CONFIG"] = "NUL"

        self.log.info("Running rclone: %s", " ".join(cmd))

        p = subprocess.run(
            cmd,  # pass list, not a joined string
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,  # decoded to str
            check=False,
        )

        if p.returncode != 0:
            raise RuntimeError(
                "rclone failed\n"
                f"exit_code: {p.returncode}\n"
                f"stdout:\n{p.stdout}\n"
                f"stderr:\n{p.stderr}"
            )

        out = p.stdout
        return out

    def _path_exists(self, path):
        """Checks if path exists on remote."""
        remote_path = self._get_remote_path(path)
        args = ["lsjson", remote_path]
        try:
            output = self._run_rclone(args)
            parsed = self._parse_rclone_json(output)
            if not parsed:
                self.log.error(
                    f"Path check failed for {remote_path}: empty output")
                return False

            items = json.loads(parsed)
            # If rclone returns a list with items, the path (or its contents) exists.
            # When pointing directly to a file, rclone returns [ { "Path": "filename", ... } ]
            self.log.debug(f"{remote_path} exists")
            return len(items) > 0
        except Exception as e:
            self.log.exception(f"Path check failed for {remote_path}: {e}")
            return False

    def _get_remote_path(self, path):
        """Helper to format the path for rclone with the remote name."""
        path = f"{self.remote_name}:{path}"
        self.log.debug(f"Remote Path: {path}")
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
