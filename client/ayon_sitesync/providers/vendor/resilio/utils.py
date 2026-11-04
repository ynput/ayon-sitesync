
class Path:
    """
    Script model for all platforms

    Paramaters
    ----------
    path : str
        Path to be used for all platforms
    macro : str
        Macro for relative path

    Use public variables `linux`, `win`, `osx` and `android` to set path for corresponding platform
    """

    FOLDERS_STORAGE = '%FOLDERS_STORAGE%'

    def __init__(self, path=None, macro=None):
        self.linux = path if path else ''
        self.win = path if path else ''
        self.osx = path if path else ''
        self.android = path if path else ''

        self.macro = macro

    def get_object(self):
        attrs = {
            'linux': self.linux,
            'win': self.win,
            'osx': self.osx,
            'android': self.android
        }

        if self.macro is not None:
            attrs['macro'] = self.macro

        return attrs

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return hash(self) == hash(other)
        else:
            return False

    def __hash__(self):
        return hash(self.linux) ^ hash(self.win) ^ hash(self.osx) ^ hash(self.android) ^ hash(self.macro)

class Script:
    """
    Script model for all platforms

    Possible platforms are defined in Script.Platform

    Parameters
    ----------
    scripts : dict, optional
        Initial scripts in format {'linux':'Linux script', 'win':'Windows script', 'osx':'Mac script'}
        Default value is {}
    """

    class Platform:
        LINUX = 'linux'
        WINDOWS = 'win'
        MAC = 'osx'

    def __init__(self, scripts={}):
        self._platforms = {}

        self.set_script(self.Platform.LINUX, scripts[self.Platform.LINUX] if self.Platform.LINUX in scripts else '')
        self.set_script(self.Platform.WINDOWS, scripts[self.Platform.WINDOWS] if self.Platform.WINDOWS in scripts else '')
        self.set_script(self.Platform.MAC, scripts[self.Platform.MAC] if self.Platform.MAC in scripts else '')

    def set_script(self, platform, script, shell=None, ext=None):
        """
        Set script for particular platform

        Parameters
        ----------
        platform : Script.Platform
            Platform name
        script : str
            Content of the script for specified platform
        shell : str, optional
            Shell to be used for the script. Default values:
                Linux: /bin/sh
                Windows: cmd.exe /Q /C
                Mac: /bin/sh
        ext : str, optional
            Extention of the script file. Default values:
                Linux: sh
                Windows: cmd
                Mac: sh
        """

        assert platform in [self.Platform.LINUX, self.Platform.WINDOWS, self.Platform.MAC], 'Unknown platform'

        self._platforms[platform] = { 'script': script }
        if shell:
            self._platforms[platform]['shell'] = shell
        if ext:
            self._platforms[platform]['ext'] = ext

    def get_object(self):
        return self._platforms
