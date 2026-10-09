"""Architecture rules API (P10 slice 2; ADR 0019): versioned saves with authors and notes,
conflicts, validation that says where, YAML import and export, and workspace isolation."""

from __future__ import annotations

import pytest

from .conftest import WEB_ORIGIN, ApiFactory, ApiHarness

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": WEB_ORIGIN}

DOCUMENT = {
    "layers": [
        {"name": "web", "match": ["com.acme.web.**"]},
        {"name": "domain", "match": ["com.acme.domain.**"], "description": "Business rules"},
    ],
    "forbid": [{"from": "domain", "to": "com.acme.legacy.**", "reason": "Being removed"}],
    "allow": [{"from": "domain", "to": "web", "reason": "Temporary", "until": "2099-12-31"}],
}


async def _project(api: ApiHarness, name: str = "Rules") -> str:
    assert api.identity is not None
    created = await api.client.post(
        "/v1/projects",
        json={"workspace_id": str(api.identity.workspace_id), "name": name},
        headers=api.auth,
    )
    return str(created.json()["id"])


async def test_rules_are_versioned_audited_and_exported(api: ApiHarness) -> None:
    project = await _project(api)
    url = f"/v1/projects/{project}/architecture-rules"
    empty = (await api.client.get(url, headers=api.auth)).json()
    assert empty["version"] == 0 and empty["document"] is None and empty["can_edit"] is True

    saved = await api.client.put(
        url,
        json={"document": DOCUMENT, "note": "First layers", "base_version": 0},
        headers=api.auth,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1 and body["current_version"] == 1
    assert body["rule_ids"] == ["arch.layers", "arch.forbid.domain-to-com-acme-legacy"]
    assert body["document"]["forbid"][0]["key"] == "domain-to-com-acme-legacy"
    assert body["document"]["forbid"][0]["from"] == "domain"  # JSON keeps the YAML names
    assert body["document"]["schema"] == "crp-architecture-rules-v1"
    (first,) = body["history"]
    assert first["note"] == "First layers" and first["created_by"] and first["source"] == "editor"
    assert (first["layers"], first["forbid"], first["allow"]) == (2, 1, 1)

    same = await api.client.put(
        url, json={"document": DOCUMENT, "base_version": 1}, headers=api.auth
    )
    assert same.json()["version"] == 1  # identical rules add no version
    stale = await api.client.put(
        url, json={"document": DOCUMENT, "base_version": 0}, headers=api.auth
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    assert stale.json()["details"]["current_version"] == 1

    exported = await api.client.get(f"{url}/export", headers=api.auth)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/yaml")
    assert 'filename="architecture-rules-v1.yaml"' in exported.headers["content-disposition"]
    text = exported.text
    assert text.startswith("# refactorX architecture rules") and "until: 2099-12-31" in text

    # The export imports unchanged into another project: same canonical rules, same hash.
    other = await _project(api, "Imported")
    imported = await api.client.put(
        f"/v1/projects/{other}/architecture-rules",
        json={"yaml": text, "note": "From Rules", "base_version": 0},
        headers=api.auth,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["sha256"] == body["sha256"]
    assert imported.json()["history"][0]["source"] == "yaml"

    changed = dict(DOCUMENT, layering="next")
    second = await api.client.put(
        url, json={"document": changed, "note": "Stricter", "base_version": 1}, headers=api.auth
    )
    assert second.json()["version"] == 2
    assert [h["version"] for h in second.json()["history"]] == [2, 1]
    old = (await api.client.get(f"{url}?version=1", headers=api.auth)).json()
    assert old["version"] == 1 and old["current_version"] == 2
    assert old["document"]["layering"] == "lower"
    missing = await api.client.get(f"{url}?version=9", headers=api.auth)
    assert missing.status_code == 404


async def test_invalid_rules_are_refused_with_reasons(api: ApiHarness) -> None:
    project = await _project(api)
    url = f"/v1/projects/{project}/architecture-rules"
    bad_yaml = await api.client.put(
        url,
        json={"yaml": "layers:\n  - name: web\n    match: ['a//b']\n", "base_version": 0},
        headers=api.auth,
    )
    assert bad_yaml.status_code == 422 and bad_yaml.json()["code"] == "invalid_rules"
    assert any("empty name part" in p for p in bad_yaml.json()["details"]["problems"])
    aliases = await api.client.put(
        url, json={"yaml": "layers: &x []\nforbid: *x\n", "base_version": 0}, headers=api.auth
    )
    assert aliases.status_code == 422
    both = await api.client.put(
        url, json={"yaml": "{}", "document": {}, "base_version": 0}, headers=api.auth
    )
    assert both.status_code == 422 and both.json()["code"] == "invalid_rules"
    unknown_layer = await api.client.put(
        url,
        json={"document": {"forbid": [{"from": "a b", "to": "c"}]}, "base_version": 0},
        headers=api.auth,
    )
    assert unknown_layer.status_code == 422
    assert (await api.client.get(url, headers=api.auth)).json()["version"] == 0


async def test_members_edit_rules_and_other_workspaces_see_nothing(
    api_factory: ApiFactory,
) -> None:
    api = await api_factory(demo_enabled=True)
    owner_project = await _project(api)
    await api.client.post("/v1/auth/demo-session", headers=ORIGIN)
    workspace = (await api.client.get("/v1/auth/me")).json()["workspaces"][0]["workspace_id"]
    mine = await api.client.post(
        "/v1/projects", json={"workspace_id": workspace, "name": "Demo rules"}, headers=ORIGIN
    )
    demo_project = mine.json()["id"]
    view = (await api.client.get(f"/v1/projects/{demo_project}/architecture-rules")).json()
    assert view["can_edit"] is True  # members decide their architecture
    saved = await api.client.put(
        f"/v1/projects/{demo_project}/architecture-rules",
        json={"document": DOCUMENT, "base_version": 0},
        headers=ORIGIN,
    )
    assert saved.status_code == 200
    hidden = await api.client.get(f"/v1/projects/{owner_project}/architecture-rules")
    assert hidden.status_code == 404
    refused = await api.client.put(
        f"/v1/projects/{owner_project}/architecture-rules",
        json={"document": DOCUMENT, "base_version": 0},
        headers=ORIGIN,
    )
    assert refused.status_code == 404
