from pydantic import Field

from openpype.settings import BaseSettingsModel
from openpype.settings.anatomy.roots import Root, default_roots

from ..settings import ListPerPlatform


class SFTPSubmodel(BaseSettingsModel):
    """Specific settings for SFTP sites.

    Use sftp_pass OR sftp_key (and sftp_key_pass) to authenticate.
    sftp_key is public ssh part, expected .pem OpenSSH format, must be
    accessible on shared drive for all artists, use sftp_pass if no shared
    drive present on artist's machines.
    """

    sftp_host: str = Field(
        "",
        title="SFTP host name",
        description="Domain name or IP of sftp server",
    )

    sftp_port: int = Field(
        "",
        title="SFTP port",
    )

    sftp_user: str = Field(
        "",
        title="SFTP user name"
    )

    sftp_pass: str = Field(
        "",
        title="SFTP password",
        description="Use password or ssh key to authenticate",
    )

    sftp_key: ListPerPlatform = Field(
        title="SFTP password",
        default_factory=ListPerPlatform,
        description="Use password or ssh key to authenticate",
    )

    roots: list[Root] = Field(
        default=default_roots,
        title="Roots",
        description="Setup root paths for the project",
    )
