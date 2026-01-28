import typing
from pydantic import Field, validator

from ayon_server.settings import (
    BaseSettingsModel,
    ensure_unique_names,
    normalize_name,
)

from .providers.local_drive import LocalDriveSubmodel
from .providers.gdrive import GoogleDriveSubmodel
from .providers.dropbox import DropboxSubmodel
from .providers.sftp import SFTPSubmodel
from .providers.rclone import RCloneSubmodel


if typing.TYPE_CHECKING:
    from ayon_server.addons import BaseServerAddon


class GeneralSubmodel(BaseSettingsModel):
    """Properties for loop and module configuration"""
    retry_cnt: int = Field(3, title="Retry Count")
    loop_delay: int = Field(60, title="Loop Delay")
    always_accessible_on: list[str] = Field([],
                                            title="Always accessible on sites")
    active_site: str = Field("studio", title="User Default Active Site")
    remote_site: str = Field("studio", title="User Default Remote Site")


class RootSubmodel(BaseSettingsModel):
    """Setup root paths for local site.

    Studio roots overrides are in separate `Roots` tab outside of Site Sync.
    """

    _layout: str = "expanded"

    name: str = Field(
        "work",
        title="Root name",
        regex="^[a-zA-Z0-9_]{1,}$",
        scope=["site"],
    )

    path: str = Field(
        "c:/projects_local",
        title="Path",
        scope=["site"],
    )


default_roots = [
    RootSubmodel(
        name="work",
        path="C:/projects_local",
    )
]


def provider_resolver():
    """Return a list of value/label dicts for the enumerator.

    Returning a list of dicts is used to allow for a custom label to be
    displayed in the UI.
    """
    provider_dict = {
        "gdrive": "Google Drive",
        "local_drive": "Local Drive",
        "dropbox": "Dropbox",
        "sftp": "SFTP",
        "rclone": "Rclone"
    }
    return [{"value": f"{key}", "label": f"{label}"}
            for key, label in provider_dict.items()]


async def defined_sited_enum_resolver(
    addon: "BaseServerAddon",
    settings_variant: str = "production",
    project_name: str | None = None,
) -> list[str]:
    """Provides list of names of configured syncable sites."""
    if addon is None:
        return []

    if project_name:
        settings = await addon.get_project_settings(project_name=project_name,
                                                    variant=settings_variant)
    else:
        settings =  await addon.get_studio_settings(variant=settings_variant)

    sites = ["local", "studio"]
    for site_model in settings.sites:
        sites.append(site_model.name)

    return sites


provider_enum = provider_resolver()


class SitesSubmodel(BaseSettingsModel):
    """Configured additional sites and properties for their providers"""
    _layout = "expanded"

    alternative_sites: list[str] = Field(
        default_factory=list,
        title="Alternative sites",
        scope=["studio", "project"],
        description="Files on this site are/should physically present on these"
                    " sites. Example sftp site exposes files from 'studio' "
                    " site"
    )

    provider: str = Field(
        "",
        title="Provider",
        description="Switch between providers",
        enum_resolver=lambda: provider_enum,
        conditional_enum=True
    )

    local_drive: LocalDriveSubmodel = Field(
        default_factory=LocalDriveSubmodel,
        scope=["studio", "project", "site"]
    )
    gdrive: GoogleDriveSubmodel = Field(
        default_factory=GoogleDriveSubmodel,
        scope=["studio", "project", "site"]
    )
    dropbox: DropboxSubmodel = Field(
        default_factory=DropboxSubmodel,
        scope=["studio", "project", "site"]
    )
    sftp: SFTPSubmodel = Field(
        default_factory=SFTPSubmodel,
        scope=["studio", "project", "site"]
    )
    rclone: RCloneSubmodel = Field(
        default_factory=RCloneSubmodel,
        scope=["studio", "project", "site"]
    )

    name: str = Field(..., title="Site name",
                      scope=["studio", "project", "site"])

    @validator("name")
    def validate_name(cls, value):
        """Ensure name does not contain weird characters"""
        return normalize_name(value)


class LocalSubmodel(BaseSettingsModel):
    """Select your local and remote site"""
    active_site: str = Field("",
                             title="My Active Site",
                             scope=["site"],
                             enum_resolver=defined_sited_enum_resolver)

    remote_site: str = Field("",
                             title="My Remote Site",
                             scope=["site"],
                             enum_resolver=defined_sited_enum_resolver)

    local_roots: list[RootSubmodel] = Field(
        default=default_roots,
        title="Local roots overrides",
        scope=["site"],
        description="Overrides for local root(s)."
    )


class SiteSyncSettings(BaseSettingsModel):
    """Settings for synchronization process"""
    enabled: bool = Field(False)

    config: GeneralSubmodel = Field(
        default_factory=GeneralSubmodel,
        title="Config"
    )

    local_setting: LocalSubmodel = Field(
        default_factory=LocalSubmodel,
        title="Local setting",
        scope=["site"],
        description="This setting is only applicable for artist's site",
    )

    sites: list[SitesSubmodel] = Field(
        default_factory=list,
        scope=["studio", "project", "site"],
        title="Sites",
    )

    @validator("sites")
    def ensure_unique_names(cls, value):
        """Ensure name fields within the lists have unique names."""
        ensure_unique_names(value)
        return value
