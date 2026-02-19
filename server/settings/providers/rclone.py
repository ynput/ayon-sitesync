from ayon_server.settings import BaseSettingsModel, SettingsField


class MultiplatformPath(BaseSettingsModel):
    windows: str = SettingsField("", title="Windows")
    linux: str = SettingsField("", title="Linux")
    darwin: str = SettingsField("", title="MacOS")


class RCloneConfigSubModel(BaseSettingsModel):
    enable: bool = SettingsField(False, title="Enable")
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
    key: str = SettingsField(title="Key")
    value: str = SettingsField(title="Value")


class RCloneWebConfigSubModel(BaseSettingsModel):
    enable: bool = SettingsField(False, title="Enable RClone Web Config")
    config_params: list[KeyValueItem] = SettingsField(default_factory=list)


class RCloneSubmodel(BaseSettingsModel):
    """Specific settings for RClone sites."""

    _layout = "collapsed"

    rclone_executable_path: MultiplatformPath = SettingsField(
        default_factory=MultiplatformPath,
        title="RClone Executable Path",
        scope=["studio", "project"],
        description="Path to rclone executable. Leave as 'rclone' if it exists on PATH",
    )

    config_file: RCloneConfigSubModel = SettingsField(
        default_factory=RCloneConfigSubModel,
        title="RClone Config File Settings",
        description="If you can provide an existing rclone.conf file, you can use it here. Otherwise leave empty.",
    )

    config_web: RCloneWebConfigSubModel = SettingsField(
        default_factory=RCloneWebConfigSubModel,
        title="RClone Web Config",
        description="Here you can configure settings that will be propagated to the env.",
    )

    type: str = SettingsField(
        "",
        title="Remote Type",
        scope=["studio", "project"],
        description="e.g. webdav, Use this if you do not have a rclone.conf",
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
