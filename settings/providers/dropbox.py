from pydantic import Field

from openpype.settings import BaseSettingsModel


class ListPerPlatform(BaseSettingsModel):
    windows: list[str] = Field(default_factory=list)
    linux: list[str] = Field(default_factory=list)
    darwin: list[str] = Field(default_factory=list)


class DropboxSubmodel(BaseSettingsModel):
    """Specific settings for Dropbox sites."""
    token: str = Field(
        "",
        title="Access token",
        description="API access token",
    )

    team_folder_name: str = Field(
        "",
        title="Team Folder Name",
    )

    acting_as_member: str = Field(
        "",
        title="Acting As Member",
    )

    root: str = Field(
        "",
        title="Roots",
        description="Root folder on Dropbox",
    )
