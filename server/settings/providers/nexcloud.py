from pydantic import Field
from ayon_server.settings import BaseSettingsModel


class NextcloudSubmodel(BaseSettingsModel):
    """Specific settings for Nextcloud sites using WebDAV."""

    _layout = "expanded"

    url: str = Field(
        "",
        title="WebDAV URL",
        scope=["studio", "project", "site"],
        description="Nextcloud WebDAV URL (e.g., https://your-cloud.com/remote.php/dav)",
    )

    username: str = Field(
        "",
        title="User name",
        scope=["studio", "project", "site"],
    )

    password: str = Field(
        "",
        title="Password",
        scope=["studio", "project", "site"],
        widget="password",
    )

    root: str = Field(
        "",
        title="Nextcloud root folder",
        scope=["studio", "project", "site"],
        description="Root folder on Nextcloud (e.g., /files/username/project_data)",
    )