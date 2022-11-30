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

# skip non server side folders
IGNORE_DIR_PATTERNS = ["package", "__pycache__", "client", r"^\."]
# skip files from addon root
IGNORE_FILES_PATTERNS = ["create_package.py", r"^\.", "pyc$"]

log = logging.getLogger("create_package")


def main(output_dir=None):
    log.info("Start creating package")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if not output_dir:
        output_dir = os.path.join(current_dir, "package")

    with open(os.path.join(current_dir, "version.py")) as fp:
        init_file = fp.read()
        addon_version = _find_key_value(init_file, "__version__")

    with open(os.path.join(current_dir, "__init__.py")) as fp:
        init_file = fp.read()
        addon_name = _find_key_value(init_file, "name")

    new_created_version_dir = os.path.join(output_dir,
                                           addon_name, addon_version)
    if os.path.isdir(new_created_version_dir):
        log.info(f"Purging {new_created_version_dir}")
        shutil.rmtree(output_dir)

    log.info(f"Preparing package for {addon_name}-{addon_version}")

    zip_file_name = f"{addon_name}_{addon_version}"
    addon_package_dir = os.path.join(output_dir, addon_name,
                                     addon_version)
    os.makedirs(addon_package_dir)

    copy_non_client_folders(addon_package_dir, current_dir,
                            IGNORE_DIR_PATTERNS, log)

    copy_root_files(addon_package_dir, current_dir,
                    IGNORE_FILES_PATTERNS, log)

    zip_client_side(addon_package_dir, current_dir, zip_file_name, log)

    copy_pyproject(addon_package_dir, current_dir)


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


def zip_client_side(addon_package_dir, current_dir, zip_file_name,
                    log=None):
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
    if os.path.isdir(client_dir):
        private_dir = os.path.join(addon_package_dir, "private")
        temp_dir_to_zip = os.path.join(private_dir, "temp")
        # shutil.copytree expects glob-style patterns, not regex
        ignore_patterns = ["*.pyc", "*__pycache__*"]
        shutil.copytree(client_dir,
                        os.path.join(temp_dir_to_zip, zip_file_name),
                        ignore=shutil.ignore_patterns(*ignore_patterns))

        toml_path = os.path.join(client_dir, "pyproject.toml")
        if os.path.exists(toml_path):
            shutil.copy(toml_path,
                        os.path.join(addon_package_dir, "private"))

        zip_file_path = os.path.join(os.path.join(private_dir, zip_file_name))
        shutil.make_archive(zip_file_path, 'zip', temp_dir_to_zip)
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


def _find_key_value(init_file, value):
    pattern = rf"{value}[\s+]=[\s+]['\"]([^'\"]*)['\"]"
    match = re.search(pattern, init_file, re.M)
    if match:
        return match.group(1)
    raise RuntimeError(f"Cannot find {value}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output_dir",
                        help="Folder url to to create package with version in."
                             "If addon version folder exists, will be purged!")

    kwargs = parser.parse_args(sys.argv[1:]).__dict__
    main(**kwargs)

