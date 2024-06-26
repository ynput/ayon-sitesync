"""Prepares server package from addon repo to upload to server.

Requires Python3.9. (Or at least 3.8+).

This script should be called from cloned addon repo.

It will produce 'package' subdirectory which could be pasted into server
addon directory directly (eg. into `ayon-backend/addons`).

Format of package folder:
ADDON_REPO/package/{addon name}/{addon version}

If there is command line argument `--output_dir` filled, version folder
(eg. `sitesync/1.0.0`) will be created there (if already present, it will be
purged first). This could be used to create package directly in server folder
if available.

Package contains server side files directly,
client side code zipped in `private` subfolder.
"""
import os
import re
import sys
import shutil
import logging
import platform
import zipfile
import argparse
import collections
import subprocess
from typing import Optional, Iterable, Pattern, Union

import package

ADDON_NAME: str = package.name
ADDON_VERSION: str = package.version
ADDON_CLIENT_DIR: str = package.client_dir

CLIENT_VERSION_CONTENT = '''# -*- coding: utf-8 -*-
"""Package declaring {} addon version."""
__version__ = "{}"
'''

# Patterns of directories to be skipped for server part of addon
IGNORE_DIR_PATTERNS: list[Pattern] = [
    re.compile(pattern)
    for pattern in {
        # Skip directories starting with '.'
        r"^\.",
        # Skip any pycache folders
        "^__pycache__$"
    }
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


def _get_yarn_executable() -> Union[str, None]:
    cmd = "which"
    if platform.system().lower() == "windows":
        cmd = "where"

    for line in subprocess.check_output(
        [cmd, "yarn"], encoding="utf-8"
    ).splitlines():
        if not line or not os.path.exists(line):
            continue
        try:
            subprocess.call([line, "--version"])
            return line
        except OSError:
            continue
    return None


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


def safe_copy_file(src_path, dst_path):
    """Copy file and make sure destination directory exists.

    Ignore if destination already contains directories from source.

    Args:
        src_path (str): File path that will be copied.
        dst_path (str): Path to destination file.
    """

    if src_path == dst_path:
        return

    dst_dir = os.path.dirname(dst_path)
    try:
        os.makedirs(dst_dir)
    except Exception:
        pass

    shutil.copy2(src_path, dst_path)


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


def fill_client_version(current_dir):
    version_file = os.path.join(
        current_dir, "client", ADDON_CLIENT_DIR, "version.py"
    )
    with open(version_file, "w") as stream:
        stream.write(CLIENT_VERSION_CONTENT.format(ADDON_NAME, ADDON_VERSION))


def create_server_package(
    current_dir: str,
    output_dir: str,
    addon_output_dir: str,
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
        output_dir, f"{ADDON_NAME}-{ADDON_VERSION}.zip"
    )

    with ZipFileLongPaths(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Write a manifest to zip
        zipf.write(
            os.path.join(current_dir, "package.py"),
            "package.py"
        )

        # Move addon content to zip into 'addon' directory
        addon_output_dir_offset = len(addon_output_dir) + 1
        for root, _, filenames in os.walk(addon_output_dir):
            if not filenames:
                continue

            dst_root = None
            if root != addon_output_dir:
                dst_root = root[addon_output_dir_offset:]

            for filename in filenames:
                src_path = os.path.join(root, filename)
                dst_path = filename
                if dst_root:
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

    # Add client code content to zip
    client_code_dir: str = os.path.join(client_dir, ADDON_CLIENT_DIR)
    for path, sub_path in find_files_in_subdir(client_code_dir):
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


def _build_frontend(frontend_dirpath: str, frontend_dist_dirpath: str):
    yarn_executable = _get_yarn_executable()
    if yarn_executable is None:
        raise RuntimeError("Yarn executable was not found.")

    subprocess.run([yarn_executable, "install"], cwd=frontend_dirpath)
    subprocess.run([yarn_executable, "build"], cwd=frontend_dirpath)
    if not os.path.exists(frontend_dirpath):
        raise RuntimeError(
            "Build frontend first with `yarn install && yarn build`"
        )


def copy_server_content(
    addon_package_dir: str, current_dir: str, log: logging.Logger
):
    # Build frontend first - nothing else make sense without valid frontend
    frontend_dir: str = os.path.join(current_dir, "frontend")
    frontend_dist_dirpath: str = os.path.join(frontend_dir, "dist")

    log.info("Building frontend")
    _build_frontend(frontend_dir, frontend_dist_dirpath)

    log.info("Copying server content")
    # Frontend
    shutil.copytree(
        frontend_dist_dirpath,
        os.path.join(addon_package_dir, "frontend", "dist"),
        dirs_exist_ok=True
    )

    filepaths_to_copy = []
    server_dirpath = os.path.join(current_dir, "server")

    for item in find_files_in_subdir(server_dirpath):
        src_path, dst_subpath = item
        dst_path = os.path.join(addon_package_dir, "server", dst_subpath)
        filepaths_to_copy.append((src_path, dst_path))

    # Copy files
    for src_path, dst_path in filepaths_to_copy:
        safe_copy_file(src_path, dst_path)


def main(output_dir=None, skip_zip=False, keep_sources=False):
    log.info("Start creating package")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if not output_dir:
        output_dir = os.path.join(current_dir, "package")

    addon_output_root = os.path.join(output_dir, ADDON_NAME)
    addon_output_dir = os.path.join(addon_output_root, ADDON_VERSION)
    if os.path.isdir(addon_output_dir):
        log.info(f"Purging {addon_output_dir}")
        shutil.rmtree(output_dir)
    os.makedirs(addon_output_dir)

    fill_client_version(current_dir)

    log.info(f"Preparing package for {ADDON_NAME}-{ADDON_VERSION}")

    copy_server_content(addon_output_dir, current_dir, log)

    zip_client_side(addon_output_dir, current_dir, log)

    # Skip server zipping
    if not skip_zip:
        create_server_package(
            current_dir, output_dir, addon_output_dir, log
        )
        # Remove sources only if zip file is created
        if not keep_sources:
            log.info("Removing source files for server package")
            shutil.rmtree(addon_output_root)
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
