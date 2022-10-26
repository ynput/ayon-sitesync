from pydantic import Field

from openpype.settings import BaseSettingsModel

from ..settings import ListPerPlatform


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
