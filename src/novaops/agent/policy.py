"""What the model is asked, and what it is allowed to see.

Lesson 10's `stage02_policy.py` and `stage03_policy.py`, adapted to the NovaOps tool
inventory. The lessons' split is kept: this file holds **policy** — prompts, the intent
vocabulary, the loadout table, the context projections — and `graph.py` holds the
mechanics. Change what the agent knows here; change how it runs there.

Two things carried over deliberately, because they are exactly what Maya's twelve-turn
session needs:

`TurnPlan.relevant_constraints` — a rule the user stated stays in the plan until they
withdraw it. Turn 9 of `S2` needs the Q3 spend freeze stated on turn 1.

`ConversationMemory` — five named fields rather than one summary string. A summary
blurs, and blurring is how "don't file anything until I say so" becomes "the user
discussed filing procedures".

One thing deliberately changed: `action_confirmed` still exists and still influences
which tools are *visible*, but it can no longer release a write. See `write_gate.py`
and ADR-006.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

KEEP_VERBATIM_TURNS = 2
DISTIL_AFTER_MESSAGES = 12
DISTIL_AFTER_TOKENS = 2_500

#: How much of each tool result the planner sees when deciding `requires_tools`.
#: Wider than the 400 characters the lessons use, and the reason is measurable: a policy
#: document runs past 1,000 characters, so a 400-character window shows the planner the
#: preamble and hides the clause the user is now asking about. It then decides the
#: answer is not in the conversation and pays for a second lookup of the same document.
#: This is planner context only — the answering model sees the full tool result.
PLANNER_TOOL_CHARS = 900


# 1. INTENT VOCABULARY -------------------------------------------------------

Intent = Literal[
    "policy_question",
    "benefits_question",
    "career_question",
    "employee_lookup",
    "onboarding_status",
    "equipment_request",
    "subscription_review",
    "access_request",
    "ticket_status",
    "other",
]

INTENT_GLOSS = (
    "Intent = the TOPIC of the newest message:\n"
    "  policy_question · what a policy, memo or IT knowledge-base article says\n"
    "  benefits_question · benefits, leave, perks, expenses\n"
    "  career_question · promotion, levelling, career paths, feedback, reviews\n"
    "  employee_lookup · who someone is (record, role, manager, status)\n"
    "  onboarding_status · a joiner's checklist, required systems, paperwork\n"
    "  equipment_request · hardware (laptops, monitors, docks, headsets)\n"
    "  subscription_review · a SaaS subscription's seats, cost or renewal\n"
    "  access_request · getting a person access to a system\n"
    "  ticket_status · existing IT tickets or access requests\n"
    "  other · none of the above\n"
    "Whether a lookup is needed is a SEPARATE field; a recap about onboarding is "
    "still onboarding_status."
)


# 2. TOOL VISIBILITY ---------------------------------------------------------
#
# Which read tools enter the model's context this turn. Every schema is sent on every
# call that has tools bound, so twenty schemas is a standing token bill you pay whether
# or not the turn needs one of them. Each row is a little wider than strictly necessary
# so a nearby classification error can still recover.
#
# No write tool appears here. Writes are released only by write_gate.py.

LOADOUTS: dict[str, list[str] | None] = {
    "policy_question": [
        "list_policies", "get_policy", "search_policies", "search_knowledge_base",
    ],
    "benefits_question": ["search_handbook", "search_policies", "get_policy"],
    "career_question": ["search_handbook", "search_policies"],
    "employee_lookup": ["get_employee", "list_direct_reports", "search_hr_documents"],
    # get_onboarding_checklist is listed first deliberately: it answers the whole
    # question from records in one call, already cited and with blockers named. The
    # narrower tools remain available for turns that genuinely want one fact.
    "onboarding_status": [
        "get_onboarding_checklist", "get_employee", "list_onboarding_tasks",
        "search_hr_documents", "check_asset_inventory", "check_software_subscription",
        "list_policies", "get_policy", "search_policies",
    ],
    "equipment_request": [
        "check_asset_inventory", "list_policies", "get_policy", "search_policies",
        "search_hr_documents",
    ],
    "subscription_review": [
        "check_software_subscription", "get_contract", "search_contracts",
        "list_seat_assignments", "search_policies",
    ],
    "access_request": [
        "get_employee", "check_software_subscription", "list_employee_tickets",
        "list_access_requests", "get_approval_status", "list_seat_assignments",
        "get_contract", "search_knowledge_base", "search_policies", "get_policy",
    ],
    "ticket_status": [
        "get_employee", "list_employee_tickets", "list_access_requests",
        "get_approval_status",
    ],
    "other": None,  # fail open for READS only: None means the whole read catalogue
}

#: Every tool that mutates state. Membership is decided here and nowhere else, so
#: "is this a write?" has one answer. `record_decision` is absent because it is not a
#: tool at all — see ADR-014.
WRITE_TOOLS = frozenset({"create_ticket", "create_access_request", "request_approval"})


def read_tools(catalogue: list[str]) -> list[str]:
    """Every tool in the catalogue that is not a write."""

    return sorted(name for name in catalogue if name not in WRITE_TOOLS)


# 3. THE TURN PLAN -----------------------------------------------------------

class TurnPlan(BaseModel):
    """The classify step's structured read of the newest message.

    This is the router. There is no second router above it — that would run the same
    classification twice.
    """

    current_intent: Intent = Field(description="Topic of the newest message.")
    relevant_facts: list[str] = Field(
        default_factory=list,
        description=(
            "Values from earlier turns this answer needs: ids, dates, numbers, tool "
            "results. The value itself, not a pointer to it."
        ),
    )
    relevant_constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Rules the user stated that are STILL in force — a spend freeze, an "
            "approval requirement, 'do not file anything until I say so'. Carry each "
            "one forward until the user withdraws it."
        ),
    )
    subject_employee: str | None = Field(
        default=None,
        description=(
            "Who the request is ABOUT, when that is someone other than the caller. An "
            "employee id where one is known — two employees share the first name Maya, "
            "so a name does not identify a person."
        ),
    )
    requires_tools: bool = Field(
        description="False only if every fact needed is already in this conversation."
    )
    action_confirmed: bool = Field(
        default=False,
        description=(
            "True only if THIS message authorises creating or filing a record "
            "('go ahead', 'file it'). Drafting, summarising or 'write it up' is "
            "composing text, NOT authorisation to file anything."
        ),
    )


PLANNER_PROMPT = (
    INTENT_GLOSS + "\n\n"
    "Plan the model context for the newest message of a NovaOps session.\n"
    "- Classify the newest message only; follow subject changes.\n"
    "- relevant_facts is the session's memory: carry a value forward if a later answer "
    "might need it. A missing fact breaks the answer; a spare one costs a few tokens.\n"
    "- relevant_constraints holds what the user declared, until they withdraw it.\n"
    "- Drop whatever the user resolved or abandoned. A topic the user closed must stop "
    "driving tool choice.\n"
    "- Users paste emails, threads and signatures. Plan for the request inside, not the "
    "boilerplate, and not for other people's items quoted in the thread.\n"
    "\n"
    "requires_tools decides whether this turn spends a lookup. Get it right in BOTH "
    "directions — a needless call costs latency and money, a missing one costs the "
    "answer.\n"
    "  The ONE test: could you QUOTE the answer from the text already written above? "
    "Not 'is the topic familiar' — could you quote it. If you cannot quote it, it is "
    "not here, and requires_tools is TRUE.\n"
    "  SESSION MEMORY IS NOT A SOURCE. The memory block is a compressed SNAPSHOT of "
    "earlier turns, not a document and not the current state of any record. It tells "
    "you what the session is about; it does not tell you what any rule says or what any "
    "record holds now. Treating a familiar-looking memory entry as the answer is the "
    "single most common way this field is set wrongly — when in doubt, TRUE.\n"
    "  FALSE only when the answer is verbatim above:\n"
    "    * a follow-up about the SAME passage you just retrieved — its text is here, so "
    "re-reading it adds nothing;\n"
    "    * a recap, a summary, or 'say that again more briefly';\n"
    "    * reasoning or arithmetic over numbers already quoted above;\n"
    "    * an INSTRUCTION TO ACT on something already established — 'go ahead', 'file "
    "it', 'do it now' — where the earlier turns already gathered who, what system and "
    "why. Preparing an action is not researching one; you are not being asked a "
    "question, so there is nothing to look up.\n"
    "  TRUE whenever anything must be looked up:\n"
    "    * a first turn — you know nothing about NovaOps otherwise;\n"
    "    * a DIFFERENT topic, which needs a different document. Having retrieved the "
    "equipment policy does not answer a spending-threshold question;\n"
    "    * the user REFERRING BACK to something they said is not the same as the answer "
    "being here. 'Given the freeze I mentioned, does this need Finance?' still needs the "
    "rule that sets the threshold — the user named the topic, not the number;\n"
    "    * any entitlement, rule, limit, threshold or amount you have not already seen "
    "quoted;\n"
    "    * anything asking what the state is NOW — what is still open, outstanding, "
    "pending or assigned — because records change and a snapshot from an earlier turn "
    "is not the current state.\n"
    "- action_confirmed only when this message says to go ahead."
)


class ConversationMemory(BaseModel):
    """What survives when the raw messages no longer fit.

    Named fields rather than a summary paragraph. The distinction is the whole point:
    a summariser given one output field will merge a standing constraint into narrative
    prose, and the constraint stops being enforceable.
    """

    current_intent: str = Field(description="What the session is about NOW.")
    important_facts: list[str] = Field(
        default_factory=list,
        description="Durable values later turns may need: ids, names, dates, numbers.",
    )
    active_constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Rules the user imposed that are STILL in force. Never drop one unless the "
            "user withdrew it."
        ),
    )
    decisions: list[str] = Field(
        default_factory=list,
        description="Conclusions reached and actions taken, including things closed.",
    )
    unresolved_items: list[str] = Field(
        default_factory=list,
        description="Open questions and outstanding work. These must survive.",
    )


DISTIL_PROMPT = (
    "You maintain the durable memory of a long NovaOps conversation.\n"
    "You are given the memory so far and the conversation since it was last updated. "
    "Return the UPDATED memory.\n\n"
    "Rules:\n"
    "- Carry every still-relevant item forward. This memory replaces the raw messages, "
    "so anything you omit is gone.\n"
    "- Keep concrete values (E001, 2026-08-01, 42 of 40 seats), not descriptions of them.\n"
    "- NEVER silently drop an active constraint. Move it to decisions only if the user "
    "explicitly withdrew or satisfied it.\n"
    "- Move a topic the user closed into decisions, and drop its raw detail.\n"
    "- unresolved_items must keep anything still outstanding.\n"
    "- Be compact. This is a working memory, not minutes."
)


def memory_is_safe(old: dict, new: ConversationMemory) -> bool:
    """Reject a distillation that is empty or that lost an active constraint.

    The safe failure mode is cost, not silence: a rejected memory leaves the raw history
    in place, so the next turn is more expensive rather than less correct.
    """

    if not (new.important_facts or new.decisions or new.unresolved_items):
        return False
    return len(new.active_constraints) >= len(old.get("active_constraints", []))


# 4. STATE -> MODEL CONTEXT --------------------------------------------------

def message_text(message) -> str:
    """Flatten Bedrock's string-or-content-block message format to plain text."""

    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    return str(content)


def recent_turns(messages: list, keep: int = 2) -> list:
    boundaries = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not boundaries:
        return list(messages)
    start = boundaries[-keep] if len(boundaries) >= keep else boundaries[0]
    return list(messages[start:])


def conversation_window(messages: list, keep: int = KEEP_VERBATIM_TURNS) -> list:
    """Keep recent prose while dropping tool traffic from older retained turns."""

    window = recent_turns(messages, keep)
    current = recent_turns(messages, 1)
    older = window[: len(window) - len(current)]
    kept = [
        m for m in older
        if not isinstance(m, ToolMessage) and not (getattr(m, "tool_calls", None) or [])
    ]
    return kept + current


def render_transcript(messages: list, tool_chars: int = 400) -> str:
    """Readable history for the planner, with bounded tool-result payloads."""

    lines = []
    for message in messages:
        role = message.__class__.__name__.replace("Message", "").lower()
        if role == "tool":
            body = message_text(message)
            if len(body) > tool_chars:
                body = body[:tool_chars] + f"... [+{len(body) - tool_chars} chars dropped]"
            lines.append(f"[tool result: {getattr(message, 'name', '?')}] {body}")
            continue
        text = message_text(message)
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            text = (text + " " if text else "") + " ".join(
                f"[called {c['name']}({json.dumps(c['args'])[:120]})]" for c in calls
            )
        lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def render_memory(memory: dict) -> str:
    if not memory:
        return "(nothing distilled yet)"
    model = ConversationMemory(**memory)

    def bullets(items: list[str]) -> str:
        return "\n".join(f"  - {item}" for item in items) if items else "  - (none)"

    # The labels are load-bearing. A block headed "Established facts" reads to the
    # planner as knowledge in hand, so it concludes the answer is already present and
    # skips the lookup — measured repeatedly on S2 turns 9 and 11. Saying so in a distant
    # prompt paragraph did not survive; the caveat has to travel with the data.
    return (
        f"Session is about: {model.current_intent}\n"
        f"SNAPSHOT from earlier turns — NOT a document, NOT the current state of any "
        f"record, and NOT a source you may cite. Anything here may be out of date:\n"
        f"{bullets(model.important_facts)}\n"
        f"Constraints the user imposed (still binding):\n"
        f"{bullets(model.active_constraints)}\n"
        f"Decisions made and topics closed:\n{bullets(model.decisions)}\n"
        f"Still open as of the snapshot:\n{bullets(model.unresolved_items)}"
    )


def planner_messages(state: dict) -> list:
    """Durable memory plus only the messages newer than it. This is what bounds cost."""

    covered = state.get("distilled_upto", 0)
    fresh = state["messages"][covered:-1]
    prompt = (
        f"Session memory:\n{render_memory(state.get('memory') or {})}\n\n"
        f"Conversation since then:\n"
        f"{render_transcript(fresh, tool_chars=PLANNER_TOOL_CHARS) or '(nothing new)'}\n\n"
        f"User's newest message:\n{message_text(state['messages'][-1])}"
    )
    return [SystemMessage(PLANNER_PROMPT), HumanMessage(prompt)]


def distiller_messages(state: dict) -> list:
    covered = state.get("distilled_upto", 0)
    fresh = state["messages"][covered:]
    prompt = (
        f"Memory so far:\n{render_memory(state.get('memory') or {})}\n\n"
        f"Conversation since then:\n{render_transcript(fresh)}"
    )
    return [SystemMessage(DISTIL_PROMPT), HumanMessage(prompt)]


def needs_distillation(state: dict) -> bool:
    """Distil when fresh history crosses either threshold. ~4 chars per token."""

    fresh = state["messages"][state.get("distilled_upto", 0):]
    rendered = render_transcript(fresh)
    return len(fresh) >= DISTIL_AFTER_MESSAGES or len(rendered) // 4 >= DISTIL_AFTER_TOKENS


ASSISTANT_PROMPT = (
    "You are the NovaOps internal assistant. Ground every answer in what the tools and "
    "the provided context actually say — never invent an employee, a seat count, a date "
    "or a policy rule.\n"
    "Work to these rules:\n"
    "- CITE EVERY CLAIM. Each tool result carries a '[source: ...]' line or a record id; "
    "name it. An uncited claim is treated as unsupported.\n"
    "- A SOURCE IS A DOCUMENT OR A RECORD. Never cite the user, the conversation, "
    "'established facts', 'user constraint' or your own memory as the source of a rule, "
    "threshold, entitlement, limit, amount or eligibility. The user naming a topic is "
    "not evidence about it: if they mention a spend freeze, the number that freeze sets "
    "is in a memo or a policy, and you must look it up and quote it. If you have a tool "
    "available and find yourself about to assert a rule without a document behind it, "
    "call the tool instead.\n"
    "- State the actual VALUE, not that a value exists: the threshold, the seat count, "
    "the date, the cost. 'Requires Finance approval' is half an answer; 'USD 18,400 "
    "exceeds the USD 7,500 threshold, so Finance must approve' is the answer.\n"
    "- NAME RECORDS BY ID AND STATUS, verbatim as the record gives them. 'Ticket T001 is "
    "open', not 'there is a ticket'. 'Access request AR001 is blocked', not 'the request "
    "is held up'. 'Pending approval', not 'waiting'. An id lets the reader go and look; a "
    "paraphrase does not, and a paraphrased status is how a blocked request comes to "
    "sound resolved.\n"
    "- Answer the user's CURRENT request. If they changed subject or closed a topic, do "
    "not keep working the old one.\n"
    "- A constraint the user states — a spending rule, an approval requirement, 'don't "
    "file anything until I say so' — stays in force for the rest of the conversation.\n"
    "- Facts established earlier are still true; reuse them instead of looking them up "
    "again.\n"
    "- NEWER OR MORE SPECIFIC GUIDANCE WINS. A memo can change a threshold a policy "
    "still states; compare dates and say which governs.\n"
    "- If the evidence does not answer the question, say so and say what is missing. "
    "Never substitute something that merely looks like the answer.\n"
    "- A correct operational answer is often 'no', with the governing evidence "
    "attached.\n"
    "- Two employees share the first name Maya. Identify people by id.\n"
    "- Users paste emails, tickets and signatures. Act on the request inside, not the "
    "boilerplate, and not on other people's items quoted in a thread.\n"
    "- A STATUS SUMMARY about a person is not a task list. Open with who they are, when "
    "they start and where they are based, then what is outstanding, then anything "
    "awaiting a decision and who owns it. A reader forwarding your summary needs the "
    "identifying facts more than the due dates.\n"
    "- Answer in at most six sentences; no preamble. A summary the user asked to forward "
    "may run longer, but every line must carry a fact."
)

#: Maya's authority clause. The write gate enforces her read-only property whatever the
#: prompt says — but a model that believes it can file things writes answers claiming it
#: did, and the answer text is half of what is scored.
MAYA_CLAUSE = (
    "\nYOUR AUTHORITY: you prepare actions; you do not file them. You have no tool that "
    "creates a ticket, an access request or an approval.\n"
    "When the user asks you to act, do NOT apologise and do NOT refuse. Hand the work "
    "over: state what you have prepared, name the system and the person it is for, name "
    "who must approve it, and report its state as PENDING that approval. A prepared "
    "request that is waiting on a human is a useful answer; 'I can't do that' is not.\n"
    "Never say or imply that you filed, created, submitted, granted or completed "
    "anything. 'Prepared and pending approval' is true; 'filed' is not."
)

#: Webex's authority clause. Note what it does not promise: having a write tool this
#: turn is not the same as access existing.
WEBEX_CLAUSE = (
    "\nYOUR AUTHORITY: you may file records, but only when a write tool is actually "
    "available to you this turn. If none is, a human approval is still outstanding — "
    "say so plainly. NEVER state that access was granted. Keep three things apart: what "
    "the records show, what you actually did, and what should happen next. Reuse an "
    "existing ticket or request rather than creating a second one."
)


#: Appended to the system prompt on a rearm. Without it a rearm changes nothing that
#: affects the model's decision — the loadout it already had was correct, and deleting
#: the answer leaves identical context, so the retry reproduces the same answer. The
#: retry has to differ in what the model is TOLD, not only in what it is offered.
REARM_INSTRUCTION = (
    "\n\nSTOP. You just answered this turn without calling a single tool, and this turn "
    "needs evidence you do not have. Something in the question — a rule, a threshold, an "
    "entitlement, a current record state — is not quotable from the conversation above. "
    "Call a tool NOW and answer from its result. Do not answer from memory, from the "
    "user's own words, or from a snapshot taken earlier in this conversation."
)


#: Appended when a turn has used its allowance of tool rounds. It matters that this
#: says what to do rather than only what is forbidden: a model told merely that it has
#: no tools tends to apologise, where what is wanted is the best answer the evidence
#: supports plus an honest statement of the gap.
BOUNDED_INSTRUCTION = (
    "\n\nYou have used this turn's allowance of lookups. Answer NOW from the evidence "
    "already gathered. If something you wanted is still missing, say plainly what it is "
    "and where it would be found — an answer that names its own gap is useful; another "
    "search is not available."
)


def answer_prompt(scope) -> str:
    """The system prompt text for the answering model, in this scope's authority."""

    return ASSISTANT_PROMPT + (WEBEX_CLAUSE if scope.writes_allowed else MAYA_CLAUSE)


def render_plan(plan: TurnPlan) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"  - {item}" for item in items) if items else "  - (none)"

    lines = [
        "\n\n--- CONTEXT FOR THIS TURN ---",
        f"User's current intent: {plan.current_intent}",
        f"Established facts you must use:\n{bullets(plan.relevant_facts)}",
        f"Constraints still in force:\n{bullets(plan.relevant_constraints)}",
    ]
    if plan.subject_employee:
        lines.append(f"This request is about: {plan.subject_employee}")
    lines.append("--- END CONTEXT ---")
    return "\n".join(lines)
