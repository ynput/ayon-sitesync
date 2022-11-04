from pydantic import Field

from openpype.settings import BaseSettingsModel
from openpype.settings.anatomy.roots import Root, default_roots


class LocalDriveSubmodel(BaseSettingsModel):
    """Specific settings for Local Drive sites."""
    _layout = "compact"
    roots: list[Root] = Field(
        default=default_roots,
        title="Roots",
        description="Setup root paths for the project",
    )
