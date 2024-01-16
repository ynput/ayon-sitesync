"""Prepares server package from addon repo to upload to server.

Requires Python3.9. (Or at least 3.8+).

This script should be called from cloned addon repo.

It will produce 'package' subdirectory which could be pasted into server
addon directory directly (eg. into `openpype4-backend/addons`).

Format of package folder:
ADDON_REPO/package/sitesync/1.0.0

If there is command line argument `--output_dir` filled, version folder
(eg. `sitesync/1.0.0`) will be created there (if already present, it will be
purged first). This could be used to create package directly in server folder
if available.

Package contains server side files directly,
client side code zipped in `private` subfolder.
"""
import os
import re
import shutil
import logging
import sys
import json
import platform
import zipfile
import argparse

ADDON_NAME = "sitesync"
ADDON_CLIENT_DIR = "ayon_sitesync"
# skip non server side folders
IGNORE_DIR_PATTERNS = ["package", "__pycache__", "client", r"^\.", "frontend"]
# skip files from addon root
IGNORE_FILES_PATTERNS = ["create_package.py", r"^\.", "pyc$"]

log = logging.getLogger("create_package")


class ZipFileLongPaths(zipfile.ZipFile):
    """Allows longer paths in zip files.

    Regular DOS paths are limited to MAX_PATH (260) characters, including
    the string's terminating NUL character.
    That limit can be exceeded by using an extended-length path that
    starts with the '\\?\' prefix.
    """
    _is_windows = platform.system().lower() == "windows"

    def _extract_member(self, member, tpath, pwd):
        if self._is_windows:
            tpath = os.path.abspath(tpath)
            if tpath.startswith("\\\\"):
                tpath = "\\\\?\\UNC\\" + tpath[2:]
            else:
                tpath = "\\\\?\\" + tpath

        return super(ZipFileLongPaths, self)._extract_member(
            member, tpath, pwd
        )


def create_server_package(
    output_dir,
    addon_output_dir,
    addon_version,
    log
):
    """Create server package zip file.

    The zip file can be installed to a server using UI or rest api endpoints.

    Args:
        output_dir (str): Directory path to output zip file.
        addon_output_dir (str): Directory path to addon output directory.
        addon_version (str): Version of addon.
        log (logging.Logger): Logger instance.
    """

    log.info("Creating server package")
    output_path = os.path.join(
        output_dir, f"{ADDON_NAME}-{addon_version}.zip"
    )
    manifest_data: dict[str, str] = {
        "addon_name": ADDON_NAME,
        "addon_version": addon_version
    }
    with ZipFileLongPaths(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Write a manifest to zip
        zipf.writestr("manifest.json", json.dumps(manifest_data, indent=4))

        # Move addon content to zip into 'addon' directory
        addon_output_dir_offset = len(addon_output_dir) + 1
        for root, _, filenames in os.walk(addon_output_dir):
            if not filenames:
                continue

            dst_root = "addon"
            if root != addon_output_dir:
                dst_root = os.path.join(
                    dst_root, root[addon_output_dir_offset:]
                )
            for filename in filenames:
                src_path = os.path.join(root, filename)
                dst_path = os.path.join(dst_root, filename)
                zipf.write(src_path, dst_path)

    log.info(f"Output package can be found: {output_path}")


def main(output_dir=None, skip_zip=False, keep_sources=False):
    log.info("Start creating package")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if not output_dir:
        output_dir = os.path.join(current_dir, "package")

    version_content = {}
    with open(os.path.join(current_dir, "version.py"), "r") as stream:
        exec(stream.read(), version_content)
    addon_version: str = version_content["__version__"]

    addon_package_root = os.path.join(output_dir, ADDON_NAME)
    if os.path.isdir(addon_package_root):
        log.info(f"Purging {addon_package_root}")
        shutil.rmtree(addon_package_root)

    log.info(f"Preparing package for {ADDON_NAME}-{addon_version}")

    addon_package_dir = os.path.join(addon_package_root, addon_version)
    os.makedirs(addon_package_dir)

    copy_non_client_folders(
        addon_package_dir, current_dir, IGNORE_DIR_PATTERNS, log)

    frontend_dir = os.path.join(current_dir, "frontend")
    copy_frontend_folders(
        addon_package_dir, frontend_dir, log)

    copy_root_files(
        addon_package_dir, current_dir, IGNORE_FILES_PATTERNS, log)

    zip_client_side(addon_package_dir, current_dir, log)

    copy_pyproject(addon_package_dir, current_dir)

    # Skip server zipping
    if not skip_zip:
        create_server_package(
            output_dir, addon_package_dir, addon_version, log
        )
        # Remove sources only if zip file is created
        if not keep_sources:
            log.info("Removing source files for server package")
            shutil.rmtree(addon_package_root)
    log.info("Package creation finished")


def copy_pyproject(addon_package_dir, current_dir):
    """Copies pyproject_toml from root of addon repo to package/../private

    Args:
        addon_package_dir (str): local package dir with addon version number
        current_dir (str): addon repo root
    """
    pyproject_path = os.path.join(current_dir, "pyproject.toml")
    if os.path.exists(pyproject_path):
        private_dir = os.path.join(addon_package_dir, "private")
        shutil.copy(pyproject_path,
                    private_dir)


def zip_client_side(addon_package_dir, current_dir, log=None):
    """Copies and zip `client` subfolder into `addon_package_dir'.

    Args:
        addon_package_dir (str): package dir in addon repo dir
        current_dir (str): addon repo dir
        zip_file_name (str): file name in format {ADDON_NAME}_{ADDON_VERSION}
            (eg. 'sitesync_1.0.0')
        log (logging.Logger)
    """
    if not log:
        log = logging.getLogger("create_package")

    log.info("Preparing client code zip")
    client_dir = os.path.join(current_dir, "client")
    if not os.path.isdir(client_dir):
        return

    private_dir = os.path.join(addon_package_dir, "private")
    temp_dir_to_zip = os.path.join(private_dir, "temp")
    # shutil.copytree expects glob-style patterns, not regex
    ignore_patterns = ["*.pyc", "*__pycache__*"]
    shutil.copytree(client_dir,
                    temp_dir_to_zip,
                    ignore=shutil.ignore_patterns(*ignore_patterns))

    version_path = os.path.join(current_dir, "version.py")
    # copy version.py to OP module know which version it is
    shutil.copy(version_path, os.path.join(temp_dir_to_zip, ADDON_CLIENT_DIR))

    zip_file_path = os.path.join(private_dir, "client.zip")
    temp_dir_to_zip_s = temp_dir_to_zip.replace("\\", "/")
    with ZipFileLongPaths(zip_file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirnames, filenames in os.walk(temp_dir_to_zip):
            root_s = root.replace("\\", "/")
            zip_root = root_s.replace(temp_dir_to_zip_s, "").strip("/")
            for name in sorted(dirnames):
                path = os.path.normpath(os.path.join(root, name))
                zip_path = name
                if zip_root:
                    zip_path = "/".join((zip_root, name))
                zipf.write(path, zip_path)

            for name in filenames:
                path = os.path.normpath(os.path.join(root, name))
                zip_path = name
                if zip_root:
                    zip_path = "/".join((zip_root, name))
                if os.path.isfile(path):
                    zipf.write(path, zip_path)

    shutil.rmtree(temp_dir_to_zip)


def copy_root_files(addon_package_dir, current_dir, ignore_patterns, log=None):
    """Copies files in root of addon repo, skips ignored pattern.

    Args:
        addon_package_dir (str): package dir in addon repo dir
        current_dir (str): addon repo dir
        ignore_patterns (list): regex pattern of files to skip
        log (logging.Logger)
    """
    if not log:
        log = logging.getLogger("create_package")

    log.info("Copying root files")
    file_names = [f for f in os.listdir(current_dir)
                  if os.path.isfile(os.path.join(current_dir, f))]
    for file_name in file_names:
        skip = False
        for pattern in ignore_patterns:
            if re.search(pattern, file_name):
                skip = True
                break

        if skip:
            continue

        shutil.copy(os.path.join(current_dir, file_name),
                    addon_package_dir)


def copy_non_client_folders(addon_package_dir, current_dir, ignore_patterns,
                            log=None):
    """Copies server side folders to 'addon_package_dir'

    Args:
        addon_package_dir (str): package dir in addon repo dir
        current_dir (str): addon repo dir
        ignore_patterns (list): regex pattern of files to skip
        log (logging.Logger)
    """
    if not log:
        log = logging.getLogger("create_package")

    log.info("Copying non client folders")
    dir_names = [f for f in os.listdir(current_dir)
                 if os.path.isdir(os.path.join(current_dir, f))]

    for folder_name in dir_names:
        skip = False
        for pattern in ignore_patterns:
            if re.search(pattern, folder_name):
                skip = True
                break

        if skip:
            continue

        folder_path = os.path.join(current_dir, folder_name)

        shutil.copytree(folder_path,
                        os.path.join(addon_package_dir, folder_name),
                        dirs_exist_ok=True)


def copy_frontend_folders(addon_package_dir, fronted_dir,
                          log=None):
    """Copies frontend files to 'addon_package_dir'

    Only 'dist' folder is necessary to copy over.

    Args:
        addon_package_dir (str): package dir in addon repo dir
        fronted_dir (str): path to frontend dir
        log (logging.Logger)
    """
    if not log:
        log = logging.getLogger("create_package")

    log.info("Copying frontend folders")
    shutil.copytree(os.path.join(fronted_dir, "dist"),
                    os.path.join(addon_package_dir, "frontend", "dist"),
                    dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-zip",
        dest="skip_zip",
        action="store_true",
        help=(
            "Skip zipping server package and create only"
            " server folder structure."
        )
    )
    parser.add_argument(
        "--keep-sources",
        dest="keep_sources",
        action="store_true",
        help=(
            "Keep folder structure when server package is created."
        )
    )
    parser.add_argument(
        "-o", "--output",
        dest="output_dir",
        default=None,
        help=(
            "Directory path where package will be created"
            " (Will be purged if already exists!)"
        )
    )

    args = parser.parse_args(sys.argv[1:])
    main(args.output_dir, args.skip_zip, args.keep_sources)
