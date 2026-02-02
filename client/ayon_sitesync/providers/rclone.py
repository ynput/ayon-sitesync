from __future__ import annotations
import os
import json
import subprocess
import platform
from typing import TYPE_CHECKING

from .abstract_provider import AbstractProvider

if TYPE_CHECKING:
    from ayon_sitesync.addon import SiteSyncAddon

class RCloneHandler(AbstractProvider):
    CODE = "rclone"
    LABEL = "RClone"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        super().__init__(project_name, site_name, tree, presets)
        self.project_name = project_name
        self.presets = presets or {}
        self.rclone_path = self.presets.get("rclone_executable_path", {}).get(
            platform.system().lower(), "rclone")

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

        # in case the remote name is not set, use the vendor name which works for the env connection
        self.remote_name = self.presets.get("remote_name")
        if not self.remote_name:
            self.remote_name = self.vendor

        self._tree = tree

    def is_active(self) -> bool:
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
            source_path: str,
            target_path: str,
            addon: SiteSyncAddon,
            project_name: str,
            file: dict,
            repre_status: dict,
            site_name: str,
            overwrite: bool = False
    ) -> str:
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
            source_path: str,
            local_path: str,
            addon: SiteSyncAddon,
            project_name: str,
            file: dict,
            repre_status: dict,
            site_name: str,
            overwrite: bool = False
    ) -> str:
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

    def delete_file(self, path: str) -> None:
        """Delete a file from the remote."""
        remote_path = self._get_remote_path(path)
        args = ["deletefile", f"{remote_path}"]
        try:
            self._run_rclone(args)
            self.log.debug(f"Successfully deleted {path} on {self.remote_name}")
        except RuntimeError as e:
            self.log.error(f"Failed to delete {path}: {e}")
            raise FileNotFoundError(
                f"Failed to delete {path} on {self.remote_name}")

    def list_folder(self, folder_path: str) -> list[str]:
        """List all files in a folder non-recursively."""
        remote_folder_path = self._get_remote_path(folder_path)
        args = ["lsjson", remote_folder_path]
        raw_json = self._run_rclone(args)
        if not raw_json:
            return []
        items = json.loads(raw_json)
        data = []
        for item in items:
            name = item["Name"]
            if not name.startswith("/"):
                name = "/" + name
            data.append(name)
        self.log.debug(f"Fetched list of files in {folder_path}: {data}")
        return data

    def create_folder(self, folder_path: str) -> str:
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

    def get_roots_config(self, anatomy=None) -> dict:
        """Returns root values for path resolving."""
        return {"root": {"work": self.presets.get('root', '/')}}

    # Helper methods
    def _run_rclone(self, args: list[str]) -> str:
        """Internal helper to execute rclone commands with extra args."""
        cmd = [self.rclone_path]
        if self._config_path:
            cmd.extend(["--config", self._config_path])

        if self.extra_args:
            cmd.extend(self.extra_args)
        cmd.extend(args)

        env = os.environ.copy()
        if self.presets.get("password"):
            # Format: RCLONE_CONFIG_<UPPERCASE_REMOTE_NAME>_PASS
            env_key = f"RCLONE_CONFIG_{self.remote_name.upper()}_PASS"
            env[env_key] = self._obscure_pass(self.presets.get("password"))

        if not self._config_path:
            env_vendor = f"RCLONE_CONFIG_{self.remote_name.upper()}_VENDOR"
            env[env_vendor] = self.vendor
            env_type = f"RCLONE_CONFIG_{self.remote_name.upper()}_TYPE"
            env[env_type] = self.tipe
            env_url = f"RCLONE_CONFIG_{self.remote_name.upper()}_URL"
            env[env_url] = self.url
            env_user = f"RCLONE_CONFIG_{self.remote_name.upper()}_USER"
            env[env_user] = self.user
            # Set RCLONE_CONFIG to null device to prevent rclone from
            # trying to find/create a default config file.
            null_device = "NUL" if platform.system().lower() == "windows" else "/dev/null"
            env["RCLONE_CONFIG"] = null_device

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
            # Rclone exit code 3 means directory not found
            if p.returncode == 3 and "lsjson" in args:
                self.log.info(f"{p.stderr}")
                return ""

            raise RuntimeError(
                "rclone failed\n"
                f"exit_code: {p.returncode}\n"
                f"stdout:\n{p.stdout}\n"
                f"stderr:\n{p.stderr}"
            )

        out = p.stdout
        return out

    def _path_exists(self, path: str) -> bool:
        """Checks if path exists on remote."""
        remote_path = self._get_remote_path(path)
        args = ["lsjson", remote_path]
        try:
            output = self._run_rclone(args)
            if not output:
                return False

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
            self.log.error(f"Path check failed for {remote_path}: {e}")
            return False

    def _get_remote_path(self, path: str) -> str:
        """Helper to format the path for rclone with the remote name."""
        path = f"{self.remote_name}:{path}"
        self.log.debug(f"Remote Path: {path}")
        return path

    def _obscure_pass(self, password: str) -> str:
        # Rclone expects passwords in env vars to be obscured
        # You can call 'rclone obscure' via subprocess to get this string
        cmd = [self.rclone_path, "obscure", password]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return output.decode().strip()
        except FileNotFoundError as e:
            self.log.error(
                f"Failed to run rclone for obscuring password: "
                f"executable not found at '{self.rclone_path}'"
            )
            raise RuntimeError(
                "rclone executable not found for password obscuring"
            ) from e
        except subprocess.CalledProcessError as e:
            output = e.output.decode("utf-8", errors="replace") if e.output else ""
            self.log.error(
                "rclone 'obscure' command failed with exit code %s and output: %s",
                e.returncode,
                output,
            )
            raise RuntimeError(
                "Failed to obscure password with rclone"
            ) from e

    @staticmethod
    def _parse_rclone_json(output: str | bytes) -> str | None:
        """Parses JSON from rclone output, stripping leading/trailing non-JSON text."""
        if not output:
            return None

        if isinstance(output, (bytes, bytearray)):
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
