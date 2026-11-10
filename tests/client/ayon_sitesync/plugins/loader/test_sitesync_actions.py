"""Tests for the Site Sync loader action plugin."""
from __future__ import annotations

import json
import threading
from typing import Any, Optional

import pytest

from ayon_core.pipeline.actions import (
    LoaderActionsContext,
    LoaderActionSelection,
    SelectionEntitiesCache,
)
from ayon_sitesync.addon import SiteSyncAddon
from ayon_sitesync.plugins.loader import sitesync_actions as mod
from ayon_sitesync.plugins.loader.sitesync_actions import (
    SiteSyncActionPlugin,
)

PROJECT_NAME = "test_project"
ACTIVE_SITE = "local_site"
REMOTE_SITE = "studio"
ENABLED_SETTINGS = {"sitesync": {"enabled": True}}

# Local and remote sync progress by representation id
SYNC_STATE = {
    "repre_local": (1, 0),
    "repre_remote": (0, 1),
    "repre_both": (1, 1),
    "repre_none": (0, 0),
}


class _FakeSiteSyncAddon:
    name = "sitesync"

    def __init__(self):
        self.sync_state = dict(SYNC_STATE)
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str, bool]] = []
        self.sync_state_queries: list[set[str]] = []
        # Set to block 'add_site' until the event is set
        self.add_site_event: Optional[threading.Event] = None

    def get_active_site(self, project_name):
        return ACTIVE_SITE

    def get_remote_site(self, project_name):
        return REMOTE_SITE

    def get_representations_sync_state(
        self, project_name, representation_ids, local_site, remote_site
    ):
        self.sync_state_queries.append(set(representation_ids))
        return {
            repre_id: self.sync_state[repre_id]
            for repre_id in representation_ids
            if repre_id in self.sync_state
        }

    def add_site(self, project_name, representation_id, site_name, force):
        if self.add_site_event is not None:
            assert self.add_site_event.wait(5)
        self.added.append((representation_id, site_name))

    def remove_site(
        self, project_name, representation_id, site_name, remove_local_files
    ):
        self.removed.append(
            (representation_id, site_name, remove_local_files)
        )

    def is_representation_on_site(self, *args, **kwargs):
        raise AssertionError("Use one sync state query per chunk instead")


class _NoQueryAddon(_FakeSiteSyncAddon):
    """Addon that fails on any call which may query the server."""

    def _fail(self, *args, **kwargs):
        raise AssertionError("Unexpected Site Sync query")

    get_active_site = _fail
    get_remote_site = _fail
    get_representations_sync_state = _fail


class _FakeAddonsManager:
    def __init__(self, addon):
        self.addons = [addon]
        self._addon = addon

    def get(self, name):
        if name == "sitesync":
            return self._addon
        return None


class _FakeContext:
    def __init__(self, addon):
        self._addons_manager = _FakeAddonsManager(addon)

    def get_addons_manager(self):
        return self._addons_manager

    def get_host_name(self):
        return None


def _make_selection(
    repre_ids: set[str],
    selected_type: str = "representation",
    product_type: str = "render",
    project_settings: Optional[dict[str, Any]] = None,
) -> LoaderActionSelection:
    if project_settings is None:
        project_settings = ENABLED_SETTINGS
    representations_by_id = {
        repre_id: {"id": repre_id, "versionId": "version_id"}
        for repre_id in SYNC_STATE
    }
    entities_cache = SelectionEntitiesCache(
        PROJECT_NAME,
        products_by_id={
            "product_id": {"id": "product_id", "productType": product_type}
        },
        versions_by_id={
            "version_id": {"id": "version_id", "productId": "product_id"}
        },
        representations_by_id=representations_by_id,
    )
    return LoaderActionSelection(
        PROJECT_NAME,
        repre_ids,
        selected_type,
        project_settings=project_settings,
        entities_cache=entities_cache,
    )


def _items_by_action(items) -> dict[str, Any]:
    return {item.data["action"]: item for item in items}


def _execute(plugin, action, repre_ids, **kwargs):
    return plugin.execute_action(
        _make_selection(set(repre_ids), **kwargs),
        {"action": action, "representation_ids": list(repre_ids)},
        {},
    )


def _wait_for_jobs(plugin):
    # Single worker, so this waits for all previous jobs
    plugin._executor.submit(lambda: None).result(timeout=5)


@pytest.fixture
def linked_repre_ids(monkeypatch):
    """Ids of representations the workfile 'version_id' references."""
    repre_ids = ["linked_repre"]

    def _get_versions_links(project_name, version_ids, **kwargs):
        assert version_ids == {"version_id"}
        return {
            "version_id": [
                {"entityType": "version", "entityId": "linked_version"},
                {"entityType": "version", "entityId": "version_id"},
                {"entityType": "folder", "entityId": "some_folder"},
            ]
        }

    def _get_representations(project_name, version_ids, fields):
        assert version_ids == {"linked_version"}
        return [{"id": repre_id} for repre_id in repre_ids]

    monkeypatch.setattr(
        mod.ayon_api, "get_versions_links", _get_versions_links
    )
    monkeypatch.setattr(
        mod.ayon_api, "get_representations", _get_representations
    )
    return repre_ids


@pytest.mark.parametrize(
    "project_settings",
    [{}, {"sitesync": {}}, {"sitesync": {"enabled": False}}],
)
def test_no_queries_when_project_disabled(project_settings):
    plugin = SiteSyncActionPlugin(_FakeContext(_NoQueryAddon()))
    selection = _make_selection(
        set(SYNC_STATE), project_settings=project_settings
    )
    assert plugin.get_action_items(selection) == []
    # Disabled state is cached as well
    assert plugin.get_action_items(selection) == []


def test_no_queries_for_versions():
    plugin = SiteSyncActionPlugin(_FakeContext(_NoQueryAddon()))
    selection = _make_selection({"version_id"}, selected_type="version")

    def _fail():
        raise AssertionError("Project settings should not be needed")

    selection.get_project_settings = _fail
    assert plugin.get_action_items(selection) == []


def test_action_items_match_sync_state():
    plugin = SiteSyncActionPlugin(_FakeContext(_FakeSiteSyncAddon()))
    items = plugin.get_action_items(_make_selection(set(SYNC_STATE)))

    by_action = _items_by_action(items)
    assert set(by_action) == {"download", "upload", "remove"}
    assert by_action["download"].label == "Download"

    repre_ids = {
        action: set(item.data["representation_ids"])
        for action, item in by_action.items()
    }
    assert repre_ids["download"] == {"repre_remote", "repre_both"}
    assert repre_ids["upload"] == {"repre_local", "repre_both"}
    assert repre_ids["remove"] == {"repre_local", "repre_both"}
    # Data is sent through the UI so it must be serializable
    for item in items:
        json.dumps(item.data)


def test_only_download_when_nothing_is_local():
    plugin = SiteSyncActionPlugin(_FakeContext(_FakeSiteSyncAddon()))
    items = plugin.get_action_items(_make_selection({"repre_remote"}))
    assert set(_items_by_action(items)) == {"download"}


def test_sync_state_is_cached_until_action_runs():
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    plugin.get_action_items(_make_selection({"repre_local"}))
    plugin.get_action_items(_make_selection({"repre_local"}))
    # Only uncached representations are queried
    plugin.get_action_items(_make_selection({"repre_local", "repre_both"}))
    assert addon.sync_state_queries == [{"repre_local"}, {"repre_both"}]

    _execute(plugin, "upload", ["repre_local"])
    _wait_for_jobs(plugin)
    plugin.get_action_items(_make_selection({"repre_local"}))
    assert addon.sync_state_queries[-1] == {"repre_local"}


def test_sync_state_is_queried_in_chunks():
    """Server returns at most 50 representations per request."""
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    repre_ids = {f"repre_{idx}" for idx in range(120)}
    addon.sync_state.update({repre_id: (0, 1) for repre_id in repre_ids})

    items = plugin.get_action_items(_make_selection(repre_ids))

    assert sorted(len(ids) for ids in addon.sync_state_queries) == [
        20, 50, 50
    ]
    download_item = _items_by_action(items)["download"]
    assert set(download_item.data["representation_ids"]) == repre_ids


@pytest.mark.parametrize(
    "action, expected_site",
    [("download", ACTIVE_SITE), ("upload", REMOTE_SITE)],
)
def test_execute_adds_site(action, expected_site):
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    repre_ids = ["repre_both", "repre_local"]

    result = _execute(plugin, action, repre_ids)
    _wait_for_jobs(plugin)

    assert sorted(addon.added) == sorted(
        (repre_id, expected_site) for repre_id in repre_ids
    )
    assert addon.removed == []
    assert result.success


def test_execute_remove_from_local():
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    _execute(plugin, "remove", ["repre_local"])
    _wait_for_jobs(plugin)

    assert addon.removed == [("repre_local", ACTIVE_SITE, True)]
    assert addon.added == []


def test_execute_fails_when_project_disabled():
    addon = _NoQueryAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    result = _execute(plugin, "download", ["repre_local"], project_settings={})
    _wait_for_jobs(plugin)

    assert not result.success
    assert addon.added == []


def test_execute_does_not_wait_for_server():
    addon = _FakeSiteSyncAddon()
    addon.add_site_event = threading.Event()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    result = _execute(plugin, "download", ["repre_remote"])
    assert result.success
    assert addon.added == []

    # Status may change while the job runs, so it is not cached
    plugin.get_action_items(_make_selection({"repre_remote"}))
    plugin.get_action_items(_make_selection({"repre_remote"}))
    assert len(addon.sync_state_queries) == 2

    addon.add_site_event.set()
    _wait_for_jobs(plugin)
    assert addon.added == [("repre_remote", ACTIVE_SITE)]


def test_execute_workfile_syncs_linked_representations(linked_repre_ids):
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    _execute(plugin, "download", ["repre_remote"], product_type="workfile")
    _wait_for_jobs(plugin)

    assert addon.added == [
        ("repre_remote", ACTIVE_SITE),
        ("linked_repre", ACTIVE_SITE),
    ]


def test_linked_on_site_check_is_batched(linked_repre_ids):
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    linked_repre_ids[:] = [f"linked_{idx}" for idx in range(60)]
    # Already on the site, so it is skipped
    addon.sync_state["linked_0"] = (1, 1)

    _execute(plugin, "download", ["repre_remote"], product_type="workfile")
    _wait_for_jobs(plugin)

    assert sorted(len(ids) for ids in addon.sync_state_queries) == [10, 50]
    added_ids = {repre_id for repre_id, _ in addon.added}
    assert added_ids == {"repre_remote"} | set(linked_repre_ids[1:])


def test_linked_status_is_queried_again_after_action(linked_repre_ids):
    """Cached status of a linked representation is not kept.

    Reproduce: right-click a representation referenced by a workfile,
    download the workfile, right-click the referenced one again.

    """
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    addon.sync_state["linked_repre"] = (0.5, 1)
    linked_selection = _make_selection({"linked_repre"})
    items = plugin.get_action_items(linked_selection)
    assert "upload" in _items_by_action(items)

    _execute(plugin, "download", ["repre_remote"], product_type="workfile")
    _wait_for_jobs(plugin)
    # Site was reset to be downloaded again
    addon.sync_state["linked_repre"] = (0, 1)

    items = plugin.get_action_items(linked_selection)
    assert set(_items_by_action(items)) == {"download"}


def test_addon_registers_plugin():
    """Browser and legacy Loader get the actions through discovery."""
    addon = SiteSyncAddon(None, {})
    context = LoaderActionsContext(
        studio_settings={},
        addons_manager=_FakeAddonsManager(addon),
        host=None,
    )
    plugin = context._get_plugins().get("sitesync.actions")
    # Discovery imports the file again, compare by name instead of class
    assert type(plugin).__name__ == SiteSyncActionPlugin.__name__
