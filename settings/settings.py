from pydantic import Field, validator

from openpype.settings import (
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
        "gdrive": "Google Drive",
        "local_drive": "Local Drive",
        "dropbox": "Dropbox",
        "sftp": "SFTP"
    }
    return [{"value": f"{key}", "label": f"{label}"}
            for key, label in provider_dict.items()]



class SitesSubmodel(BaseSettingsModel):
    _layout = "expanded"

    alternative_sites: list[str] = Field(
        default_factory=list,
        title="Alternative sites",
        description="Files on this site are/should physically present on these"
                    " sites. Example sftp site exposes files from 'studio' "
                    " site"
    )

    provider = Field(
        "Local Drive",
        title="Provider enum",
        enum_resolver=provider_resolver,
    )

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

    # Implemented providers settings are in `ADDON_ROOT/settings/provicers`
    # Here only example usage (must be imported first!)
    # TODO tie change of provider enum value to load particular settings
    # currently not implemented in core code
    local_drive_settings: LocalDriveSubmodel = Field(
        default_factory=LocalDriveSubmodel, title="Local Drive")

    google_drive_settings: GoogleDriveSubmodel = Field(
        default_factory=GoogleDriveSubmodel, title="Google Drive")

    dropbox_settings: DropboxSubmodel = Field(
        default_factory=DropboxSubmodel, title="Dropbox")

    sftp_settings: SFTPSubmodel = Field(
        default_factory=SFTPSubmodel, title="SFTP")

    @validator("sites")
    def ensure_unique_names(cls, value):
        """Ensure name fields within the lists have unique names."""
        ensure_unique_names(value)
        return value
