from __future__ import annotations
from typing import Any, Type
from nxtools import logging
import os
from fastapi import Depends, Path, Query, Response

from ayon_server.addons import BaseServerAddon

from ayon_server.access.utils import folder_access_list
from ayon_server.api import (
    dep_current_user, 
    dep_project_name, 
    dep_representation_id, 
)

from ayon_server.entities.representation import RepresentationEntity
from ayon_server.entities.user import UserEntity
from ayon_server.lib.postgres import Postgres
from ayon_server.utils import SQLTool

from .settings.settings import SiteSyncSettings
from .settings.models import (
    FileModel,
    RepresentationStateModel,
    SiteSyncParamsModel,
    SiteSyncSummaryItem,
    SiteSyncSummaryModel,
    SortByEnum,
    StatusEnum,
    SyncStatusModel,
)


class SiteSync(BaseServerAddon):
    settings_model: Type[SiteSyncSettings] = SiteSyncSettings

    frontend_scopes: dict[str, Any] = {"project": {}}

    def initialize(self) -> None:
        logging.info("Init SiteSync")

        self.add_endpoint(
            "/{project_name}/get_user_sites",
            self.get_user_sites,
            method="GET",
        )

        self.add_endpoint(
            "/{project_name}/params",
            self.get_site_sync_params,
            method="GET",
        )

        self.add_endpoint(
            "/{project_name}/state",
            self.get_site_sync_state,
            method="GET",
        )

        self.add_endpoint(
            "/{project_name}/state/{representation_id}/{site_name}",  # noqa
            self.set_site_sync_representation_state,
            method="POST",
        )

        self.add_endpoint(
            "/{project_name}/state/{representation_id}/{site_name}",  # noqa
            self.remove_site_sync_representation_state,
            method="DELETE",
        )
        logging.info("added endpoints")

    #
    # GET SITE SYNC PARAMS
    #

    async def get_site_sync_params(
        self,
        project_name: str = Depends(dep_project_name),
        user: UserEntity = Depends(dep_current_user),
    ) -> SiteSyncParamsModel:

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
    # GET USER SYNC SITES
    #

    async def get_user_sites(
        self,
        project_name: str = Depends(dep_project_name),
        user: UserEntity = Depends(dep_current_user),
    ) -> {}:
        sites = {}
        site_infos = await Postgres.fetch("select id, data from sites")
        for site_info in site_infos:
            settings = await self.get_project_site_settings(project_name,
                                                            user.name, 
                                                            site_info["id"])
            for site_type in ["active_site", "remote_site"]:
                used_site = settings.dict()["local_setting"][site_type]
                sites[site_type] = []

                if used_site == "local":
                    sites[site_type].append(site_info["id"])
                else:
                    sites[site_type].append(used_site)        

        return sites


    #
    # GET SITE SYNC OVERAL STATE
    #
    async def get_site_sync_state(
        self,
        project_name: str = Depends(dep_project_name),
        user: UserEntity = Depends(dep_current_user),
        representationIds: list[str] | None = Query(
            None,
            description="Filter by representation ids",
            example="['57cf375c749611ed89de0242ac140004']",
        ),
        repreNameFilter: list[str] | None = Query(None,
            description="Filter by representation name"),
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
        folderFilter: str | None = Query(
            None,
            description="Filter folders by name",
            example="sh042",
        ),
        folderIdsFilter: list[str] | None = Query(
            None,
            description="Filter folders by id, eg filtering by folder ids",
            example="['57cf375c749611ed89de0242ac140004']",
        ),
        productFilter: str | None = Query(
            None,
            description="Filter products by name",
            example="animation",
        ),
        versionFilter: int | None = Query(
            None,
            description="Filter products by version",
            example="1",
        ),
        versionIdsFilter: list[str] | None = Query(
            None,
            description="Filter versions by ids",
            example="['57cf375c749611ed89de0242ac140004']",
        ),
        localStatusFilter: list[StatusEnum] | None = Query(
            None,
            description=f"List of states to show. Available options: {StatusEnum.__doc__}",
            example=[StatusEnum.QUEUED, StatusEnum.IN_PROGRESS],
        ),
        remoteStatusFilter: list[StatusEnum] | None = Query(
            None,
            description=f"List of states to show. Available options: {StatusEnum.__doc__}",
            example=[StatusEnum.QUEUED, StatusEnum.IN_PROGRESS],
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
        # Pagination
        page: int = Query(1, ge=1),
        pageLength: int = Query(50, ge=1),
    ) -> SiteSyncSummaryModel:
        """Return a site sync state.

        Used for querying representations to be synchronized and state of
        versions and representations to show in Loader UI.
        """
        await check_sync_status_table(project_name)
        conditions = []

        if representationIds is not None:
            conditions.append(f"r.id IN {SQLTool.array(representationIds)}")

        if folderFilter:
            conditions.append(f"f.name ILIKE '%{folderFilter}%'")

        if folderIdsFilter:
            conditions.append(f"f.id IN {SQLTool.array(folderIdsFilter)}")

        if productFilter:
            conditions.append(f"p.name ILIKE '%{productFilter}%'")

        if versionFilter:
            conditions.append(f"v.version = {versionFilter}")

        if versionIdsFilter:
            conditions.append(f"v.id IN {SQLTool.array(versionIdsFilter)}")

        if localStatusFilter:
            statusFilter = [str(s.value) for s in localStatusFilter]
            conditions.append(f"local.status IN ({','.join(statusFilter)})")

        if remoteStatusFilter:
            statusFilter = [str(s.value) for s in remoteStatusFilter]
            conditions.append(f"remote.status IN ({','.join(statusFilter)})")

        if repreNameFilter:
            conditions.append(f"r.name IN {SQLTool.array(repreNameFilter)}")

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
            files = row["representation_files"]
            file_count = len(files)
            total_size = sum([f.get("size") for f in files])

            ldata = row["local_data"] or {}
            lfiles = ldata.get("files", {})
            lsize = sum([f.get("size") for f in lfiles.values()] or [0])
            ltime = max([f.get("timestamp") for f in lfiles.values()] or [0])

            rdata = row["remote_data"] or {}
            rfiles = rdata.get("files", {})
            rsize = sum([f.get("size") for f in rfiles.values()] or [0])
            rtime = max([f.get("timestamp") for f in rfiles.values()] or [0])

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
            # logging.debug(f"lfiles::{pprint.pformat(lfiles, indent=4)}")
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
        self,
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
        self,
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
    """Checks for existence of `sitesync_files_status` table, creates if not."""
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
