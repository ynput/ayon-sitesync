"""Tests of the state endpoints against a running AYON server.

The addon package must be uploaded to the server. Tests are skipped if
the variables below are not set or the server is not reachable.

- AYON_SERVER_URL, AYON_API_KEY: server and key of a service user.
- AYON_SITESYNC_VERSION: version of the addon on the server.
- AYON_SITESYNC_PROJECT: project with published representations.
"""
import os
import uuid

import pytest

requests = pytest.importorskip("requests")

SERVER_URL = os.environ.get("AYON_SERVER_URL", "").rstrip("/")
API_KEY = os.environ.get("AYON_API_KEY")
VERSION = os.environ.get("AYON_SITESYNC_VERSION")
PROJECT = os.environ.get("AYON_SITESYNC_PROJECT")


def _server_available():
    if not (SERVER_URL and API_KEY and VERSION and PROJECT):
        return False
    try:
        response = requests.get(f"{SERVER_URL}/api/info", timeout=5)
    except requests.RequestException:
        return False
    return response.ok


pytestmark = pytest.mark.skipif(
    not _server_available(), reason="AYON server for tests is not available"
)


class Client:
    def __init__(self, user_name=None):
        self.base_url = (
            f"{SERVER_URL}/api/addons/sitesync/{VERSION}/{PROJECT}"
        )
        self.headers = {"x-api-key": API_KEY}
        if user_name:
            self.headers["x-as-user"] = user_name

    def request(self, method, path, **kwargs):
        kwargs.setdefault("timeout", 30)
        return requests.request(
            method, self.base_url + path, headers=self.headers, **kwargs
        )

    def state(self, **params):
        params.setdefault("localSite", "studio")
        params.setdefault("remoteSite", "studio")
        return self.request("GET", "/state", params=params)

    def site_states(self, representation_ids, site_names=None):
        params = {"representationIds": representation_ids}
        if site_names:
            params["siteNames"] = site_names
        return self.request("GET", "/state/representations", params=params)

    def set_state(self, representation_id, site_name, files=()):
        return self.request(
            "POST",
            f"/state/{representation_id}/{site_name}",
            json={"files": list(files)},
        )

    def remove_state(self, representation_id, site_name):
        return self.request(
            "DELETE", f"/state/{representation_id}/{site_name}"
        )


@pytest.fixture(scope="module")
def admin():
    return Client()


@pytest.fixture
def test_site():
    return f"zz_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def representation_ids(admin):
    response = admin.state(pageLength=1000)
    assert response.status_code == 200, response.text
    repre_ids = [
        repre["representationId"]
        for repre in response.json()["representations"]
    ]
    if not repre_ids:
        pytest.skip(f"Project '{PROJECT}' has no representations")
    return repre_ids


@pytest.mark.parametrize(
    "params",
    [
        {"folderFilter": "it's"},
        {"productFilter": "it's"},
        {"repreNameFilter": ["it's"]},
        {"localSite": "it's"},
        {"remoteSite": "it's"},
    ],
)
def test_state_filters_with_quotes(admin, representation_ids, params):
    response = admin.state(**params)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("param", ["folderFilter", "productFilter"])
def test_state_name_filters_match_literal_text(
    admin, representation_ids, param
):
    for value in ("%", "_' OR TRUE --"):
        response = admin.state(**{param: value})
        assert response.status_code == 200, response.text
        assert response.json()["representations"] == []


@pytest.mark.parametrize(
    "param", ["representationIds", "folderIdsFilter", "versionIdsFilter"]
)
def test_state_rejects_invalid_ids(admin, representation_ids, param):
    response = admin.state(
        **{param: [f"{representation_ids[0]}') OR TRUE --"]}
    )
    assert response.status_code == 400, response.text


def test_site_states_with_quotes(admin, representation_ids, test_site):
    response = admin.site_states(
        representation_ids[:1], site_names=[f"{test_site}' OR TRUE --"]
    )
    assert response.status_code == 200, response.text
    assert response.json() == []

    response = admin.site_states(
        [f"{representation_ids[0]}') OR TRUE --"]
    )
    assert response.status_code == 400, response.text
