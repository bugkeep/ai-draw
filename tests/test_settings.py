import secrets
import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from studio.llm import request_options, litellm
from studio.settings import ModelConfig, ModelSettings, PROVIDERS, SettingsUpdate
from studio.store import StudioStore, VersionConflict


def test_settings_encrypt_keys_and_preserve_them_after_restart(tmp_path):
    path = tmp_path / "settings.sqlite3"
    store = StudioStore(path)
    settings = ModelSettings(store)
    secret = secrets.token_urlsafe(32)
    update = SettingsUpdate(provider="openai", model="drawing-model", api_base="https://api.openai.com/v1", api_key=secret)
    view = settings.save(update)
    assert view["revision"] == 1 and view["has_api_key"]
    assert secret not in str(view)
    assert store.get_model_settings()["encrypted_key"] != secret
    assert secret not in store.get_model_settings()["encrypted_key"]
    retained = SettingsUpdate(**view["config"], revision=1)
    settings.save(retained)
    store.close()

    reopened = StudioStore(path)
    assert ModelSettings(reopened).load().api_key.get_secret_value() == secret
    with pytest.raises(VersionConflict):
        ModelSettings(reopened).save(retained)
    reopened.close()


def test_switching_connection_requires_its_own_key(tmp_path):
    store = StudioStore(tmp_path / "settings.sqlite3")
    settings = ModelSettings(store)
    settings.save(SettingsUpdate(provider="openai", model="drawing-model", api_key=secrets.token_urlsafe(32)))
    with pytest.raises(ValueError, match="重新填写"):
        settings.candidate(SettingsUpdate(provider="anthropic", model="drawing-model", revision=1))
    local = settings.candidate(SettingsUpdate(provider="local", model="drawing-model", api_base="http://localhost:1234/v1", revision=1))
    assert local.configured and not local.api_key.get_secret_value()
    with pytest.raises(ValueError, match="受保护字段"):
        SettingsUpdate(request_parameters={"api_key": secrets.token_urlsafe(32)})
    store.close()


def test_every_preset_routes_to_an_installed_provider():
    for preset in PROVIDERS:
        if preset["route"]:
            assert preset["route"] in litellm.provider_list
            config = ModelConfig(provider=preset["id"], model="drawing-model", api_base=preset["api_base"],
                                 api_key=secrets.token_urlsafe(32))
            assert request_options(config)["model"] == f"{preset['route']}/drawing-model"
    responses = ModelConfig(provider="openai", model="drawing-model", api_mode="responses", api_key=secrets.token_urlsafe(32))
    assert request_options(responses)["model"] == "openai/responses/drawing-model"


def test_settings_save_is_blocked_by_tasks_in_any_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "http.sqlite3"))
    with TestClient(create_app()) as client:
        settings = client.get("/api/studio/settings").json()
        body = {**settings["config"], "revision": settings["revision"]}
        session_id = client.post("/api/studio/sessions", json={}).json()["id"]
        task_id = client.post(f"/api/studio/sessions/{session_id}/messages",
                              json={"message": "用画笔画一张笑脸"}).json()["id"]
        client.post("/api/studio/sessions", json={"title": "另一个窗口"})
        assert client.get("/api/studio/settings").json()["busy"]
        response = client.put("/api/studio/settings", json=body)
        assert response.status_code == 409
        for _ in range(150):
            if client.get(f"/api/studio/tasks/{task_id}").json()["status"] == "completed":
                break
            time.sleep(0.02)
        assert not client.get("/api/studio/settings").json()["busy"]
        body.update(provider="local", api_base="http://localhost:1234/v1", model="drawing-model",
                    api_key=secrets.token_urlsafe(32))
        assert client.put("/api/studio/settings", json=body).status_code == 200


def test_settings_validation_does_not_return_request_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "http.sqlite3"))
    secret = secrets.token_urlsafe(32)
    with TestClient(create_app()) as client:
        response = client.put("/api/studio/settings", json={"provider": "unknown", "api_key": secret})
        assert response.status_code == 422
        assert secret not in response.text
