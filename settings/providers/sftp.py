from pydantic import Field

from openpype.settings import BaseSettingsModel
from openpype.settings.anatomy.roots import Root, default_roots

class ListPerPlatform(BaseSettingsModel):
    windows: list[str] = Field(default_factory=list)
    linux: list[str] = Field(default_factory=list)
    darwin: list[str] = Field(default_factory=list)


class SFTPSubmodel(BaseSettingsModel):
    """Specific settings for SFTP sites.

    Use sftp_pass OR sftp_key (and sftp_key_pass) to authenticate.
    sftp_key is public ssh part, expected .pem OpenSSH format, must be
    accessible on shared drive for all artists, use sftp_pass if no shared
    drive present on artist's machines.
    """
    _layout = "compact"
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
        title="SFTP key path",
        default_factory=ListPerPlatform,
        description="Use password or ssh key to authenticate",
    )

    sftp_key_pass: str = Field(
        "",
        title="SFTP user ssh key password",
        description="Password for ssh key",
    )

    roots: list[Root] = Field(
        default=default_roots,
        title="Roots",
        description="Setup root paths for the project",
    )
