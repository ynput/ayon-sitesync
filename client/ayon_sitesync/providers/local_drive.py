from __future__ import print_function
import os.path
import shutil
import threading
import time

from ayon_core.lib import Logger
from ayon_core.pipeline import Anatomy
from .abstract_provider import AbstractProvider

from ayon_core.addon import AddonsManager

log = Logger.get_logger("SiteSync")


class LocalDriveHandler(AbstractProvider):
    CODE = "local_drive"
    LABEL = "Local drive"

    """ Handles required operations on mounted disks with OS """
    def __init__(self, project_name, site_name, tree=None, presets=None):
        self.presets = None
        self.active = False
        self.project_name = project_name
        self.site_name = site_name
        self._editable_properties = {}

        self.active = self.is_active()

    def is_active(self):
        return True

    def upload_file(self, source_path, target_path,
                    server, project_name, file, representation, site,
                    overwrite=False, direction="Upload"):
        """
            Copies file from 'source_path' to 'target_path'
        """
        if not os.path.isfile(source_path):
            raise FileNotFoundError("Source file {} doesn't exist."
                                    .format(source_path))

        if overwrite:
            thread = threading.Thread(target=self._copy,
                                      args=(source_path, target_path))
            thread.start()
            self._mark_progress(project_name, file, representation, server,
                                site, source_path, target_path, direction)
        else:
            if os.path.exists(target_path):
                raise ValueError("File {} exists, set overwrite".
                                 format(target_path))

        return os.path.basename(target_path)

    def download_file(self, source_path, local_path,
                      server, project_name, file, representation, site,
                      overwrite=False):
        """
            Download a file form 'source_path' to 'local_path'
        """
        return self.upload_file(source_path, local_path,
                                server, project_name, file,
                                representation, site,
                                overwrite, direction="Download")

    def delete_file(self, path):
        """
            Deletes a file at 'path'
        """
        if os.path.exists(path):
            os.remove(path)

    def list_folder(self, folder_path):
        """
            Returns list of files and subfolder in a 'folder_path'. Non recurs
        """
        lst = []
        if os.path.isdir(folder_path):
            for (dir_path, dir_names, file_names) in os.walk(folder_path):
                for name in file_names:
                    lst.append(os.path.join(dir_path, name))
                for name in dir_names:
                    lst.append(os.path.join(dir_path, name))

        return lst

    def create_folder(self, folder_path):
        """
            Creates 'folder_path' on local system

            Args:
                folder_path (string): absolute path on local (and mounted) disk

            Returns:
                (string) - sends back folder_path to denote folder(s) was
                    created
        """
        os.makedirs(folder_path, exist_ok=True)
        return folder_path

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
        site_name = self._normalize_site_name(self.site_name)
        if not anatomy:
            anatomy = Anatomy(self.project_name,
                              site_name)

        # TODO cleanup when Anatomy will implement siteRoots method
        roots = anatomy.roots
        root_values = [root.value for root in roots.values()]
        if not all(root_values):
            manager = AddonsManager()
            sitesync_addon = manager.get_enabled_addon("sitesync")
            if not sitesync_addon:
                raise RuntimeError("No SiteSync addon")
            roots = sitesync_addon._get_project_roots_for_site(
                self.project_name, site_name)

        return {'root': roots}

    def get_tree(self):
        return

    def _copy(self, source_path, target_path):
        print("copying {}->{}".format(source_path, target_path))
        try:
            shutil.copy(source_path, target_path)
        except shutil.SameFileError:
            print("same files, skipping")

    def _mark_progress(self, project_name, file, representation, server,
                       site_name, source_path, target_path, direction):
        """
            Updates progress field in DB by values 0-1.

            Compares file sizes of source and target.
        """
        source_file_size = os.path.getsize(source_path)
        target_file_size = 0
        last_tick = status_val = None
        side = "local"
        if direction == "Upload":
            side = "remote"
        while source_file_size != target_file_size:
            if not last_tick or \
                    time.time() - last_tick >= server.LOG_PROGRESS_SEC:
                status_val = target_file_size / source_file_size
                last_tick = time.time()
                log.debug(direction + "ed %d%%." % int(status_val * 100))
                server.update_db(project_name=project_name,
                                 new_file_id=None,
                                 file=file,
                                 representation=representation,
                                 site_name=site_name,
                                 side=side,
                                 progress=status_val
                                 )
            try:
                target_file_size = os.path.getsize(target_path)
            except FileNotFoundError:
                pass
            time.sleep(0.5)

    def _normalize_site_name(self, site_name):
        """Transform user id to 'local' for Local settings"""
        if site_name != 'studio':
            return 'local'
        return site_name
