"""来源契约门禁回归：不访问真实 UAT。"""
import json


def test_crm_legacy_call_without_requires_env_is_unchanged():
    from app.services.api_endpoint_script_executor import check_env_requirements
    assert check_env_requirements({}) == []
    assert check_env_requirements({"timeout": 300}) == []


def test_crm_requires_confirmed_profile_and_environment():
    from app.services.api_endpoint_script_executor import check_env_requirements
    assert check_env_requirements({"requires_env": True}) == ["base_url"]
    assert check_env_requirements({"requires_env": True, "source_type": "crm_non_pos", "base_url": "https://uat"}) == []


def test_aigens_is_blocked_until_business_confirmation():
    from app.services.api_endpoint_script_executor import check_env_requirements
    missing = check_env_requirements({"requires_env": True, "channel": "aigens", "base_url": "https://uat"})
    assert "contract_confirmation" in missing


def test_missing_credentials_and_base_url_are_blocked_without_network():
    from app.services.api_endpoint_script_executor import check_env_requirements
    missing = check_env_requirements({"requires_env": True, "source_type": "crm_non_pos", "appid_ref": "", "secret_ref": ""})
    assert set(missing) == {"base_url", "appid_ref", "secret_ref"}


def test_block_reason_can_be_recorded_without_secret_leak():
    from app.services.api_endpoint_script_executor import redact_config
    from app.services.api_contract_profiles import validate_contract_for_execution
    config = {"requires_env": True, "channel": "aigens", "appsecret": "LEAK_ME", "base_url": "https://uat"}
    reason = validate_contract_for_execution(config)
    record = {"status": "blocked", "reason": reason, "execution_config": redact_config(config)}
    dumped = json.dumps(record)
    assert "contract_confirmation" in dumped
    assert "LEAK_ME" not in dumped
