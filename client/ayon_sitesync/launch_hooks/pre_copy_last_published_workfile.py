import os
import shutil

from ayon_api import get_repre_ids_by_context_filters, get_representations
from ayon_applications import PreLaunchHook
from ayon_core.lib.profiles_filtering import filter_profiles
from ayon_sitesync.sitesync import download_last_published_workfile
from ayon_core.pipeline.template_data import get_template_data
from ayon_core.pipeline.workfile import get_workfile_template_key
from ayon_core.settings import get_project_settings


class CopyLastPublishedWorkfile(PreLaunchHook):
    """Copy last published workfile as first workfile.

    Prelaunch hook works only if last workfile leads to not existing file.
        - That is possible only if it's first version.
    """

    # Before `AddLastWorkfileToLaunchArgs`
    order = -1
    # any DCC could be used but TrayPublisher and other specials
    app_groups = ["blender", "photoshop", "tvpaint", "aftereffects",
                  "nuke", "nukeassist", "nukex", "hiero", "nukestudio",
                  "maya", "harmony", "celaction", "flame", "fusion",
                  "houdini", "tvpaint"]

    def execute(self):
        """Check if local workfile doesn't exist, else copy it.

        1- Check if setting for this feature is enabled
        2- Check if workfile in work area doesn't exist
        3- Check if published workfile exists and is copied locally in publish
        4- Substitute copied published workfile as first workfile
           with incremented version by +1

        Returns:
            None: This is a void method.
        """
        sitesync_addon = self.addons_manager.get("sitesync")
        if not sitesync_addon or not sitesync_addon.enabled:
            self.log.debug("Sync server module is not enabled or available")
            return

        # Check there is no workfile available
        last_workfile = self.data.get("last_workfile_path")
        if os.path.exists(last_workfile):
            self.log.debug(
                "Last workfile exists. Skipping {} process.".format(
                    self.__class__.__name__
                )
            )
            return

        # Get data
        project_name = self.data["project_name"]
        task_name = self.data["task_name"]
        task_type = self.data["task_type"]
        host_name = self.application.host_name

        # Check settings has enabled it
        project_settings = get_project_settings(project_name)
        profiles = project_settings["core"]["tools"]["Workfiles"][
            "last_workfile_on_startup"
        ]
        filter_data = {
            "tasks": task_name,
            "task_types": task_type,
            "hosts": host_name,
        }
        last_workfile_settings = filter_profiles(profiles, filter_data)
        if not last_workfile_settings:
            return
        use_last_published_workfile = last_workfile_settings.get(
            "use_last_published_workfile"
        )
        if use_last_published_workfile is None:
            self.log.info(
                (
                    "Seems like old version of settings is used."
                    ' Can\'t access custom templates in host "{}".'.format(
                        host_name
                    )
                )
            )
            return
        elif use_last_published_workfile is False:
            self.log.info(
                (
                    'Project "{}" has turned off to use last published'
                    ' workfile as first workfile for host "{}"'.format(
                        project_name, host_name
                    )
                )
            )
            return

        max_retries = int((sitesync_addon.sync_project_settings[project_name]
                                                               ["config"]
                                                               ["retry_cnt"]))

        self.log.info("Trying to fetch last published workfile...")

        folder_entity = self.data.get("folder_entity")
        anatomy = self.data.get("anatomy")
        hierarchy = "/".join(folder_entity["path"].split("/")[:-1]).lstrip("/")
        context_filters = {
            "folder.name": [folder_entity["name"]],
            "hierarchy": [hierarchy],
            "product.type": ["workfile"],
            "task.name": [task_name],
            "task.type": [task_type]
        }

        workfile_representation_ids = list(get_repre_ids_by_context_filters(
            project_name,
            context_filters=context_filters
        ))

        if not workfile_representation_ids:
            self.log.debug(
                'No published workfile for task "{}" and host "{}".'.format(
                    task_name, host_name
                )
            )
            return

        workfile_representations = get_representations(
            project_name, workfile_representation_ids)

        if not workfile_representations:
            self.log.debug(
                f"Not found representations for '{workfile_representation_ids}"
            )
            return

        workfile_representation = max(
            workfile_representations, key=lambda r: r["context"]["version"]
        )

        # Copy file and substitute path
        last_published_workfile_path = download_last_published_workfile(
            host_name,
            project_name,
            task_name,
            workfile_representation,
            max_retries,
            anatomy=anatomy
        )
        if not last_published_workfile_path:
            self.log.debug(
                "Couldn't download {}".format(last_published_workfile_path)
            )
            return

        project_entity = self.data["project_entity"]
        task_entity = self.data["task_entity"]

        project_settings = self.data["project_settings"]

        # Get workfile data
        workfile_data = get_template_data(
            project_entity, folder_entity, task_entity, host_name,
            project_settings
        )

        extension = last_published_workfile_path.split(".")[-1]
        workfile_data["version"] = (
                workfile_representation["context"]["version"] + 1)
        workfile_data["ext"] = extension

        template_key = get_workfile_template_key(
            task_name, host_name, project_name, project_settings
        )
        template = anatomy.get_template_item("work", template_key, "path")
        local_workfile_path = template.format_strict(workfile_data)

        # Copy last published workfile to local workfile directory
        shutil.copy(
            last_published_workfile_path,
            local_workfile_path,
        )

        self.data["last_workfile_path"] = local_workfile_path
        # Keep source filepath for further path conformation
        self.data["source_filepath"] = last_published_workfile_path
