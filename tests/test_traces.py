from traces import DaemonTracer


def test_daemon_tracer_redacts_nested_secrets_without_mutating_source(tmp_path):
    tracer = DaemonTracer(path=str(tmp_path / "daemon.jsonl"))
    records = []
    tracer._emit = records.append
    payload = {
        "api_key": "top-secret",
        "nested": {
            "authorization": "Bearer secret",
            "model": "qwen-plus",
        },
    }

    tracer.on_ipc_request("chat", payload)

    traced_payload = records[0]["data"]["payload"]
    assert traced_payload["api_key"] == "[REDACTED]"
    assert traced_payload["nested"]["authorization"] == "[REDACTED]"
    assert traced_payload["nested"]["model"] == "qwen-plus"
    assert payload["api_key"] == "top-secret"
