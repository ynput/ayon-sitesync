from pydantic import Field

from ayon_server.settings import BaseSettingsModel
from ayon_server.settings.anatomy.roots import Root, default_roots


class LocalDriveSubmodel(BaseSettingsModel):
    """Specific settings for Local Drive sites."""
    _layout = "compact"
    roots: list[Root] = Field(
        default=default_roots,
        scope=["studio", "project", "site"],
        title="Roots",
        description="Setup root paths for the project",
    )
