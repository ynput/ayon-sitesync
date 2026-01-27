import os
import shutil

from ayon_api import (
    get_representations,
    get_products,
    get_last_versions
)

from ayon_core.lib import is_func_signature_supported
from ayon_core.pipeline.template_data import get_template_data
from ayon_core.pipeline.workfile import get_workfile_template_key
from ayon_core.pipeline.workfile import should_use_last_workfile_on_launch

from ayon_applications import PreLaunchHook

from ayon_sitesync.sitesync import download_last_published_workfile


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
        project_name = self.data["project_name"]
        sitesync_addon = self.addons_manager.get("sitesync")
        if (
            not sitesync_addon
            or not sitesync_addon.enabled
            or not sitesync_addon.is_project_enabled(project_name, True)
        ):
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

        host_name = self.application.host_name

        host_addon = self.addons_manager.get_host_addon(host_name)
        if host_addon is None:
            self.log.warning(
                f"Host addon not found for host '{host_name}'"
            )
            return

        workfile_extensions = host_addon.get_workfile_extensions()
        if not workfile_extensions:
            self.log.debug(
                "No workfile extensions defined by"
                f" host addon '{host_addon.name}'"
            )
            return

        # Get data
        project_settings = self.data["project_settings"]
        anatomy = self.data["anatomy"]
        task_id = self.data["task_entity"]["id"]
        folder_entity = self.data["folder_entity"]
        folder_id = folder_entity["id"]
        task_name = self.data["task_name"]
        task_type = self.data["task_type"]
        project_entity = self.data["project_entity"]
        task_entity = self.data["task_entity"]

        use_last_published_workfile = should_use_last_workfile_on_launch(
            project_name,
            host_name,
            task_name,
            task_type,
            project_settings=project_settings
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

        self.log.info("Trying to fetch last published workfile...")

        workfile_representation = (
            self._get_last_published_workfile_representation(
                project_name, folder_id, task_id, workfile_extensions
            )
        )

        if not workfile_representation:
            self.log.info("Couldn't find published workfile representation")
            return

        max_retries = int(
            sitesync_addon.sync_project_settings
            [project_name]
            ["config"]
            ["retry_cnt"]
        )

        # Copy file and substitute path
        last_published_workfile_path = download_last_published_workfile(
            host_name,
            project_name,
            task_name,
            workfile_representation,
            max_retries,
            anatomy=anatomy,
            sitesync_addon=sitesync_addon,
        )
        if not last_published_workfile_path:
            self.log.debug(
                "Couldn't download {}".format(last_published_workfile_path)
            )
            return

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

    def _get_last_published_workfile_representation(self,
        project_name, folder_id, task_id, workfile_extensions
    ):
        """Looks for last published representation for host and context"""

        kwargs = dict(
            folder_ids={folder_id},
            product_base_types={"workfile"},
        )
        if not is_func_signature_supported(
            get_products, project_name, **kwargs
        ):
            kwargs["product_types"] = kwargs.pop("product_base_types")

        product_entities = get_products(project_name, **kwargs)

        product_ids = {
            product_entity["id"]
            for product_entity in product_entities
        }
        if not product_ids:
            return None

        versions_by_product_id = get_last_versions(
            project_name,
            product_ids
        )
        version_ids = {
            version_entity["id"]
            for version_entity in versions_by_product_id.values()
            if version_entity["taskId"] == task_id
        }
        if not version_ids:
            return None

        for representation_entity in get_representations(
            project_name,
            version_ids=version_ids,
        ):
            ext = representation_entity["context"].get("ext")
            if not ext:
                continue
            ext = f".{ext}"
            if ext in workfile_extensions:
                return representation_entity
        return None
