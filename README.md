# CharityMilestoneVault

Trustless philanthropy on GenLayer. A creator lists a charitable project with fixed-payout milestones; anyone can donate GEN into its pool. For each milestone the creator submits evidence (a URL plus a narrative), and AI validators check whether the evidence page shows the milestone was completed. Verified milestones release their fixed payout from the donation pool to the creator as a withdrawable credit; if every milestone is verified, the project completes. A creator may cancel an unreleased project so donors can claw their money back.

## Architecture

- **User action** — `create_project(pid, name, description, milestones, payout_per_milestone_atto)` lists a project with N milestones, each paying the same fixed amount; `donate(pid)` (payable) adds value to the pool; `submit_evidence(pid, idx, url, narrative)` attaches evidence to a pending milestone; `verify_milestone(pid, idx)` triggers resolution.
- **Evidence source** — the leader fetches the evidence URL via `gl.nondet.web.get(url)`; HTTP 4xx aborts with `[EXTERNAL]`, 5xx with `[TRANSIENT]`. The first 4000 characters of the page body (decoded from bytes) are embedded in the prompt.
- **Nondet call** — `gl.nondet.exec_prompt(..., response_format="json")` asks `{"completed": true/false, "reasoning": "..."}`; output is defensively parsed (`_parse_llm_json`), the decision key is accepted under the aliases `completed` / `is_completed` / `done`, coerced to a real boolean, and anything malformed reverts `[LLM_ERROR]`.
- **Equivalence principle** — custom validator reruns the whole leader function (fetch + prompt) and accepts only **exact agreement on the `completed` boolean** between leader and fresh rerun. Leader errors are reconciled through `_handle_leader_error` ([EXPECTED]/[EXTERNAL] must match exactly, [TRANSIENT] matches by class).
- **Settlement effect** — a completed milestone flips to `done`, moves `payout_per_milestone_atto` from the pool into the creator's credit balance, and a fully-done project flips to `completed`. `withdraw()` pays accumulated credits out; `cancel_project` (only while active and nothing released) lets donors `claim_refund` their donation back as a credit.
- **Appeal path** — none in this contract; GenLayer Optimistic Democracy provides leader-proposes / validator-check / appeal window natively around each nondet decision.

## Quickstart

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# lint
.venv/bin/genvm-lint check contracts/CharityMilestoneVault.py --json

# direct tests
.venv/bin/pytest tests/direct/ -v
```

## Interface

| Method | Type | Notes |
| --- | --- | --- |
| `create_project(pid, name, description, milestones, payout_per_milestone_atto)` | write | unique id, ≥1 milestone, payout > 0; statuses all `pending` |
| `donate(pid)` | write, payable | active projects only, `value > 0`; tracks per-donor amounts |
| `submit_evidence(pid, idx, evidence_url, narrative)` | write | creator only, active project, pending milestone |
| `verify_milestone(pid, idx)` | write | creator only; evidence must exist; AI verdict releases payout |
| `cancel_project(pid)` | write | creator only; active and zero released funds |
| `claim_refund(pid)` | write | cancelled project; zeroes the donor's stake and credits it |
| `withdraw()` | write | pays out accumulated credits to sender |
| `get_project(pid)` | view | full record; `creator` as string, milestones/statuses as lists |
| `my_donation(pid)` | view | caller's donation to a project |
| `credit_of(who)` | view | withdrawable credit balance |
| `total_projects()` | view | number of listed projects |
| `owner()` | view | deployer address as string |

Milestone statuses: `pending` / `done`. Project statuses: `active` / `completed` / `cancelled`.

> **StudioNet note:** gasless network — 0 GEN balances are fine for testing.
