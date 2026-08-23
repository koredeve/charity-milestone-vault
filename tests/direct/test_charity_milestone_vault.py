import json

PAYOUT = 5 * 10**18
EVIDENCE_URL = "https://example.com/evidence/field-report"
WEB_REGEX = r"https://example\.com/evidence/.*"
LLM_REGEX = r"Verify charitable milestone"
PAGE_BODY = json.dumps(
    {
        "title": "Field report",
        "content": "The borehole was drilled and water quality tests passed on site.",
    }
)

MILESTONES = ["Drill well", "Train caretakers"]


def _deploy(direct_vm, direct_deploy, creator):
    direct_vm.sender = creator
    return direct_deploy("contracts/CharityMilestoneVault.py")


def _create_project(direct_vm, contract, creator, pid="proj-1"):
    direct_vm.sender = creator
    contract.create_project(
        pid, "Clean Water", "Build wells in two villages", MILESTONES, PAYOUT
    )


def _donate(direct_vm, contract, donor, pid, amount):
    direct_vm.sender = donor
    direct_vm.value = amount
    contract.donate(pid)
    direct_vm.value = 0


def _submit_evidence(direct_vm, contract, creator, pid, idx):
    direct_vm.sender = creator
    contract.submit_evidence(pid, idx, EVIDENCE_URL, "Contractor invoice and photos attached.")


def _mock_oracle(direct_vm, completed=True):
    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"completed": completed, "reasoning": "evidence confirms completion"}),
    )


def _verify(direct_vm, contract, creator, pid, idx, completed=True):
    _mock_oracle(direct_vm, completed)
    direct_vm.sender = creator
    contract.verify_milestone(pid, idx)


def test_create_two_milestone_project(direct_vm, direct_deploy, direct_alice):
    """A creator lists a project with two fixed-payout milestones, all pending."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)

    info = contract.get_project("proj-1")
    assert info["name"] == "Clean Water"
    assert len(info["creator"]) > 0
    assert info["milestones"] == MILESTONES
    assert info["milestone_status"] == ["pending", "pending"]
    assert info["payout_per_milestone_atto"] == PAYOUT
    assert info["raised_atto"] == 0
    assert info["released_atto"] == 0
    assert info["status"] == "active"
    assert contract.total_projects() == 1


def test_donations_pool_and_donor_amounts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Donations accumulate in the pool and per-donor amounts are tracked."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _donate(direct_vm, contract, direct_bob, "proj-1", 2 * 10**18)

    info = contract.get_project("proj-1")
    assert info["raised_atto"] == 10 * 10**18

    direct_vm.sender = direct_alice
    assert contract.my_donation("proj-1") == 8 * 10**18
    direct_vm.sender = direct_bob
    assert contract.my_donation("proj-1") == 2 * 10**18


def test_zero_value_donation_reverts(direct_vm, direct_deploy, direct_alice):
    """A donation with zero value is rejected."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Send value"):
        contract.donate("proj-1")

    assert contract.get_project("proj-1")["raised_atto"] == 0


def test_non_creator_cannot_submit_evidence(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Only the project creator may submit milestone evidence."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the project creator"):
        contract.submit_evidence("proj-1", 0, EVIDENCE_URL, "Forged evidence.")


def test_verified_milestone_releases_payout(direct_vm, direct_deploy, direct_alice):
    """A verified milestone releases its fixed payout as a withdrawable credit."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 0)

    _verify(direct_vm, contract, direct_alice, "proj-1", 0, completed=True)

    info = contract.get_project("proj-1")
    assert info["milestone_status"][0] == "done"
    assert info["milestone_status"][1] == "pending"
    assert info["released_atto"] == PAYOUT
    assert info["status"] == "active"
    assert contract.credit_of(direct_alice) == PAYOUT


def test_double_verification_of_done_milestone_reverts(direct_vm, direct_deploy, direct_alice):
    """An already-done milestone cannot be verified again."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 0)
    _verify(direct_vm, contract, direct_alice, "proj-1", 0, completed=True)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Milestone is not pending"):
        contract.verify_milestone("proj-1", 0)

    assert contract.credit_of(direct_alice) == PAYOUT


def test_all_milestones_verified_completes_project(direct_vm, direct_deploy, direct_alice):
    """Verifying the last milestone completes the project and totals the credits."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _donate(direct_vm, contract, direct_alice, "proj-1", 2 * 10**18)

    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 0)
    _verify(direct_vm, contract, direct_alice, "proj-1", 0, completed=True)
    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 1)
    _verify(direct_vm, contract, direct_alice, "proj-1", 1, completed=True)

    info = contract.get_project("proj-1")
    assert info["status"] == "completed"
    assert info["milestone_status"] == ["done", "done"]
    assert info["released_atto"] == 2 * PAYOUT
    assert contract.credit_of(direct_alice) == 2 * PAYOUT


def test_cancel_after_release_reverts(direct_vm, direct_deploy, direct_alice):
    """A project with released funds can no longer be cancelled."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 0)
    _verify(direct_vm, contract, direct_alice, "proj-1", 0, completed=True)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("cannot be cancelled"):
        contract.cancel_project("proj-1")

    assert contract.get_project("proj-1")["status"] == "active"


def test_cancel_then_claim_refund_second_project(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Cancelling an unreleased project lets donors claim refunds exactly once."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _create_project(direct_vm, contract, direct_alice, pid="proj-2")
    _donate(direct_vm, contract, direct_bob, "proj-2", 3 * 10**18)

    direct_vm.sender = direct_alice
    contract.cancel_project("proj-2")
    assert contract.get_project("proj-2")["status"] == "cancelled"

    direct_vm.sender = direct_bob
    contract.claim_refund("proj-2")
    assert contract.credit_of(direct_bob) == 3 * 10**18
    assert contract.my_donation("proj-2") == 0

    with direct_vm.expect_revert("No donation to refund"):
        contract.claim_refund("proj-2")


def test_donate_to_cancelled_project_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A cancelled project no longer accepts donations."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice, pid="proj-2")
    direct_vm.sender = direct_alice
    contract.cancel_project("proj-2")

    direct_vm.sender = direct_bob
    direct_vm.value = 10**18
    with direct_vm.expect_revert("not accepting donations"):
        contract.donate("proj-2")
    direct_vm.value = 0


def test_garbage_llm_output_raises_llm_error(direct_vm, direct_deploy, direct_alice):
    """LLM output without a completed field aborts verification with [LLM_ERROR]."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)
    _donate(direct_vm, contract, direct_alice, "proj-1", 8 * 10**18)
    _submit_evidence(direct_vm, contract, direct_alice, "proj-1", 0)

    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})
    direct_vm.mock_llm(LLM_REGEX, json.dumps({"weather": "sunny", "confidence": "high"}))

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.verify_milestone("proj-1", 0)

    info = contract.get_project("proj-1")
    assert info["milestone_status"][0] == "pending"
    assert info["released_atto"] == 0
    assert contract.credit_of(direct_alice) == 0


def test_non_creator_cannot_cancel_and_unknown_id_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Only the creator can cancel, and unknown project ids revert everywhere."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _create_project(direct_vm, contract, direct_alice)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the project creator"):
        contract.cancel_project("proj-1")
    with direct_vm.expect_revert("Unknown project id"):
        contract.donate("missing-project")

    assert contract.get_project("proj-1")["status"] == "active"
    assert contract.total_projects() == 1
