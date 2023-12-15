from pydantic import Field

from ayon_server.settings import BaseSettingsModel


class CredPathPerPlatform(BaseSettingsModel):
    windows: str = Field(default_factory=list,
                         scope=["studio", "project", "site"],)
    linux: str = Field(default_factory=list,
                       scope=["studio", "project", "site"],)
    darwin: str = Field(default_factory=list,
                        scope=["studio", "project", "site"],)


class SFTPSubmodel(BaseSettingsModel):
    """Specific settings for SFTP sites.

    Use sftp_pass OR sftp_key (and sftp_key_pass) to authenticate.
    sftp_key is public ssh part, expected .pem OpenSSH format, must be
    accessible on shared drive for all artists, use sftp_pass if no shared
    drive present on artist's machines.
    """
    _layout = "expanded"
    sftp_host: str = Field(
        "",
        title="SFTP host name",
        scope=["studio", "project"],
        description="Domain name or IP of sftp server",
    )

    sftp_port: int = Field(
        0,
        title="SFTP port",
        scope=["studio", "project"],
    )

    sftp_user: str = Field(
        "",
        title="SFTP user name",
        scope=["studio", "project", "site"],
    )

    sftp_pass: str = Field(
        "",
        title="SFTP password",
        scope=["studio", "project", "site"],
        description="Use password or ssh key to authenticate",
    )

    sftp_key: CredPathPerPlatform = Field(
        title="SFTP key path",
        scope=["studio", "project", "site"],
        default_factory=CredPathPerPlatform,
        description="Pah to certificate file",
    )

    sftp_key_pass: str = Field(
        "",
        title="SFTP user ssh key password",
        scope=["studio", "project", "site"],
        description="Password for ssh key",
    )

    roots: str = Field(
        "",
        title="SFTP root folder",
        scope=["studio", "project"],
        description="Root folder on SFTP",
    )
