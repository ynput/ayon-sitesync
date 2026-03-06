import time

from ayon_core.lib import Logger
from ayon_api import get_representations, get_versions_links


log = Logger.get_logger("SiteSync")


class ResumableError(Exception):
    """Error which could be temporary, skip current loop, try next time"""
    pass


class SiteAlreadyPresentError(Exception):
    """Representation has already site skeleton present."""
    pass


class SyncStatus:
    DO_NOTHING = 0
    DO_UPLOAD = 1
    DO_DOWNLOAD = 2


class SiteSyncStatus:
    NA = -1
    IN_PROGRESS = 0
    QUEUED = 1
    FAILED = 2
    PAUSED = 3
    OK = 4


def time_function(method):
    """ Decorator to print how much time function took.
        For debugging.
        Depends on presence of 'log' object
    """

    def timed(*args, **kw):
        ts = time.time()
        result = method(*args, **kw)
        te = time.time()
        if "log_time" in kw:
            name = kw.get("log_name", method.__name__.upper())
            kw["log_time"][name] = int((te - ts) * 1000)
        else:
            log.debug("%r  %2.2f ms" % (method.__name__, (te - ts) * 1000))
        return result

    return timed


class EditableScopes:
    SYSTEM = 0
    PROJECT = 1
    LOCAL = 2


def get_linked_representation_id(
        project_name,
        repre_entity,
        link_type,
):
    """Returns list of linked ids of particular type (if provided).

    One of representation document or representation id must be passed.
    Note:
        Representation links now works only from representation through
            version back to representations.

    Todos:
        This function should probably live in sitesync addon?

    Args:
        project_name (str): Name of project where look for links.
        repre_entity (dict[str, Any]): Representation entity.
        link_type (str): Type of link (e.g. 'reference', ...).

    Returns:
        List[ObjectId] Linked representation ids.
    """

    if not repre_entity:
        return []

    version_id = repre_entity["versionId"]

    link_types = None
    if link_type:
        link_types = [link_type]

    # Store already found version ids to avoid infinite recursion
    linked_version_ids = {version_id}
    # Each loop will find new versions linked to current versions
    versions_to_check = {version_id}

    while versions_to_check:
        versions_links = get_versions_links(
            project_name,
            versions_to_check,
            link_types=link_types,
            link_direction="in")  # looking for 'in'puts for version

        versions_to_check = set()
        for links in versions_links.values():
            for link in links:
                # Care only about version links
                if link["entityType"] != "version":
                    continue
                entity_id = link["entityId"]
                # Only add if not already visited
                if entity_id not in linked_version_ids:
                    linked_version_ids.add(entity_id)
                    versions_to_check.add(entity_id)

    # Remove the original version_id from results
    linked_version_ids.discard(version_id)

    if not linked_version_ids:
        return []

    representations = get_representations(
        project_name,
        version_ids=linked_version_ids,
        fields=["id"])
    return [
        repre["id"]
        for repre in representations
    ]
