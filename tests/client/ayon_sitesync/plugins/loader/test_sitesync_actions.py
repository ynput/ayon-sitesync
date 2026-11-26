"""Tests for the Site Sync loader action plugin."""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from ayon_core.pipeline.actions import (
    LoaderActionsContext,
    LoaderActionSelection,
    SelectionEntitiesCache,
)
from ayon_sitesync import addon as addon_mod
from ayon_sitesync.addon import SiteSyncAddon
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
        self.added: list[tuple[set[str], str, bool]] = []
        self.removed: list[tuple[set[str], str, bool]] = []
        self.sync_state_queries: list[set[str]] = []
        # Representations the server queues too, e.g. referenced by workfile
        self.referenced_ids: list[str] = []
        self.error: Optional[Exception] = None

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

    def add_site_to_representations(
        self, project_name, representation_ids, site_name, add_referenced
    ):
        self.added.append((set(representation_ids), site_name, add_referenced))
        if self.error is not None:
            raise self.error
        for repre_id in self.referenced_ids:
            self.sync_state[repre_id] = (0, 1)
        return list(representation_ids) + self.referenced_ids

    def remove_site_from_representations(
        self, project_name, representation_ids, site_name, remove_local_files
    ):
        self.removed.append(
            (set(representation_ids), site_name, remove_local_files)
        )
        if self.error is not None:
            raise self.error
        return list(representation_ids)


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


class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass


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

    # All representations in one call, server adds referenced ones
    assert addon.added == [(set(repre_ids), expected_site, True)]
    assert addon.removed == []
    assert result.success


def test_execute_remove_from_local():
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    result = _execute(plugin, "remove", ["repre_local", "repre_both"])

    assert addon.removed == [
        ({"repre_local", "repre_both"}, ACTIVE_SITE, True)
    ]
    assert addon.added == []
    assert result.success


def test_execute_fails_when_project_disabled():
    addon = _NoQueryAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))

    result = _execute(plugin, "download", ["repre_local"], project_settings={})

    assert not result.success
    assert addon.added == []


def test_referenced_status_is_queried_again_after_action():
    """Cached status of representations referenced by workfile is cleared.

    Action on a workfile changes the site of its references too.

    """
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    addon.sync_state["referenced_repre"] = (0.5, 1)
    addon.referenced_ids = ["referenced_repre"]
    referenced_selection = _make_selection({"referenced_repre"})
    items = plugin.get_action_items(referenced_selection)
    assert "upload" in _items_by_action(items)

    _execute(plugin, "download", ["repre_remote"], product_type="workfile")

    # Site was reset to be downloaded again
    items = plugin.get_action_items(referenced_selection)
    assert set(_items_by_action(items)) == {"download"}


@pytest.mark.parametrize("action", ["download", "remove"])
def test_status_is_queried_again_after_failed_action(action):
    """Remove may fail on local files after the site was removed."""
    addon = _FakeSiteSyncAddon()
    plugin = SiteSyncActionPlugin(_FakeContext(addon))
    plugin.get_action_items(_make_selection({"repre_both"}))
    addon.error = ValueError("File cannot be removed")

    with pytest.raises(ValueError):
        _execute(plugin, action, ["repre_both"])

    plugin.get_action_items(_make_selection({"repre_both"}))
    assert addon.sync_state_queries == [{"repre_both"}, {"repre_both"}]


def test_addon_adds_site_in_one_request(monkeypatch):
    requests = []

    def _post(endpoint, **kwargs):
        requests.append((endpoint, kwargs))
        return _FakeResponse({"representationIds": ["repre_a", "linked"]})

    monkeypatch.setattr(addon_mod.ayon_api, "post", _post)
    addon = SiteSyncAddon(None, {})

    repre_ids = addon.add_site_to_representations(
        PROJECT_NAME, {"repre_a"}, ACTIVE_SITE, add_referenced=True
    )

    assert repre_ids == ["repre_a", "linked"]
    assert requests == [(
        f"{addon.endpoint_prefix}/{PROJECT_NAME}/add_site",
        {
            "siteName": ACTIVE_SITE,
            "representationIds": ["repre_a"],
            "addReferenced": True,
        },
    )]


def test_addon_removes_local_files_of_removed_sites_only(monkeypatch):
    """Server skips representations without the site or access."""
    requests = []
    removed_files = []

    def _post(endpoint, **kwargs):
        requests.append((endpoint, kwargs))
        return _FakeResponse({"representationIds": ["repre_a"]})

    monkeypatch.setattr(addon_mod.ayon_api, "post", _post)
    addon = SiteSyncAddon(None, {})
    monkeypatch.setattr(
        addon, "_remove_local_files", lambda *args: removed_files.append(args)
    )

    repre_ids = addon.remove_site_from_representations(
        PROJECT_NAME, ["repre_a", "repre_b"], ACTIVE_SITE,
        remove_local_files=True,
    )

    assert repre_ids == ["repre_a"]
    assert [endpoint for endpoint, _ in requests] == [
        f"{addon.endpoint_prefix}/{PROJECT_NAME}/remove_site"
    ]
    assert removed_files == [(PROJECT_NAME, ["repre_a"], ACTIVE_SITE)]


def test_addon_removes_other_local_files_if_one_fails(monkeypatch, tmp_path):
    """Sites are already removed, so remove all files that can be removed."""
    files_by_repre_id = {
        "repre_locked": ["locked/a.ma", "locked/b.ma"],
        "repre_missing": ["missing/a.ma"],
        "repre_ok": ["ok/a.ma"],
    }
    for path in ("locked/a.ma", "locked/b.ma", "ok/a.ma"):
        (tmp_path / path).parent.mkdir(exist_ok=True)
        (tmp_path / path).write_text("")
    locked_path = str(tmp_path / "locked" / "a.ma")
    os_remove = addon_mod.os.remove

    def _remove(path):
        if path == locked_path:
            raise PermissionError(path)
        os_remove(path)

    monkeypatch.setattr(addon_mod.os, "remove", _remove)
    monkeypatch.setattr(addon_mod, "get_local_site_id", lambda: ACTIVE_SITE)
    monkeypatch.setattr(
        addon_mod,
        "get_representations",
        lambda project_name, representation_ids, fields: [
            {"id": repre_id, "files": [{"path": path} for path in paths]}
            for repre_id, paths in files_by_repre_id.items()
        ],
    )
    addon = SiteSyncAddon(None, {})
    monkeypatch.setattr(
        addon, "get_provider_for_site", lambda site: "local_drive"
    )
    monkeypatch.setattr(
        addon,
        "get_local_file_path",
        lambda project_name, site_name, path: str(tmp_path / path),
    )

    with pytest.raises(ValueError, match="1 local file"):
        addon._remove_local_files(
            PROJECT_NAME, list(files_by_repre_id), ACTIVE_SITE
        )

    remaining = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
    )
    assert remaining == ["locked", "locked/a.ma"]


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
