from pydantic import Field

from ayon_server.settings import BaseSettingsModel


class ListPerPlatform(BaseSettingsModel):
    windows: list[str] = Field(default_factory=list)
    linux: list[str] = Field(default_factory=list)
    darwin: list[str] = Field(default_factory=list)


class DropboxSubmodel(BaseSettingsModel):
    """Specific settings for Dropbox sites."""
    _layout = "expanded"
    token: str = Field(
        "",
        title="Access token",
        scope=["studio", "project", "site"],
        description="API access token",
    )

    team_folder_name: str = Field(
        "",
        title="Team Folder Name",
        scope=["studio", "project"],
    )

    acting_as_member: str = Field(
        "",
        title="Acting As Member",
        scope=["studio", "project", "site"],
    )

    roots: str = Field(
        "",
        title="Roots",
        scope=["studio", "project", "site"],
        description="Root folder on Dropbox",
    )
