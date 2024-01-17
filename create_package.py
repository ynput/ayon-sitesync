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
import collections
from typing import Optional, Iterable, Pattern

ADDON_NAME: str = "sitesync"
ADDON_CLIENT_DIR: str = "ayon_sitesync"

# Patterns of directories to be skipped for server part of addon
IGNORE_DIR_PATTERNS: list[Pattern] = [
    re.compile(pattern)
    for pattern in [
        # Skip directories starting with '.'
        r"^\.",
        # Skip any pycache folders
        "^__pycache__$"
    ]
]

# Patterns of files to be skipped for server part of addon
IGNORE_FILE_PATTERNS: list[Pattern] = [
    re.compile(pattern)
    for pattern in {
        # Skip files starting with '.'
        # NOTE this could be an issue in some cases
        r"^\.",
        # Skip '.pyc' files
        r"\.pyc$"
    }
]

log: logging.Logger = logging.getLogger("create_package")


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


def _value_match_regexes(value: str, regexes: Iterable[Pattern]) -> bool:
    return any(
        regex.search(value)
        for regex in regexes
    )


def find_files_in_subdir(
    src_path: str,
    ignore_file_patterns: Optional[list[Pattern]]=None,
    ignore_dir_patterns: Optional[list[Pattern]]=None
) -> list[tuple[str, str]]:
    """Find all files to copy in subdirectories of given path.

    All files that match any of the patterns in 'ignore_file_patterns' will
        be skipped and any directories that match any of the patterns in
        'ignore_dir_patterns' will be skipped with all subfiles.

    Args:
        src_path (str): Path to directory to search in.
        ignore_file_patterns (Optional[list[Pattern]]): List of regexes
            to match files to ignore.
        ignore_dir_patterns (Optional[list[Pattern]]): List of regexes
            to match directories to ignore.

    Returns:
        list[tuple[str, str]]: List of tuples with path to file and parent
            directories relative to 'src_path'.
    """

    if ignore_file_patterns is None:
        ignore_file_patterns = IGNORE_FILE_PATTERNS

    if ignore_dir_patterns is None:
        ignore_dir_patterns = IGNORE_DIR_PATTERNS
    output: list[tuple[str, str]] = []

    hierarchy_queue: collections.deque = collections.deque()
    hierarchy_queue.append((src_path, []))
    while hierarchy_queue:
        item: tuple[str, str] = hierarchy_queue.popleft()
        dirpath, parents = item
        for name in os.listdir(dirpath):
            path: str = os.path.join(dirpath, name)
            if os.path.isfile(path):
                if not _value_match_regexes(name, ignore_file_patterns):
                    items: list[str] = list(parents)
                    items.append(name)
                    output.append((path, os.path.sep.join(items)))
                continue

            if not _value_match_regexes(name, ignore_dir_patterns):
                items: list[str] = list(parents)
                items.append(name)
                hierarchy_queue.append((path, items))

    return output


def create_server_package(
    output_dir: str,
    addon_output_dir: str,
    addon_version: str,
    log: logging.Logger
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


def _get_client_zip_content(current_dir: str, log: logging.Logger):
    """

    Args:
        current_dir (str): Directory path of addon source.
        log (logging.Logger): Logger object.

    Returns:
        list[tuple[str, str]]: List of path mappings to copy. The destination
            path is relative to expected output directory.
    """

    client_dir: str = os.path.join(current_dir, "client")
    if not os.path.isdir(client_dir):
        raise RuntimeError("Client directory was not found.")

    log.info("Preparing client code zip")

    output: list[tuple[str, str]] = []

    src_version_path: str = os.path.join(current_dir, "version.py")
    dst_version_path: str = os.path.join(ADDON_CLIENT_DIR, "version.py")
    output.append((src_version_path, dst_version_path))

    # Add client code content to zip
    client_code_dir: str = os.path.join(client_dir, ADDON_CLIENT_DIR)
    for path, sub_path in find_files_in_subdir(client_code_dir):
        if sub_path == "version.py":
            continue
        output.append((path, os.path.join(ADDON_CLIENT_DIR, sub_path)))
    return output


def zip_client_side(addon_package_dir, current_dir, log):
    """Copies and zip `client` subfolder into `addon_package_dir'.

    Args:
        addon_package_dir (str): package dir in addon repo dir
        current_dir (str): addon repo dir
        log (logging.Logger)
    """

    client_dir: str = os.path.join(current_dir, "client")
    if not os.path.isdir(client_dir):
        log.info("Client directory was not found. Skipping")
        return

    log.info("Preparing client code zip")
    private_dir: str = os.path.join(addon_package_dir, "private")
    os.makedirs(private_dir, exist_ok=True)

    mapping = _get_client_zip_content(current_dir, log)

    zip_filepath: str = os.path.join(os.path.join(private_dir, "client.zip"))
    with ZipFileLongPaths(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Add client code content to zip
        for path, sub_path in mapping:
            zipf.write(path, sub_path)

    shutil.copy(os.path.join(client_dir, "pyproject.toml"), private_dir)


def copy_server_content(
    addon_package_dir: str, current_dir: str, log: logging.Logger
):
    # Build frontend first - nothing else make sense without valid frontend
    frontend_dir: str = os.path.join(current_dir, "frontend")
    frontend_dist_dirpath: str = os.path.join(frontend_dir, "dist")

    log.info("Copying server content")
    # Frontend
    shutil.copytree(
        frontend_dist_dirpath,
        os.path.join(addon_package_dir, "frontend", "dist"),
        dirs_exist_ok=True
    )

    for folder_name in {"settings", "private"}:
        folder_path = os.path.join(current_dir, folder_name)
        shutil.copytree(
            folder_path,
            os.path.join(addon_package_dir, folder_name),
            dirs_exist_ok=True
        )

    for filename in {
        "__init__.py",
        "version.py",
        "models.py",
    }:
        shutil.copy(
            os.path.join(current_dir, filename),
            addon_package_dir
        )


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

    copy_server_content(addon_package_dir, current_dir, log)

    zip_client_side(addon_package_dir, current_dir, log)

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
