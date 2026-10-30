"""Preflight must fail closed without leaking configuration or contacting services."""
import json
import runpy
import socket
import sys
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from app import broker_paper_preflight as cli
from app.core import config
from app.services import broker_oauth_service, credential_cipher


@pytest.fixture
def configured_settings(monkeypatch):
    settings = SimpleNamespace(auth_enabled=True,
        alpaca_oauth_client_id="private-application-id",
        alpaca_oauth_client_secret="private-application-secret",
        alpaca_oauth_redirect_uri="https://example.test/api/brokers/oauth/alpaca/callback",
        broker_encryption_key=Fernet.generate_key().decode())
    for module in (config, broker_oauth_service, credential_cipher):
        monkeypatch.setattr(module, "settings", settings)
    def no_network(*args, **kwargs):
        pytest.fail("Offline preflight attempted network access")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    return settings


def test_valid_is_only_configuration(configured_settings, capsys):
    assert cli.main() == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["configuration_valid"] is True
    assert report["execution_enabled"] is False
    assert report["external_validation_required"] is True
    assert report["environment"] == "paper"
    for value in vars(configured_settings).values():
        if isinstance(value, str):
            assert value not in output


@pytest.mark.parametrize("field,value,check", [
    ("auth_enabled", False, "authentication_enabled"),
    ("alpaca_oauth_client_id", "", "application_client_id_present"),
    ("alpaca_oauth_client_id", "  ", "application_client_id_present"),
    ("alpaca_oauth_client_secret", "", "application_client_secret_present"),
    ("alpaca_oauth_client_secret", "  ", "application_client_secret_present"),
    ("broker_encryption_key", "", "encryption_key_valid"),
    ("broker_encryption_key", "private-invalid-key", "encryption_key_valid"),
    ("broker_encryption_key", "\u00e9", "encryption_key_valid"),
    ("alpaca_oauth_redirect_uri", "http://example.test/api/brokers/oauth/alpaca/callback", "oauth_configuration_valid"),
    ("alpaca_oauth_redirect_uri", "https://example.test/wrong", "oauth_configuration_valid"),
    ("alpaca_oauth_redirect_uri", "https://example.test/api/brokers/oauth/alpaca/callback?secret=private", "oauth_configuration_valid"),
    ("alpaca_oauth_redirect_uri", "https://[", "oauth_configuration_valid"),
])
def test_bad_configuration(configured_settings, capsys, field, value, check):
    setattr(configured_settings, field, value)
    assert cli.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["configuration_valid"] is False
    assert report["checks"][check] is False
    assert "private" not in output


def test_unexpected_failure_never_prints_exception(monkeypatch, capsys):
    def fail():
        raise ValueError("secret-settings-input")
    monkeypatch.setattr(cli, "configuration_report", fail)
    assert cli.main() == 2
    output = capsys.readouterr().out
    assert "secret-settings-input" not in output
    assert json.loads(output)["configuration_valid"] is False


def test_module_entrypoint(configured_settings, capsys, monkeypatch):
    monkeypatch.delitem(sys.modules, "app.broker_paper_preflight")
    with pytest.raises(SystemExit) as result:
        runpy.run_module("app.broker_paper_preflight", run_name="__main__")
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["execution_enabled"] is False
