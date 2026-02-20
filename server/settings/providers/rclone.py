from ayon_server.settings import BaseSettingsModel, SettingsField


class MultiplatformPath(BaseSettingsModel):
    windows: str = SettingsField("", title="Windows")
    linux: str = SettingsField("", title="Linux")
    darwin: str = SettingsField("", title="MacOS")


class RCloneConfigSubModel(BaseSettingsModel):
    enabled: bool = SettingsField(False, title="Enable")
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
        description="Path to the rclone.conf file. Or use the settings below. Then leave this empty",
    )


class KeyValueItem(BaseSettingsModel):
    _layout = "compact"
    key: str = SettingsField(title="Key")
    value: str = SettingsField(title="Value")


class RCloneWebConfigSubModel(BaseSettingsModel):
    enabled: bool = SettingsField(False, title="Enable RClone Web Config")
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
    info_note: str = SettingsField(
        "Choose either one Setting below: file config (*.conf) or web config, declare all here. If both are active config is used first.",
        title="⚠️ Important",
        description="Choose either one Setting below: file config (*.conf) or web config, declare all here. If both are active config is used first.",
        widget="label",
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
