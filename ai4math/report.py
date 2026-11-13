"""A human-readable route board and manuscript evidence ledger."""
from __future__ import annotations

import json


def markdown(workbench):
    board, audit = workbench.board(), workbench.audit()
    goal = board["project"]["goal"]
    status = workbench.node_status(goal) if goal else "unset"
    lines = [f"# {board['project']['title']}", "", f"Goal: `{goal}`. Evidence level: **{board['goal_evidence_level']}**. Workflow: {status}.", "",
             f"Integrity audit: {'passed' if audit['passed'] else 'FAILED'}; events: {audit['events']}; artifacts: {audit['artifacts']}.",
             f"Event tail SHA-256: `{audit['tail_hash']}`.", "",
             "An accepted claim retains its declared evidence level. Review and command completion do not constitute mathematical proof.", "",
             ]
    review = board["strategy_review"]
    if review["enabled"]:
        lines.extend(["## Global strategy review", "",
                      f"State: {'due — dispatch paused' if review['due'] else 'open'}. Rounds since review: {review['rounds_since_review']}/{review['round_interval']}.",
                      f"Brief SHA-256: `{review['brief_sha256']}`. Last decision: `{review['last_decision']}`.",
                      "Ready proof obligations: " + (", ".join(review["ready_obligations"]) or "none"), ""])
        for trigger in review["triggers"]:
            lines.append(f"- {trigger['kind']}: {trigger.get('reference', trigger.get('rounds'))}")
        lines.append("")
    lines.extend(["## Targets and proof obligations", ""])
    for node in board["nodes"]:
        lines.extend([f"### {node['id']}", "", node["statement"], "",
                      f"Domain: {node['domain']}. Scope: {node['quantifier']}. Evidence: {node['evidence_level']}. Workflow: {node['status']}.",
                      f"Reachable from goal: {node['reachable']}. Proof dependencies: {', '.join(node['proof_dependencies']) or 'none'}.",
                      f"Statement SHA-256: `{node['statement_digest']}`.", ""])
    lines.extend(["## Routes", ""])
    for route in board["routes"]:
        lines.extend([f"### {route['id']} — {route['status']}", "", route["rationale"], "",
                      f"Target: {route['node']}. Runs: {route['runs']}/{route['max_runs']}. Stagnation: {route['stagnant']}/{route['stagnation_limit']}.",
                      f"Next question: {route['next_question']}", "", f"Stop condition: {route['stop_condition']}", ""])
    lines.extend(["## Claims", ""])
    for c in board["claims"]:
        trust = "none"
        if c.get("validation"):
            v = workbench.store.get("validation", c["validation"])
            trust = f"{v['method']}; {v['trust']}; {v['trust_basis']}"
        lines.extend([f"- `{c['id']}`: **{c['evidence_level']}**, {c['status']}, scope={c['scope']}. {c['summary']}",
                      f"  Validation trust: {trust}."])
    lines.extend(["", "## Tool evidence", ""])
    for item in board["tool_evidence"]:
        lines.append(f"- `{item['id']}`: {item['tool_id']}; **{item['verification_status']}**; {item['evidence_level']}; finite scope; audit task `{item['audit_task']}`. Claim: {item['claim'] or 'none'}. No automatic acceptance.")
    lines.extend(["", "## Manuscript ledger", ""])
    for item in board["ledger"]:
        claim = workbench.store.get("claim", item["claim"])
        current = "usable" if claim["status"] == "accepted" else "BLOCKED by revoked claim"
        lines.extend([f"- {item['id']} [{item['use']}; {item['evidence_level']}; {item['scope']}; {current}]: {item['text']}",
                      f"  Node `{item['node']}`; claim `{item['claim']}`. Free prose still requires semantic review."])
    lines.extend(["", "## Mathematical method designs", ""])
    for method in board["methods"]:
        lines.extend([f"### {method['id']} — {method['status']}", "", method["proposed_tool"], "", method["mechanism"], "",
                      f"Proof obligations: {', '.join(method['obligations'])}.", f"Falsifier: {method['falsifier']}",
                      f"Minimal probe: {method['minimal_probe']}", f"Learning goal: {method['learning_goal']}",
                      "Method review schedules exploration only; mathematical evidence remains unknown.", ""])
    for review in board["method_reviews"]:
        lines.append(f"- Review `{review['id']}`: {review['verdict']}; kind={review['review_kind']}; same_author={review['same_author']}; scope=research_design_only.")
    for note in board["research_notes"]:
        lines.append(f"- Research note `{note['id']}` ({note['kind']}; {note['evidence_level']}): {note['summary']}")
    lines.extend(["", "## Academic mechanisms and exploration", ""])
    for card in board["mechanism_cards"]:
        lines.append(f"- Mechanism `{card['id']}`: {card['content']['name']}; source applicability **unknown**; {', '.join(card['content']['capabilities'])}.")
    for pool in board["exploration_pools"]:
        lines.append(f"- Pool `{pool['id']}` → `{pool['node']}`: {pool['question']}; task reservations {pool['tasks_reserved']}/{pool['max_tasks']}; no automatic acceptance or budget reset.")
    for candidate in board["exploration_candidates"]:
        lines.append(f"- Candidate `{candidate['id']}`: {candidate['capability']}; mechanism {candidate['mechanism_key']}; **unknown**; parents {', '.join(candidate['parents']) or 'none'}.")
    for item in board["exploration_evaluations"]:
        lines.append(f"- Candidate evaluation `{item['id']}`: finite scope; valid={item['valid']}; {item['evidence_level']}; metrics={json.dumps(item['metrics'], ensure_ascii=False)}; trust basis: {item['trust_basis']}. No mathematical claim accepted.")
        lines.append(f"  Evaluation protocol: `{item.get('evaluation_protocol_sha256', 'legacy-unranked')}`.")
    for item in board["exploration_transfers"]:
        lines.append(f"- Transfer `{item['id']}` → obligation `{item['obligation']}`. Source `{item['source']}`, {item['locator']}. Applicability **unknown** at registration. {item['mapping']}")
    for item in board["exploration_counterexamples"]:
        lines.append(f"- Counterexample `{item['id']}` → candidate `{item['candidate']}`. Case `{item['case_id']}`, **finite_support**. Minimality scope: {item['search_domain']}. Checked {item['smaller_cases_checked']} smaller cases.")
    for item in board["exploration_resolutions"]:
        status = workbench.store.get("claim", item["claim"])["status"]
        lines.append(f"- Resolution `{item['id']}`: transfer `{item['transfer']}` → witness `{item['counterexample']}` → revision `{item['candidate']}` → obligation `{item['obligation']}`. Claim `{item['claim']}` is {status} ({item['claim_scope']} scope).")
    for probe in board["exploration_probes"]:
        lines.append(f"- Probe `{probe['id']}`: {probe['question']}; predicted information/call={probe['predicted_information_per_call']}; hypothetical predictions, not observed truth.")
    for item in board["exploration_feedback"]:
        lines.append(f"- Obstacle `{item['id']}`: {item['obstacle']}; next change: {item['next_change']}; **unknown**; budget not reset.")
    lines.extend(["", "## Tasks", ""])
    for task in board["tasks"]:
        lines.append(f"- `{task['id']}` ({task['role']}): **{task['status']}**. Execution status only.")
    lines.extend(["", "## Next actions", ""])
    for action in board["next_actions"]:
        lines.append(f"- `{action['route']}` → `{action['node']}`: {action['question']}")
    if not board["next_actions"]:
        lines.append("No executable route. Review the goal, route budgets and stop conditions before creating new work.")
    if audit["errors"]:
        lines.extend(["", "## Integrity errors", "", *[f"- {error}" for error in audit["errors"]]])
    return "\n".join(lines) + "\n"
