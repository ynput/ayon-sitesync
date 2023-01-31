from pydantic import Field, validator

from ayon_server.settings import (
    BaseSettingsModel,
    ensure_unique_names,
    normalize_name)

from .providers.local_drive import LocalDriveSubmodel
from .providers.gdrive import GoogleDriveSubmodel
from .providers.dropbox import DropboxSubmodel
from .providers.sftp import SFTPSubmodel


def provider_resolver():
    """Return a list of value/label dicts for the enumerator.

    Returning a list of dicts is used to allow for a custom label to be
    displayed in the UI.
    """
    provider_dict = {
        "gdrive_model": "Google Drive",
        "local_model": "Local Drive",
        "dropbox_model": "Dropbox",
        "sftp_model": "SFTP"
    }
    return [{"value": f"{key}", "label": f"{label}"}
            for key, label in provider_dict.items()]


provider_enum = provider_resolver()


class SitesSubmodel(BaseSettingsModel):
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

    local_model: LocalDriveSubmodel = Field(default_factory=LocalDriveSubmodel)
    gdrive_model: GoogleDriveSubmodel = Field(
        default_factory=GoogleDriveSubmodel)
    dropbox_model: DropboxSubmodel = Field(default_factory=DropboxSubmodel)
    sftp_model: SFTPSubmodel = Field(default_factory=SFTPSubmodel)

    name: str = Field(..., title="Site name")

    @validator("name")
    def validate_name(cls, value):
        """Ensure name does not contain weird characters"""
        return normalize_name(value)


class SiteSyncSettings(BaseSettingsModel):
    """Test addon settings"""
    sites: list[SitesSubmodel] = Field(
        default_factory=list,
        title="Sites",
    )

    @validator("sites")
    def ensure_unique_names(cls, value):
        """Ensure name fields within the lists have unique names."""
        ensure_unique_names(value)
        return value
