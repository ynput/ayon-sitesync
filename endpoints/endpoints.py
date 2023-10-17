import os

from fastapi import APIRouter, Depends, Path, Query, Response
from .models import (
    FileModel,
    RepresentationStateModel,
    SiteSyncParamsModel,
    SiteSyncSummaryItem,
    SiteSyncSummaryModel,
    SortByEnum,
    StatusEnum,
    SyncStatusModel,
)

from ayon_server.access.utils import folder_access_list
from ayon_server.api import dep_current_user, dep_project_name, dep_representation_id
from ayon_server.entities.representation import RepresentationEntity
from ayon_server.entities.user import UserEntity
from ayon_server.lib.postgres import Postgres
from ayon_server.utils import SQLTool

from nxtools import logging


router = APIRouter(tags=["Site sync"])


def get_overal_status(files: dict) -> StatusEnum:
    all_states = [v.get("status", StatusEnum.NOT_AVAILABLE) for v in files.values()]
    if all(stat == StatusEnum.NOT_AVAILABLE for stat in all_states):
        return StatusEnum.NOT_AVAILABLE
    elif all(stat == StatusEnum.SYNCED for stat in all_states):
        return StatusEnum.SYNCED
    elif any(stat == StatusEnum.FAILED for stat in all_states):
        return StatusEnum.FAILED
    elif any(stat == StatusEnum.IN_PROGRESS for stat in all_states):
        return StatusEnum.IN_PROGRESS
    elif any(stat == StatusEnum.PAUSED for stat in all_states):
        return StatusEnum.PAUSED
    elif all(stat == StatusEnum.QUEUED for stat in all_states):
        return StatusEnum.QUEUED
    return StatusEnum.NOT_AVAILABLE


async def check_sync_status_table(project_name):

    await Postgres.execute(
        f"CREATE TABLE IF NOT EXISTS project_{project_name}.sitesync_files_status ("
           f"""representation_id UUID NOT NULL REFERENCES project_{project_name}.representations(id) ON DELETE CASCADE,
            site_name VARCHAR NOT NULL,
            status INTEGER NOT NULL DEFAULT -1,
            priority INTEGER NOT NULL DEFAULT 50,
            data JSONB NOT NULL DEFAULT '{{}}'::JSONB,
            PRIMARY KEY (representation_id, site_name)
        );"""
    )
    await Postgres.execute(f"CREATE INDEX IF NOT EXISTS file_status_idx ON project_{project_name}.sitesync_files_status(status);")
    await Postgres.execute(f"CREATE INDEX IF NOT EXISTS file_priority_idx ON project_{project_name}.sitesync_files_status(priority desc);")

#
# GET SITE SYNC PARAMS
#


async def get_site_sync_params(
    project_name: str = Depends(dep_project_name),
    user: UserEntity = Depends(dep_current_user),
) -> SiteSyncParamsModel:
    """Counts how many representations are in project.

    Used for SiteSyncSummary table to paginate correctly.
    """
    access_list = await folder_access_list(user, project_name, "read")
    conditions = []
    if access_list is not None:
        conditions.append(f"h.path like ANY ('{{ {','.join(access_list)} }}')")

    query = f"""
        SELECT
            DISTINCT(r.name) as name,
            COUNT (*) OVER () as total_count
        FROM project_{project_name}.representations as r
        INNER JOIN project_{project_name}.versions as v
            ON r.version_id = v.id
        INNER JOIN project_{project_name}.products as p
            ON v.product_id = p.id
        INNER JOIN project_{project_name}.hierarchy as h
            ON p.folder_id = h.id
        {SQLTool.conditions(conditions)}
    """

    total_count = 0
    names = []
    async for row in Postgres.iterate(query):
        total_count = row["total_count"] or 0
        names.append(row["name"])

    return SiteSyncParamsModel(count=total_count, names=names)


#
# GET SITE SYNC OVERAL STATE
#


async def get_site_sync_state(
    project_name: str = Depends(dep_project_name),
    user: UserEntity = Depends(dep_current_user),
    localSite: str = Query(
        ...,
        description="Name of the local site",
        example="Machine42",
    ),
    remoteSite: str = Query(
        ...,
        description="Name of the remote site",
        example="GDrive",
    ),
    sortBy: SortByEnum = Query(
        SortByEnum.folder,
        description="Sort the result by this value",
        example=SortByEnum.folder,
    ),
    sortDesc: bool = Query(
        False,
        name="Sort descending",
        description="Sort the result in descending order",
    ),
    folderFilter: str
    | None = Query(
        None,
        description="Filter folders by name",
        example="sh042",
    ),
    folderIdFilter: list[str]
        | None = Query(
        None,
        description="Filter folders by id, eg filtering by asset id",
        example="57cf375c749611ed89de0242ac140004",
    ),
    productFilter: str
    | None = Query(
        None,
        description="Filter products by name",
        example="animation",
    ),
    versionIdFilter: list[str]
        | None = Query(
        None,
        description="Filter versions by id",
        example="57cf375c749611ed89de0242ac140004",
    ),
    localStatusFilter: list[StatusEnum]
    | None = Query(
        None,
        description=f"List of states to show. Available options: {StatusEnum.__doc__}",
        example=[StatusEnum.QUEUED, StatusEnum.IN_PROGRESS],
    ),
    remoteStatusFilter: list[StatusEnum]
    | None = Query(
        None,
        description=f"List of states to show. Available options: {StatusEnum.__doc__}",
        example=[StatusEnum.QUEUED, StatusEnum.IN_PROGRESS],
    ),
    nameFilter: list[str] | None = Query(None),
    representationId: str
    | None = Query(None, description="Select only the given representation."),
    # Pagination
    page: int = Query(1, ge=1),
    pageLength: int = Query(50, ge=1),
) -> SiteSyncSummaryModel:
    """Return a site sync state.

    When a representationId is provided,
    the result will contain only one representation,
    along with the information on individual files.
    """
    await check_sync_status_table(project_name)
    conditions = []

    if representationId is not None:
        conditions.append(f"r.id = '{representationId}'")

    else:
        # When a single representation is requested
        # We ignore the rest of the filter
        if folderFilter:
            conditions.append(f"f.name ILIKE '%{folderFilter}%'")

        if folderIdFilter:
            conditions.append(f"f.id IN {SQLTool.array(folderIdFilter)}")

        if productFilter:
            conditions.append(f"s.name ILIKE '%{productFilter}%'")

        if versionIdFilter:
            conditions.append(f"v.id IN {SQLTool.array(versionIdFilter)}")

        if localStatusFilter:
            statusFilter = [str(s.value) for s in localStatusFilter]
            conditions.append(f"local.status IN ({','.join(statusFilter)})")

        if remoteStatusFilter:
            statusFilter = [str(s.value) for s in remoteStatusFilter]
            conditions.append(f"remote.status IN ({','.join(statusFilter)})")

        if nameFilter:
            conditions.append(f"r.name IN {SQLTool.array(nameFilter)}")

        access_list = await folder_access_list(user, project_name, "read")
        if access_list is not None:
            conditions.append(f"path like ANY ('{{ {','.join(access_list)} }}')")

    query = f"""
        SELECT
            f.name as folder,
            p.name as product,
            v.version as version,
            r.name as representation,
            h.path as path,

            r.id as representation_id,
            r.files as representation_files,
            local.data as local_data,
            remote.data as remote_data,
            local.status as localStatus,
            remote.status as remoteStatus,
            v.id as version_id
        FROM
            project_{project_name}.folders as f
        INNER JOIN
            project_{project_name}.products as p
            ON p.folder_id = f.id
        INNER JOIN
            project_{project_name}.versions as v
            ON v.product_id = p.id
        INNER JOIN
            project_{project_name}.representations as r
            ON r.version_id = v.id
        INNER JOIN
            project_{project_name}.hierarchy as h
            ON f.id = h.id
        LEFT JOIN
            project_{project_name}.sitesync_files_status as local
            ON local.representation_id = r.id
            AND local.site_name = '{localSite}'
        LEFT JOIN
            project_{project_name}.sitesync_files_status as remote
            ON remote.representation_id = r.id
            AND remote.site_name = '{remoteSite}'

        {SQLTool.conditions(conditions)}

        ORDER BY {sortBy.value} {'DESC' if sortDesc else 'ASC'}
        LIMIT {pageLength}
        OFFSET { (page-1) * pageLength }
    """
    repres = []

    async for row in Postgres.iterate(query):
        import pprint
        logging.debug(f"row::{pprint.pformat(row, indent=4)}")
        files = row["representation_files"]
        file_count = len(files)
        total_size = sum([f.get("size") for f in files])

        ldata = row["local_data"] or {}
        logging.debug(f"local_data::{pprint.pformat(ldata, indent=4)}")
        lfiles = ldata.get("files", {})
        lsize = sum([f.get("size") for f in lfiles.values()] or [0])
        ltime = max([f.get("timestamp") for f in lfiles.values()] or [0])

        rdata = row["remote_data"] or {}
        rfiles = rdata.get("files", {})
        rsize = sum([f.get("size") for f in rfiles.values()] or [0])
        rtime = max([f.get("timestamp") for f in rfiles.values()] or [0])

        logging.debug(f"lfiles::{pprint.pformat(lfiles, indent=4)}")
        logging.debug(f"rfiles::{pprint.pformat(rfiles, indent=4)}")

        local_status = SyncStatusModel(
            status=StatusEnum.NOT_AVAILABLE
            if row["localstatus"] is None
            else row["localstatus"],
            totalSize=total_size,
            size=lsize,
            timestamp=ltime,
        )
        remote_status = SyncStatusModel(
            status=StatusEnum.NOT_AVAILABLE
            if row["remotestatus"] is None
            else row["remotestatus"],
            totalSize=total_size,
            size=rsize,
            timestamp=rtime,
        )
        logging.debug(f"lfiles::{pprint.pformat(lfiles, indent=4)}")
        file_list = []
        for file_info in files:
            file_id = file_info["id"]
            local_file = lfiles.get(file_id, {})
            remote_file = rfiles.get(file_id, {})

            file_list.append(
                FileModel(
                    id=file_id,
                    fileHash=file_info["hash"],
                    size=file_info["size"],
                    path=file_info["path"],
                    baseName=os.path.split(file_info["path"])[1],
                    localStatus=SyncStatusModel(
                        status=local_file.get("status",
                                              StatusEnum.NOT_AVAILABLE),
                        size=local_file.get("size", 0),
                        totalSize=file_info["size"],
                        timestamp=local_file.get("timestamp", 0),
                        message=local_file.get("message", None),
                        retries=local_file.get("retries", 0),
                    ),
                    remoteStatus=SyncStatusModel(
                        status=remote_file.get("status",
                                               StatusEnum.NOT_AVAILABLE),
                        size=remote_file.get("size", 0),
                        totalSize=file_info["size"],
                        timestamp=remote_file.get("timestamp", 0),
                        message=remote_file.get("message", None),
                        retries=remote_file.get("retries", 0),
                    ),
                )
            )

        repres.append(
            SiteSyncSummaryItem.construct(
                folder=row["folder"],
                product=row["product"],
                version=row["version"],
                representation=row["representation"],
                representationId=row["representation_id"],
                fileCount=file_count,
                size=total_size,
                localStatus=local_status,
                remoteStatus=remote_status,
                files=file_list,
                version_id=row["version_id"]
            )
        )

    return SiteSyncSummaryModel(representations=repres)


#
# SET REPRESENTATION SYNC STATE
#


async def set_site_sync_representation_state(
    post_data: RepresentationStateModel,
    project_name: str = Depends(dep_project_name),
    representation_id: str = Depends(dep_representation_id),
    site_name: str = Path(...),  # TODO: add regex validator/dependency here! Important!
) -> Response:
    """Adds site information to representation.

    Called after integration to set initial state of representation files on
    sites.
    Called repeatedly during synchronization to update progress/store error
    message
    """
    await check_sync_status_table(project_name)

    priority = post_data.priority

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            query = (
                f"""
                SELECT priority, data
                FROM project_{project_name}.sitesync_files_status
                WHERE representation_id = $1 AND site_name = $2
                FOR UPDATE
                """,
                representation_id,
                site_name,
            )

            result = await conn.fetch(*query)
            do_insert = False
            if not result:
                do_insert = True
                repre = await RepresentationEntity.load(
                    project_name, representation_id, transaction=conn
                )

                files = {}
                for file_info in repre._payload.files:
                    fhash = file_info.hash
                    files[file_info.id] = {
                        "hash": fhash,
                        "status": StatusEnum.NOT_AVAILABLE,
                        "size": 0,
                        "timestamp": 0,
                    }
            else:
                files = result[0]["data"].get("files")
                if priority is None:
                    priority = result[0]["priority"]

            for posted_file in post_data.files:
                posted_file_id = posted_file.id
                if posted_file_id not in files:
                    logging.info(f"{posted_file} not in files")
                    continue
                files[posted_file_id]["timestamp"] = posted_file.timestamp
                files[posted_file_id]["status"] = posted_file.status
                files[posted_file_id]["size"] = posted_file.size

                if posted_file.message:
                    files[posted_file_id]["message"] = posted_file.message
                elif "message" in files[posted_file_id]:
                    del files[posted_file_id]["message"]

                if posted_file.retries:
                    files[posted_file_id]["retries"] = posted_file.retries
                elif "retries" in files[posted_file_id]:
                    del files[posted_file_id]["retries"]

            status = get_overal_status(files)

            if do_insert:
                await conn.execute(
                    f"""
                    INSERT INTO project_{project_name}.sitesync_files_status
                    (representation_id, site_name, status, priority, data)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    representation_id,
                    site_name,
                    status,
                    post_data.priority if post_data.priority is not None else 50,
                    {"files": files},
                )
            else:
                await conn.execute(
                    f"""
                    UPDATE project_{project_name}.sitesync_files_status
                    SET status = $1, data = $2, priority = $3
                    WHERE representation_id = $4 AND site_name = $5
                    """,
                    status,
                    {"files": files},
                    priority,
                    representation_id,
                    site_name,
                )

    return Response(status_code=204)


async def remove_site_sync_representation_state(
    project_name: str = Depends(dep_project_name),
    user: UserEntity = Depends(dep_current_user),
    representation_id: str = Depends(dep_representation_id),
    site_name: str = Path(...),  # TODO: add regex validator/dependency here! Important!
) -> Response:
    await check_sync_status_table(project_name)

    async with Postgres.acquire() as conn:
        async with conn.transaction():
            query = (
                f"""
                DELETE
                FROM project_{project_name}.sitesync_files_status
                WHERE representation_id = $1 AND site_name = $2
                """,
                representation_id,
                site_name,
            )

            result = await conn.fetch(*query)

            return Response(status_code=204)
