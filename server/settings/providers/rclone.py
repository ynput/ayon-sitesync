from pydantic import Field
from ayon_server.settings import BaseSettingsModel


class MultiplatformPath(BaseSettingsModel):
    windows: str = Field("", title="Windows")
    linux: str = Field("", title="Linux")
    darwin: str = Field("", title="MacOS")


class RCloneSubmodel(BaseSettingsModel):
    """Specific settings for RClone sites."""
    _layout = "expanded"

    rclone_executable_path: MultiplatformPath = Field(
        default_factory=MultiplatformPath,
        title="RClone Executable Path",
        scope=["studio", "project", "site"],
        description="Path to rclone executable. Leave as 'rclone' to use system PATH"
    )

    rclone_config_path: MultiplatformPath = Field(
        default_factory=MultiplatformPath,
        title="RClone Config Path (.conf)",
        scope=["studio", "project", "site"],
        description="Path to the rclone.conf file."
    )

    remote_name: str = Field(
        "nextcloud",
        title="Remote Name",
        scope=["studio", "project", "site"],
        description="The name of the remote as defined in rclone.conf"
    )

    root: str = Field(
        "",
        title="Root Folder",
        scope=["studio", "project", "site"],
        description="Root folder on the remote storage."
    )

    password: str = Field(
        "",
        title="Password / Token",
        scope=["studio", "project", "site"],
        description="Optional password. Will be obscured before passing to rclone."
    )

    additional_args: list[str] = Field(
        default_factory=list,
        title="Additional Arguments",
        description="Extra flags for rclone (e.g. ['--webdav-nextcloud-chunk-size', '0'])"
    )