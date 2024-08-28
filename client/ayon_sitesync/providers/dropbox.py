import os

import dropbox

from .abstract_provider import AbstractProvider


class DropboxHandler(AbstractProvider):
    CODE = "dropbox"
    LABEL = "Dropbox"

    def __init__(self, project_name, site_name, tree=None, presets=None):
        self.active = False
        self.site_name = site_name
        self.presets = presets
        self.dbx = None

        if not self.presets:
            self.log.info(
                "Sync Server: There are no presets for {}.".format(site_name)
            )
            return

        if not self.presets.get("enabled"):
            self.log.debug("Sync Server: Site {} not enabled for {}.".
                      format(site_name, project_name))
            return

        token = self.presets.get("token", "")
        if not token:
            msg = "Sync Server: No access token for dropbox provider"
            self.log.info(msg)
            return

        team_folder_name = self.presets.get("team_folder_name", "")
        if not team_folder_name:
            msg = "Sync Server: No team folder name for dropbox provider"
            self.log.info(msg)
            return

        acting_as_member = self.presets.get("acting_as_member", "")
        if not acting_as_member:
            msg = (
                "Sync Server: No acting member for dropbox provider"
            )
            self.log.info(msg)
            return

        try:
            self.dbx = self._get_service(
                token, acting_as_member, team_folder_name
            )
        except Exception as e:
            self.log.info("Could not establish dropbox object: {}".format(e))
            return

        super(AbstractProvider, self).__init__()

    def _get_service(self, token, acting_as_member, team_folder_name):
        dbx = dropbox.DropboxTeam(token)

        # Getting member id.
        member_id = None
        member_names = []
        for member in dbx.team_members_list().members:
            member_names.append(member.profile.name.display_name)
            if member.profile.name.display_name == acting_as_member:
                member_id = member.profile.team_member_id

        if member_id is None:
            raise ValueError(
                "Could not find member \"{}\". Available members: {}".format(
                    acting_as_member, member_names
                )
            )

        # Getting team folder id.
        team_folder_id = None
        team_folder_names = []
        for entry in dbx.team_team_folder_list().team_folders:
            team_folder_names.append(entry.name)
            if entry.name == team_folder_name:
                team_folder_id = entry.team_folder_id

        if team_folder_id is None:
            raise ValueError(
                "Could not find team folder \"{}\". Available folders: "
                "{}".format(
                    team_folder_name, team_folder_names
                )
            )

        # Establish dropbox object.
        path_root = dropbox.common.PathRoot.namespace_id(team_folder_id)
        return dropbox.DropboxTeam(
            token
        ).with_path_root(path_root).as_user(member_id)

    def is_active(self):
        """
            Returns True if provider is activated, eg. has working credentials.
        Returns:
            (boolean)
        """
        return self.presets.get("enabled") and self.dbx is not None

    def _path_exists(self, path):
        try:
            entries = self.dbx.files_list_folder(
                path=os.path.dirname(path)
            ).entries
        except dropbox.exceptions.ApiError:
            return False

        for entry in entries:
            if entry.name == os.path.basename(path):
                return True

        return False

    def upload_file(
        self,
        source_path,
        path,
        addon,
        project_name,
        file,
        repre_status,
        site_name,
        overwrite=False
    ):
        """
            Copy file from 'source_path' to 'target_path' on provider.
            Use 'overwrite' boolean to rewrite existing file on provider

        Args:
            source_path (string):
            path (string): absolute path with or without name of the file
            overwrite (boolean): replace existing file

            arguments for saving progress:
            addon (SiteSync): addon instance to call update_db on
            project_name (str):
            file (dict): info about uploaded file (matches structure from db)
            repre_status (dict): complete repre containing 'file'
            site_name (str): site name
        Returns:
            (string) file_id of created file, raises exception
        """
        # Check source path.
        if not os.path.exists(source_path):
            raise FileNotFoundError(
                "Source file {} doesn't exist.".format(source_path)
            )

        if self._path_exists(path) and not overwrite:
            raise FileExistsError(
                "File already exists, use 'overwrite' argument"
            )

        mode = dropbox.files.WriteMode("add", None)
        if overwrite:
            mode = dropbox.files.WriteMode.overwrite

        with open(source_path, "rb") as f:
            file_size = os.path.getsize(source_path)

            CHUNK_SIZE = 50 * 1024 * 1024

            if file_size <= CHUNK_SIZE:
                self.dbx.files_upload(f.read(), path, mode=mode)
            else:
                upload_session_start_result = \
                    self.dbx.files_upload_session_start(f.read(CHUNK_SIZE))

                cursor = dropbox.files.UploadSessionCursor(
                    session_id=upload_session_start_result.session_id,
                    offset=f.tell())

                commit = dropbox.files.CommitInfo(path=path, mode=mode)

                while f.tell() < file_size:
                    if (file_size - f.tell()) <= CHUNK_SIZE:
                        self.dbx.files_upload_session_finish(
                            f.read(CHUNK_SIZE),
                            cursor,
                            commit)
                    else:
                        self.dbx.files_upload_session_append(
                            f.read(CHUNK_SIZE),
                            cursor.session_id,
                            cursor.offset)
                        cursor.offset = f.tell()

        addon.update_db(
            project_name=project_name,
            new_file_id=None,
            file=file,
            repre_status=repre_status,
            site_name=site_name,
            side="remote",
            progress=100
        )

        return path

    def download_file(
        self,
        source_path,
        local_path,
        addon,
        project_name,
        file,
        repre_status,
        site_name,
        overwrite=False
    ):
        """
            Download file from provider into local system

        Args:
            source_path (string): absolute path on provider
            local_path (string): absolute path with or without name of the file
            overwrite (boolean): replace existing file

            arguments for saving progress:
            addon (SiteSync): addon instance to call update_db on
            project_name (str):
            file (dict): info about uploaded file (matches structure from db)
            repre_status (dict): complete repre containing 'file'
            site_name (str): site name
        Returns:
            None
        """
        # Check source path.
        if not self._path_exists(source_path):
            raise FileNotFoundError(
                "Source file {} doesn't exist.".format(source_path)
            )

        if os.path.exists(local_path) and not overwrite:
            raise FileExistsError(
                "File already exists, use 'overwrite' argument"
            )

        if os.path.exists(local_path) and overwrite:
            os.unlink(local_path)

        self.dbx.files_download_to_file(local_path, source_path)

        addon.update_db(
            project_name=project_name,
            new_file_id=None,
            file=file,
            repre_status=repre_status,
            site_name=site_name,
            side="local",
            progress=100
        )

        return os.path.basename(source_path)

    def delete_file(self, path):
        """
            Deletes file from 'path'. Expects path to specific file.

        Args:
            path (string): absolute path to particular file

        Returns:
            None
        """
        if not self._path_exists(path):
            raise FileExistsError("File {} doesn't exist".format(path))

        self.dbx.files_delete(path)

    def list_folder(self, folder_path):
        """
            List all files and subfolders of particular path non-recursively.
        Args:
            folder_path (string): absolut path on provider

        Returns:
            (list)
        """
        if not self._path_exists(folder_path):
            raise FileExistsError(
                "Folder \"{}\" does not exist".format(folder_path)
            )

        entry_names = []
        for entry in self.dbx.files_list_folder(path=folder_path).entries:
            entry_names.append(entry.name)
        return entry_names

    def create_folder(self, folder_path):
        """
            Create all nonexistent folders and subfolders in 'path'.

        Args:
            path (string): absolute path

        Returns:
            (string) folder id of lowest subfolder from 'path'
        """
        if self._path_exists(folder_path):
            return folder_path

        self.dbx.files_create_folder_v2(folder_path)

        return folder_path

    def get_tree(self):
        """
            Creates folder structure for providers which do not provide
            tree folder structure (GDrive has no accessible tree structure,
            only parents and their parents)
        """
        pass

    def get_roots_config(self, anatomy=None):
        """
            Returns root values for path resolving

            Takes value from Anatomy which takes values from Settings
            overridden by Local Settings

        Returns:
            (dict) - {"root": {"root": "/My Drive"}}
                     OR
                     {"root": {"root_ONE": "value", "root_TWO":"value}}
            Format is importing for usage of python's format ** approach
        """
        # TODO implement multiple roots
        return {"root": {"work": self.presets['root']}}

    def resolve_path(self, path, root_config=None, anatomy=None):
        """
            Replaces all root placeholders with proper values

            Args:
                path(string): root[work]/folder...
                root_config (dict): {'work': "c:/..."...}
                anatomy (Anatomy): object of Anatomy
            Returns:
                (string): proper url
        """
        if not root_config:
            root_config = self.get_roots_config(anatomy)

        if root_config and not root_config.get("root"):
            root_config = {"root": root_config}

        try:
            if not root_config:
                raise KeyError

            path = path.format(**root_config)
        except KeyError:
            try:
                path = anatomy.fill_root(path)
            except KeyError:
                msg = "Error in resolving local root from anatomy"
                self.log.error(msg)
                raise ValueError(msg)

        return path
