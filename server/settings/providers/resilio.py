from pydantic import Field

from ayon_server.settings import BaseSettingsModel
from ayon_server.settings.anatomy.roots import Root, default_roots


class ResilioSubmodel(BaseSettingsModel):
    """Specific settings for Resilio sites.

    token: API token for site
    root: root folder on Resilio
    """
    _layout = "expanded"

    host: str = Field(
        "",
        title="Resilio Management Console host name",
        scope=["studio", "project"],
        description="Domain name or IP of sftp server",
    )

    port: int = Field(
        0,
        title="Resilio Management Console port",
        scope=["studio", "project"],
        placeholder="8443"
    )

    token: str = Field(
        "",
        title="Access token",
        scope=["studio", "project", "site"],
        description="API access token",
    )

    agent_id: int = Field(
        0,
        title="Agent id",
        scope=["studio", "project"],
    )

    roots: list[Root] = Field(
        default=default_roots,
        scope=["studio", "project"],
        title="Roots",
        description="Setup root paths for the project",
    )
