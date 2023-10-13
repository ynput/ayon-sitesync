from pydantic import Field, validator

from ayon_server.settings import (
    BaseSettingsModel,
    ensure_unique_names,
    normalize_name)

from ayon_server.settings.anatomy.roots import Root

from .providers.local_drive import LocalDriveSubmodel
from .providers.gdrive import GoogleDriveSubmodel
from .providers.dropbox import DropboxSubmodel
from .providers.sftp import SFTPSubmodel


class GeneralSubmodel(BaseSettingsModel):
    """Properties for loop and module configuration"""
    retry_cnt: int = Field(3, title="Retry Count")
    loop_delay: int = Field(60, title="Loop Delay")
    always_accessible_on: list[str] = Field([],
                                            title="Always accessible on sites")
    active_site: str = Field("studio", title="User Default Active Site")
    remote_site: str = Field("studio", title="User Default Remote Site")


def provider_resolver():
    """Return a list of value/label dicts for the enumerator.

    Returning a list of dicts is used to allow for a custom label to be
    displayed in the UI.
    """
    provider_dict = {
        "gdrive": "Google Drive",
        "local": "Local Drive",
        "dropbox": "Dropbox",
        "sftp": "SFTP"
    }
    return [{"value": f"{key}", "label": f"{label}"}
            for key, label in provider_dict.items()]


provider_enum = provider_resolver()


class SitesSubmodel(BaseSettingsModel):
    """Configured additional sites and properties for their providers"""
    _layout = "expanded"

    alternative_sites: list[str] = Field(
        default_factory=list,
        title="Alternative sites",
        description="Files on this site are/should physically present on these"
                    " sites. Example sftp site exposes files from 'studio' "
                    " site"
    )

    provider: str = Field(
        "",
        title="Provider",
        description="Switch between providers",
        enum_resolver=lambda: provider_enum,
        conditionalEnum=True
    )

    local_drive: LocalDriveSubmodel = Field(default_factory=LocalDriveSubmodel)
    gdrive: GoogleDriveSubmodel = Field(
        default_factory=GoogleDriveSubmodel)
    dropbox: DropboxSubmodel = Field(default_factory=DropboxSubmodel)
    sftp: SFTPSubmodel = Field(default_factory=SFTPSubmodel)

    name: str = Field(..., title="Site name")

    @validator("name")
    def validate_name(cls, value):
        """Ensure name does not contain weird characters"""
        return normalize_name(value)


class LocalSubmodel(BaseSettingsModel):
    """Select your local and remote site"""
    active_site: str = Field("",
                             title="My Active Site",
                             scope=["site"],
                             enum_resolver=lambda: ["local", "studio"])
    active_site_root: str = Field("", title="Root", scope=["site"])  # TODO show only for local_drive sites

    remote_site: str = Field("",
                             title="My Remote Site",
                             scope=["site"],
                             enum_resolver=lambda: ["local", "studio"])  # TODO should query configured sites for project
    remote_site_root: str = Field("", title="Root", scope=["site"])  # TODO show only for local_drive sites


class SiteSyncSettings(BaseSettingsModel):
    """Settings for synchronization process"""
    enabled: bool = Field(False)

    config: GeneralSubmodel = Field(
        default_factory=GeneralSubmodel,
        title="Config"
    )

    sites: list[SitesSubmodel] = Field(
        default_factory=list,
        title="Sites",
    )

    local_setting: LocalSubmodel = Field(
        default_factory=LocalSubmodel,
        title="Local setting",
        scope=["site"],
        description="This setting is only applicable for artist's site",
    )

    @validator("sites")
    def ensure_unique_names(cls, value):
        """Ensure name fields within the lists have unique names."""
        ensure_unique_names(value)
        return value
