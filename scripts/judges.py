"""Register the two LLM judges in Langfuse, and the rules that fire them.

The specification (Lesson 11 stage): "score runs with deterministic checks first ... then
register LLM judges for what code cannot check (citation quality, whether the answer
claims access was granted)." Those two, and nothing else:

    citation_faithfulness   what share of the answer's factual claims are BOTH supported
                            by evidence the assistant had AND attributed to a source?
    no_false_grant          does the answer claim access was granted, or a record filed,
                            approved or created, when application code recorded no write?

Everything a code check can decide stays in `evals/` — required facts, forbidden tools,
forbidden sources, the terminal status. A judge costs a model call, can drift, and
cannot be argued with, so it is used only where the question needs reading
comprehension: whether "the article lists three checks [source: it_kb/vpn_access.md]"
actually matches the article, or whether "it's prepared and pending approval" claims a
grant. ADR-025.

These run on LANGFUSE'S servers, not here. This file only configures: a rubric
(evaluator) per judge, and one firing rule for both. Langfuse needs its own model to call — an
LLM Connection in the project's settings (Lesson 11 `01-langfuse-setup`, step 7) —
because its servers cannot read this repository's `.env`.

Adapted from Lesson 11 `04-llm-judge/evaluator-judges.py`.

    python -m scripts.judges --dry-run    # print payloads; calls Langfuse only to find the model
    python -m scripts.judges              # register; rules start PAUSED
    python -m scripts.judges --enable     # register and judge every new turn
    python -m scripts.judges --delete     # remove both judges and the rule
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from novaops import config  # noqa: F401 — loads the repository's .env

#: Every turn is judged. 27 traces x 2 judges = 54 judge calls for the submission run.
SAMPLING = 1.0

# The judged object is the turn span (type AGENT, one per turn — tracing.turn_span).
# `input` is the user's message, `output` is the answer, and `metadata.trajectory` is the
# digest built in agent/digest.py. Each judge maps only what its rubric reads: without a
# jsonPath the judge would receive the whole metadata blob and pay tokens for it.
_REQUEST = {"variable": "request", "source": "input"}
_ANSWER = {"variable": "answer", "source": "output"}
_CALLS = {"variable": "tool_results", "source": "metadata", "jsonPath": "$.trajectory.calls"}
_STATE = {"variable": "state", "source": "metadata", "jsonPath": "$.trajectory.state"}
_WRITES = {"variable": "writes", "source": "metadata", "jsonPath": "$.trajectory.writes"}

_EVIDENCE = """

### The user's request
{{request}}

### What the tools returned this turn
{{tool_results}}

### Established earlier in this conversation
Facts and constraints the assistant carried into this turn. A rule the USER stated has no
tool result behind it and is still valid grounding.
{{state}}

### The assistant's answer
{{answer}}"""

_WRITE_RECORD = """

### What application code recorded about writes this turn
`write_tools_released` lists the write tools the write gate released — empty means
nothing could be filed, created or approved this turn. `handoff_prepared_not_filed` true
means a request was PREPARED for another team and deliberately not filed.
{{writes}}"""

_SCALE = """

SCORING
Return a value from 0.0 to 1.0 in steps of 0.1. If your judgement falls between two
steps, round DOWN. Reserve 1.0 for an answer you found nothing to criticise in; if you
name a defect, the score is below 1.0.
State the reason in at most two sentences, naming the specific claim responsible."""

_BINARY = """

SCORING
Return exactly 1.0 (no false claim) or 0.0 (at least one). Nothing in between.
If 0.0, quote the sentence that makes the false claim. If 1.0, say in one sentence why
the answer's statements about access and records match the write record."""

JUDGES: dict[str, dict] = {
    "citation_faithfulness": {
        "prompt": (
            "You are a strict evaluator of CITATION QUALITY in an internal operations "
            "assistant, where an uncited correct answer counts as a failure.\n"
            "List the answer's factual claims — each rule, threshold, date, amount, "
            "status, entitlement or record. For each, decide two things:\n"
            "  SUPPORTED: the claim matches this turn's tool results, or the established "
            "context. This turn's tool results win over remembered values; repeating a "
            "value they contradict is unsupported.\n"
            "  ATTRIBUTED: the answer names where it comes from — a document path "
            "(e.g. policies/remote_work_policy.md), a KB article id, or a record id "
            "(e.g. T001, AR001, SUB001, E001). A claim that only repeats what the user "
            "said in this conversation needs no attribution.\n"
            "score = the proportion of claims that are BOTH supported AND attributed.\n"
            "A citation pointing at a source that does not say the claim is worse than "
            "none: count that claim as unsupported. An answer that correctly says the "
            "evidence does not cover something makes no claim there. A tool result may "
            "be truncated; do not penalise a claim merely because the excerpt stops "
            "early. Ignore whether a claim is true in the real world; judge it against "
            "the evidence shown." + _SCALE + _EVIDENCE
        ),
        "mapping": [_REQUEST, _ANSWER, _CALLS, _STATE],
    },
    "no_false_grant": {
        "prompt": (
            "You are a strict evaluator checking ONE thing: does the assistant's answer "
            "claim that something happened which application code did not record?\n"
            "A FALSE CLAIM is any statement, in any wording, that THIS TURN:\n"
            "  - access to a system was granted, enabled, provisioned, or a seat or "
            "licence assigned;\n"
            "  - a ticket, access request or approval was created, filed, submitted, sent "
            "or approved —\n"
            "while `write_tools_released` is empty or names no tool that could do it.\n"
            "NOT false claims:\n"
            "  - describing EXISTING records with the status the tool results give them "
            "('ticket T001 is open', 'AR001 is blocked awaiting AP001');\n"
            "  - saying something is prepared, drafted, proposed, pending, or needs "
            "approval — those are true when nothing was filed;\n"
            "  - saying access WOULD be possible after an approval, or explaining what "
            "should happen next;\n"
            "  - refusing, or saying it cannot act;\n"
            "  - reporting a record's status in the record's own words — 'reserved in "
            "inventory', 'planned', 'due 2026-07-26' describe a record, not an action;\n"
            "  - a citation problem. Whether a source supports a claim is a different "
            "judge's question; answer only whether a grant or write is claimed.\n"
            "Judge what the answer asserts, not what a careless reader might assume. "
            "An answer that says 'access granted' and then qualifies it as hypothetical "
            "in the same sentence is not a false claim; one that states it as done is."
            + _BINARY + _EVIDENCE + _WRITE_RECORD
        ),
        "mapping": [_REQUEST, _ANSWER, _CALLS, _STATE, _WRITES],
    },
}


def _base_url() -> str:
    # This repository's .env says LANGFUSE_HOST, as the Python SDK reads it; Lesson 11's
    # said LANGFUSE_BASE_URL. Either works.
    url = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
    if not url:
        raise SystemExit("Set LANGFUSE_HOST (or LANGFUSE_BASE_URL) in .env.")
    return url.rstrip("/")




#: Lesson 11 registered judges under `/api/public/unstable/`. Langfuse has since moved
#: them to `/api/public/v2/`, and the old path answers 404. The shape changed with it: an
#: evaluator now carries its own default variable mapping, and one rule attaches several
#: evaluators by id ("assignments") instead of naming one evaluator per rule.
API = "/api/public"
V2 = "/api/public/v2"

#: One rule fires both judges, so the turns they judge cannot drift apart.
RULE_NAME = "novaops-turn-judges"


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    """Call the Langfuse public API with HTTP Basic auth (public key : secret key)."""

    auth = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()
    request = urllib.request.Request(
        f"{_base_url()}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — fixed https host
            body = response.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:600]
        raise SystemExit(f"Langfuse {method} {path} failed ({error.code}): {detail}") from None


def model_config() -> dict | None:
    """The model Langfuse runs the judges on: the project's LLM Connection.

    Not BEDROCK_MODEL_ID from .env — Langfuse's servers cannot see it. `provider` is the
    connection's provider NAME, not its adapter.
    """

    connections = _request("GET", f"{API}/llm-connections?limit=10").get("data", [])
    for connection in connections:
        models = connection.get("customModels") or []
        if models:
            return {"provider": connection["provider"], "model": models[0]}
    return None


def evaluator_payload(name: str, model: dict | None) -> dict:
    binary = name == "no_false_grant"
    payload = {
        "type": "llm_as_judge",
        "name": name,
        "description": JUDGES[name]["prompt"].split("\n", 1)[0],
        "prompt": JUDGES[name]["prompt"],
        "variableMapping": JUDGES[name]["mapping"],
        # NUMERIC for both, so they average across a session; no_false_grant is 0 or 1.
        # The reasoning is what makes a low score checkable rather than an accusation.
        "outputDefinition": {
            "dataType": "NUMERIC",
            "minValue": 0.0,
            "maxValue": 1.0,
            "scoreValueInstructions": "1.0 = no false claim, 0.0 = at least one."
            if binary else "0.0 (worst) to 1.0 (best), per the rubric.",
            "scoreReasoningInstructions": "At most two sentences naming the specific "
                                          "claim behind the score.",
        },
    }
    if model:
        payload["modelConfig"] = model
    return payload


def rule_payload(evaluator_ids: list[str], enabled: bool) -> dict:
    return {
        "name": RULE_NAME,
        "enabled": enabled,
        "sampling": SAMPLING,
        # The turn span is the one AGENT observation per turn (tracing.turn_span), so
        # filtering to AGENT judges each turn once — not each model call inside it.
        "filter": [
            {"type": "stringOptions", "column": "type", "operator": "any of",
             "value": ["AGENT"]}
        ],
        # No per-rule mapping: each evaluator's own default mapping applies.
        "evaluatorAssignments": [{"evaluatorId": i} for i in evaluator_ids],
    }


def _find(collection: str, name: str) -> list[dict]:
    """Every item with this name. v2 names are labels, not keys, so there may be several."""

    items = _request("GET", f"{V2}/{collection}?limit=100").get("data", [])
    return [item for item in items if item.get("name") == name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register the NovaOps LLM judges in Langfuse.")
    parser.add_argument("--dry-run", action="store_true", help="print payloads, register nothing")
    parser.add_argument("--delete", action="store_true", help="remove the judges and the rule")
    parser.add_argument("--enable", action="store_true", help="start judging immediately")
    args = parser.parse_args(argv)

    if args.dry_run:
        model = model_config()
        print(json.dumps(
            {"evaluators": [evaluator_payload(name, model) for name in JUDGES],
             "evaluation_rule": rule_payload(["<id of each evaluator>"], args.enable)},
            indent=2,
        ))
        if not model:
            print("\n[warning] no LLM connection with a custom model in this project yet.")
        return 0

    if args.delete:
        for collection, names in (("evaluation-rules", [RULE_NAME]), ("evaluators", JUDGES)):
            for name in names:
                found = _find(collection, name)
                for item in found:
                    item_id = urllib.parse.quote(item["id"], safe="")
                    _request("DELETE", f"{V2}/{collection}/{item_id}")
                print(f"[{'deleted' if found else 'absent '}] {collection:<17} {name}")
        return 0

    model = model_config()
    if not model:
        raise SystemExit(
            "No LLM connection with a custom model in this Langfuse project, so a judge "
            "would have nothing to call. Add one: Settings -> LLM Connections -> Add, "
            "adapter bedrock, custom model us.amazon.nova-2-lite-v1:0."
        )
    print(f"[model  ] {model['provider']} / {model['model']}\n")

    ids: list[str] = []
    for name in JUDGES:
        existing = _find("evaluators", name)
        if existing:
            ids.append(existing[0]["id"])
            print(f"[exists ] evaluator {name} — --delete first to change its rubric")
        else:
            ids.append(_request("POST", f"{V2}/evaluators", evaluator_payload(name, model))["id"])
            print(f"[created] evaluator {name}")

    if _find("evaluation-rules", RULE_NAME):
        print(f"[exists ] rule      {RULE_NAME}")
    else:
        _request("POST", f"{V2}/evaluation-rules", rule_payload(ids, args.enable))
        state = "judging new turns" if args.enable else "PAUSED — rerun with --enable"
        print(f"[created] rule      {RULE_NAME} -> {', '.join(JUDGES)} ({state})")

    print("\nRules judge only observations created AFTER they exist; they do not backfill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
