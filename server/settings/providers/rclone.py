from ayon_server.settings import BaseSettingsModel, SettingsField


class MultiplatformPath(BaseSettingsModel):
    windows: str = SettingsField("", title="Windows")
    linux: str = SettingsField("", title="Linux")
    darwin: str = SettingsField("", title="MacOS")


def _config_type_enum():
    return [
        {"value": "config_file", "label": "Use File Config"},
        {"value": "config_web", "label": "Use Web Config"},
    ]


class RCloneConfigSubModel(BaseSettingsModel):
    remote_name: str = SettingsField(
        "",
        title="Remote Name from the rclone config",
        scope=["studio", "project"],
        description="The name of the remote as defined in rclone.conf",
    )
    rclone_config_path: MultiplatformPath = SettingsField(
        default_factory=MultiplatformPath,
        title="RClone Config Path (.conf)",
        scope=["studio", "project"],
        description="Path to the rclone.conf file",
    )


class KeyValueItem(BaseSettingsModel):
    _layout = "compact"
    key: str = SettingsField(title="Key")
    value: str = SettingsField(title="Value")


class RCloneWebConfigSubModel(BaseSettingsModel):
    type: str = SettingsField(
        "",
        title="Type",
        scope=["studio", "project"],
        description="e.g. webdav, S3",
    )
    config_params: list[KeyValueItem] = SettingsField(
        default_factory=list, title="Config Parameters"
    )


class RCloneSubmodel(BaseSettingsModel):
    """Specific settings for RClone sites."""

    rclone_executable_path: MultiplatformPath = SettingsField(
        default_factory=MultiplatformPath,
        title="RClone Executable Path",
        scope=["studio", "project"],
        description="Path to rclone executable. Leave as 'rclone' if it exists on PATH or environment variable expansion is possible. Syntax {LOCALAPPDATA}/path.",
    )

    config_type: str = SettingsField(
        "config_web",
        title="Config Type",
        enum_resolver=_config_type_enum,
        conditional_enum=True,
        description="Choose between file-based config (.conf) or web-based config (define inline)",
    )

    config_file: RCloneConfigSubModel = SettingsField(
        default_factory=RCloneConfigSubModel,
        title="RClone File Config",
        description="Provide an existing rclone.conf file, environment variable expansion is possible. Syntax {LOCALAPPDATA}/path ",
    )

    config_web: RCloneWebConfigSubModel = SettingsField(
        default_factory=RCloneWebConfigSubModel,
        title="RClone Web Config",
        description="Define all required keys from the rclone.conf, no need to distribute a file.",
    )

    root: str = SettingsField(
        "",
        title="Root Folder",
        scope=["studio", "project"],
        description="Root folder on the remote storage.",
    )

    additional_args: list[str] = SettingsField(
        default_factory=list,
        title="Additional Arguments",
        scope=["studio", "project"],
        description="Extra flags for rclone (e.g. ['--checkers=16'])",
    )
