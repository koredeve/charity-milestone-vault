from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


def test_deploy_smoke():
    factory = get_contract_factory("CharityMilestoneVault")
    contract = factory.deploy(args=[])
    result = contract.total_projects(args=[]).call()
    assert result is not None
    assert int(result) == 0
