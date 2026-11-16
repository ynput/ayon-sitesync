import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def provider_lib():
    path = (
        Path(__file__).resolve().parents[3]
        / "client" / "ayon_sitesync" / "providers" / "lib.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_sitesync_provider_factory", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_and_batch_limits_do_not_import_providers(monkeypatch):
    import importlib

    import_module = Mock(side_effect=AssertionError("Unexpected provider import"))
    monkeypatch.setattr(importlib, "import_module", import_module)
    path = (
        Path(__file__).resolve().parents[3]
        / "client" / "ayon_sitesync" / "providers" / "lib.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_sitesync_provider_factory", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for code, limit in (
        ("gdrive", 7), ("dropbox", 10), ("local_drive", 50),
        ("sftp", 20), ("rclone", 20),
    ):
        assert module.factory.get_provider_batch_limit(code) == limit
    import_module.assert_not_called()


@pytest.mark.parametrize("code,module_name,class_name", [
    ("gdrive", ".gdrive", "GDriveHandler"),
    ("dropbox", ".dropbox", "DropboxHandler"),
    ("local_drive", ".local_drive", "LocalDriveHandler"),
    ("sftp", ".sftp", "SFTPHandler"),
    ("rclone", ".rclone", "RCloneHandler"),
])
def test_only_requested_provider_is_imported_and_cached(
    provider_lib, monkeypatch, code, module_name, class_name
):
    handler = Mock()
    import_module = Mock(return_value=SimpleNamespace(**{class_name: handler}))
    monkeypatch.setattr(provider_lib, "import_module", import_module)
    tree = {}
    presets = {"root": "/test"}

    assert provider_lib.factory.get_provider(
        code, "project", "site", tree, presets
    ) is handler.return_value
    assert provider_lib.factory.get_provider_cls(code) is handler
    handler.assert_called_once_with("project", "site", tree, presets)
    import_module.assert_called_once_with(module_name, provider_lib.__package__)


def test_explicit_class_registration_is_preserved(provider_lib):
    handler = Mock()
    provider_lib.factory.register_provider("custom", handler, 3)

    assert provider_lib.factory.get_provider_cls("custom") is handler
    assert provider_lib.factory.get_provider_batch_limit("custom") == 3


def test_import_failure_is_not_hidden_or_cached(provider_lib, monkeypatch):
    import_module = Mock(side_effect=ModuleNotFoundError("Missing provider SDK"))
    monkeypatch.setattr(provider_lib, "import_module", import_module)

    with pytest.raises(ModuleNotFoundError, match="Missing provider SDK"):
        provider_lib.factory.get_provider_cls("dropbox")
    assert provider_lib.factory.providers["dropbox"][0] == ".dropbox.DropboxHandler"


def test_unknown_provider_is_rejected(provider_lib):
    with pytest.raises(ValueError, match="Provider unknown not registered"):
        provider_lib.factory.get_provider_cls("unknown")
