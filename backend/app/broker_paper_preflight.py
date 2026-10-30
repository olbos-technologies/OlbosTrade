"""Offline configuration check: python -m app.broker_paper_preflight.

Never opens a broker/DB connection, prints settings, or authorizes execution.
"""
import json


def configuration_report():
    # Load lazily so even settings-validation errors cannot print secret inputs.
    from cryptography.fernet import Fernet
    from app.core.config import settings
    from app.services.broker_oauth_service import configured

    encryption_valid = False
    try:
        Fernet(settings.broker_encryption_key.encode("utf-8"))
        encryption_valid = True
    except (ValueError, TypeError, UnicodeError):
        pass
    checks = {
        "authentication_enabled": settings.auth_enabled is True,
        "application_client_id_present": bool(settings.alpaca_oauth_client_id.strip()),
        "application_client_secret_present": bool(settings.alpaca_oauth_client_secret.strip()),
        "encryption_key_valid": encryption_valid,
        "oauth_configuration_valid": configured(),
    }
    return {
        "configuration_valid": all(checks.values()),
        "checks": checks,
        "environment": "paper",
        "permission": "read_only",
        "execution_enabled": False,
        "external_validation_required": True,
        "scope": "Offline configuration only; no database, broker, deployment or approval checks.",
    }


def main():
    try:
        report = configuration_report()
    except Exception:
        # Pydantic/library errors can contain input values: never print them.
        print(json.dumps({"configuration_valid": False,
                          "error": "Configuration could not be validated; inspect setting names privately.",
                          "execution_enabled": False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["configuration_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
