import pytest

from brain import mcphttp, mcprouter, remoteauth
from brain.cli import main

ENV = {"BRAIN_MCP_ISSUER": "https://team.example.cloudflareaccess.com",
       "BRAIN_MCP_JWKS_URL": "https://team.example.cloudflareaccess.com/cdn-cgi/access/certs",
       "BRAIN_MCP_AUDIENCE": "aud-tag-1"}


@pytest.fixture
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def test_http_mode_passes_env_auth_and_flags(env, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(mcphttp, "run_vault_server", lambda vault, **kw: seen.update(kw, vault=vault) or 0)
    rc = main(["mcp", "--http", "--vault", str(tmp_path / "bob"), "--person", "bob",
               "--person-email", "bob@acme.com", "--port", "8901", "--spool", str(tmp_path)])
    assert rc == 0
    assert seen["person"] == "bob" and seen["port"] == 8901 and seen["spool"] == tmp_path
    assert seen["auth"].audience == "aud-tag-1"


def test_http_mode_needs_person_email_and_port(env, capsys, tmp_path):
    assert main(["mcp", "--http", "--vault", str(tmp_path)]) == 2
    assert "--person" in capsys.readouterr().err


def test_http_mode_without_identity_settings_refuses(monkeypatch, capsys, tmp_path):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert main(["mcp", "--http", "--vault", str(tmp_path), "--person", "bob",
                 "--person-email", "b@x.com", "--port", "8901"]) == 2
    assert "BRAIN_MCP_ISSUER" in capsys.readouterr().err


def test_missing_extra_is_named(env, monkeypatch, capsys, tmp_path):
    def boom():
        raise remoteauth.RemoteAuthError("remote MCP needs the optional extra — install 'brainkit[remote]'")
    monkeypatch.setattr(remoteauth, "_require_jwt", boom)
    assert main(["mcp-router", "--routes", str(tmp_path / "r.yaml")]) == 2
    assert "brainkit[remote]" in capsys.readouterr().err


def test_router_passes_routes_and_port(env, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(mcprouter, "run_router", lambda path, **kw: seen.update(kw, path=path) or 0)
    assert main(["mcp-router", "--routes", str(tmp_path / "r.yaml")]) == 0
    assert seen["port"] == 8900 and seen["path"] == tmp_path / "r.yaml"


def test_stdio_mode_is_unchanged(monkeypatch, tmp_path):
    called = {}
    import brain.mcp as stdio
    monkeypatch.setattr(stdio, "serve", lambda vault: called.setdefault("vault", vault))
    assert main(["mcp", "--vault", str(tmp_path)]) == 0
    assert called["vault"] == tmp_path
