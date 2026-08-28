# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

MILESTONE_PENDING = "pending"
MILESTONE_DONE = "done"

MAX_PAGE_CHARS = 4000


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _coerce_bool(raw) -> bool:
	if isinstance(raw, bool):
		return raw
	s = str(raw).strip().lower()
	if s in ("true", "1", "yes"):
		return True
	if s in ("false", "0", "no"):
		return False
	raise gl.vm.UserError(f"{ERROR_LLM} non-boolean completed field in LLM output")


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Project:
	creator: Address
	name: str
	description: str
	milestones: DynArray[str]
	milestone_status: DynArray[str]
	payout_per_milestone_atto: u256
	raised_atto: u256
	released_atto: u256
	status: str


class CharityMilestoneVault(gl.Contract):
	owner_addr: Address
	approved_projects: TreeMap[str, bool]
	projects: TreeMap[str, Project]
	donations: TreeMap[str, u256]
	donors: TreeMap[str, DynArray[str]]
	evidence: TreeMap[str, str]
	project_ids: DynArray[str]
	credits: TreeMap[Address, u256]

	def __init__(self) -> None:
		self.owner_addr = gl.message.sender_address

	def _get_project(self, pid: str) -> Project:
		project = self.projects.get(pid)
		if project is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown project id")
		return project

	def _credit(self, who: Address, amount: u256) -> None:
		self.credits[who] = self.credits.get(who, u256(0)) + amount

	@gl.public.write
	def create_project(
		self,
		pid: str,
		name: str,
		description: str,
		milestones: DynArray[str],
		payout_per_milestone_atto: u256,
	) -> None:
		if len(milestones) == 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} At least one milestone is required")
		if payout_per_milestone_atto == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Payout per milestone must be positive")
		if pid in self.projects:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Project id already exists")
		milestones_list = list(milestones)
		# New projects start UNVERIFIED: donations and evidence unlock only
		# after the platform owner vets the organization.
		self.approved_projects[pid] = False
		self.projects[pid] = Project(
			creator=gl.message.sender_address,
			name=name,
			description=description,
			milestones=milestones_list,
			milestone_status=[MILESTONE_PENDING] * len(milestones_list),
			payout_per_milestone_atto=u256(payout_per_milestone_atto),
			raised_atto=u256(0),
			released_atto=u256(0),
			status=STATUS_ACTIVE,
		)
		self.project_ids.append(pid)

	@gl.public.write
	def approve_project(self, pid: str) -> None:
		if gl.message.sender_address != self.owner_addr:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the platform owner may vet projects")
		self._get_project(pid)
		self.approved_projects[pid] = True

	@gl.public.view
	def is_project_approved(self, pid: str) -> bool:
		return self.approved_projects.get(str(pid), False)

	@gl.public.write.payable
	def donate(self, pid: str) -> None:
		project = self._get_project(pid)
		if project.status != STATUS_ACTIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Project is not accepting donations")
		if not self.approved_projects.get(str(pid), False):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Project is pending owner verification")
		if gl.message.value == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Send value with the call")
		donor_key = pid + ":" + str(gl.message.sender_address)
		previous = self.donations.get(donor_key, u256(0))
		self.donations[donor_key] = previous + u256(gl.message.value)
		if previous == u256(0):
			donor_addr = str(gl.message.sender_address)
			donor_list = self.donors.get(pid)
			if donor_list is None:
				self.donors[pid] = [donor_addr]
			else:
				donor_list.append(donor_addr)
		project.raised_atto = project.raised_atto + u256(gl.message.value)

	def _check_milestone_action(self, pid: str, idx_i: int) -> Project:
		project = self._get_project(pid)
		if str(gl.message.sender_address) != str(project.creator):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the project creator may manage milestones")
		if project.status != STATUS_ACTIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Project is not active")
		if idx_i >= len(project.milestones):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Milestone index out of range")
		if str(project.milestone_status[idx_i]) != MILESTONE_PENDING:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Milestone is not pending")
		return project

	@gl.public.write
	def submit_evidence(self, pid: str, idx: u256, evidence_url: str, narrative: str) -> None:
		if not self.approved_projects.get(str(pid), False):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Project is pending owner verification")
		idx_i = int(idx)
		project = self._check_milestone_action(pid, idx_i)
		url = str(evidence_url).strip()
		if not url.startswith("https://"):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Evidence URL must start with https://")
		self.evidence[pid + ":" + str(idx_i)] = url + "||" + str(narrative)

	@gl.public.write
	def verify_milestone(self, pid: str, idx: u256) -> None:
		idx_i = int(idx)
		project = self._check_milestone_action(pid, idx_i)
		payout = project.payout_per_milestone_atto
		if project.raised_atto < project.released_atto + payout:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Insufficient donations raised for milestone payout")
		evidence_payload = self.evidence.get(pid + ":" + str(idx_i))
		if evidence_payload is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} No evidence submitted for this milestone")
		milestone_text = str(project.milestones[idx_i])

		def leader_fn() -> dict:
			url, narrative = evidence_payload.split("||", 1)
			page_res = gl.nondet.web.get(url)
			status_code = int(page_res.status)
			if status_code >= 500:
				raise gl.vm.UserError(f"{ERROR_TRANSIENT} Evidence page returned HTTP {status_code}")
			if status_code >= 400:
				raise gl.vm.UserError(f"{ERROR_EXTERNAL} Evidence page returned HTTP {status_code}")
			try:
				page_text = page_res.body.decode("utf-8")[:MAX_PAGE_CHARS]
			except Exception:
				page_text = ""
			prompt = (
				"Verify charitable milestone completion.\n"
				f"MILESTONE: <m>{milestone_text}</m>\n"
				f"NARRATIVE: <n>{narrative}</n>\n"
				f"EVIDENCE PAGE: <e>{page_text}</e>\n"
				"Does the evidence show this milestone was completed? "
				'Reply JSON {"completed": true/false, "reasoning": "..."}'
			)
			analysis = gl.nondet.exec_prompt(prompt, response_format="json")
			parsed = _parse_llm_json(analysis)
			raw = None
			for key in ("completed", "is_completed", "done"):
				if key in parsed:
					raw = parsed[key]
					break
			if raw is None:
				raise gl.vm.UserError(f"{ERROR_LLM} missing completed field in LLM output")
			completed = _coerce_bool(raw)
			reasoning = parsed.get("reasoning", "")
			return {"completed": bool(completed), "reasoning": str(reasoning)}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			return bool(leader_data.get("completed")) == bool(fresh.get("completed"))

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		if bool(result["completed"]):
			project.milestone_status[idx_i] = MILESTONE_DONE
			project.released_atto = project.released_atto + payout
			self._credit(project.creator, payout)
			all_done = True
			for i in range(len(project.milestone_status)):
				if str(project.milestone_status[i]) != MILESTONE_DONE:
					all_done = False
					break
			if all_done:
				project.status = STATUS_COMPLETED

	@gl.public.write
	def cancel_project(self, pid: str) -> None:
		project = self._get_project(pid)
		if str(gl.message.sender_address) != str(project.creator):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the project creator may cancel a project")
		if project.status != STATUS_ACTIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only active projects can be cancelled")
		if project.released_atto != u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Projects with released funds cannot be cancelled")
		project.status = STATUS_CANCELLED

	@gl.public.write
	def claim_refund(self, pid: str) -> None:
		project = self._get_project(pid)
		if project.status != STATUS_CANCELLED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Refunds require a cancelled project")
		donation_key = pid + ":" + str(gl.message.sender_address)
		amount = self.donations.get(donation_key, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} No donation to refund")
		self.donations[donation_key] = u256(0)
		self._credit(gl.message.sender_address, amount)

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_project(self, pid: str) -> dict:
		project = self._get_project(pid)
		milestones_out = []
		for i in range(len(project.milestones)):
			milestones_out.append(str(project.milestones[i]))
		statuses_out = []
		for i in range(len(project.milestone_status)):
			statuses_out.append(str(project.milestone_status[i]))
		return {
			"creator": str(project.creator),
			"name": project.name,
			"description": project.description,
			"milestones": milestones_out,
			"milestone_status": statuses_out,
			"payout_per_milestone_atto": project.payout_per_milestone_atto,
			"raised_atto": project.raised_atto,
			"released_atto": project.released_atto,
			"status": project.status,
		}

	@gl.public.view
	def my_donation(self, pid: str) -> u256:
		donation_key = pid + ":" + str(gl.message.sender_address)
		return self.donations.get(donation_key, u256(0))

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_projects(self) -> u256:
		return u256(len(self.project_ids))

	@gl.public.view
	def owner(self) -> str:
		return str(self.owner_addr)
