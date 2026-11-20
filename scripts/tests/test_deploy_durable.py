import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("durable_deploy", Path(__file__).parents[1] / "deploy_durable_azure.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_durable_configuration_preserves_identity_and_secrets_without_provisioning():
    app = {"name": "redline", "location": "uksouth", "identity": {"type": "SystemAssigned"}, "properties": {
        "environmentId": "env", "configuration": {"ingress": {"external": True}, "secrets": [{"name": "registry"}]},
        "template": {"revisionSuffix": "old", "containers": [{"name": "redline", "image": "old", "env": [
            {"name": "APP_PASSWORD", "secretRef": "password"}, {"name": "SESSION_SECRET", "secretRef": "session"}]}]}}}
    result = module.configuration(app, [{"name": "registry", "value": "private-registry-value"}], "example.azurecr.io/redline@sha256:" + "a"*64,
                                  "postgresql://redline_app:private@host/redline?sslmode=verify-full", "https://example.blob.core.windows.net")
    assert result["identity"] == app["identity"]
    assert app["properties"]["configuration"]["ingress"]["external"]  # planner is pure
    assert not result["properties"]["configuration"]["ingress"]["external"]
    env = {e["name"]: e for e in result["properties"]["template"]["containers"][0]["env"]}
    assert env["DATABASE_URL"]["secretRef"] == "database-url" and env["APP_PASSWORD"]["secretRef"] == "password"
    assert env["AZURE_STORAGE_CONTAINER"]["value"] == "reviews"
    assert result["properties"]["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}
    with pytest.raises(ValueError, match="immutable"):
        module.configuration(app, [], "image:latest", "", "")
