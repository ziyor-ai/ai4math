"""Node scope: one classifier for proof obligations and exploration candidates.

Batch 8.0 slice 4 (frozen baseline §4.5b).

Two different relations must never be conflated:

  proof cone        closure of ``statement_dependencies + proof_dependencies`` from the goal;
                    only these nodes may carry proof routes and count as proof debt.
  research cone     nodes a researcher *explicitly registered* as exploration candidates,
                    plus one explicit hop through ``proposed_dependency``. Adding a
                    ``research_relevance`` note never grants dispatch rights by itself,
                    and the closure is not recursive.

Everything that answers "is this node workable / counted where" goes through
``node_scope`` so route admission, debt and the health panel cannot disagree.
"""
from .store import ResearchError, now

KINDS = ("proof", "exploration", "none")
DEFAULT_REGISTRATION_CAP = 8


def proof_cone(w):
    goal = w.store.get("project", "main")["goal"]
    seen = set()

    def visit(key):
        if key is None or key in seen:
            return
        seen.add(key)
        node = w.store.get("node", key)
        for dep in node["statement_dependencies"] + node["proof_dependencies"]:
            visit(dep)

    visit(goal)
    return seen


def registered(w):
    return {row["node"]: row for row in w.store.all("research_node")}


def research_cone(w):
    base = registered(w)
    allowed = set(base)
    for node_id in list(base):
        node = w.store.get("node", node_id)
        for dep in node.get("proposed_dependencies") or []:
            if w.store.exists("node", dep):
                allowed.add(dep)
    return allowed


def node_scope(w, node_id):
    if node_id in proof_cone(w):
        return "proof"
    if node_id in research_cone(w):
        return "exploration"
    return "none"


def verified_bridge(w, parent, dep):
    """True when a verified inference already links `dep` into `parent`'s proof.

    The `inference` record is introduced by the mathematics batch; until then this
    returns False for every exploratory dependency, which is the conservative default.
    """
    for row in w.store.all("inference"):
        if row.get("parent") != parent or row.get("dep") != dep:
            continue
        if (row.get("verification") or {}).get("status") == "verified":
            return True
    return False


def propose(w, p, actor):
    """Record candidate dependencies without touching the formal proof plan."""
    if not isinstance(p, dict):
        raise ResearchError("node.propose needs an object payload")
    node_id = p.get("node")
    deps = p.get("proposed_dependencies")
    if not isinstance(deps, list) or not deps or any(not isinstance(d, str) or not d for d in deps):
        raise ResearchError("node.propose needs a nonempty proposed_dependencies list")
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("node.propose needs a reason")
    node = w.store.get("node", node_id)
    for dep in deps:
        w.store.get("node", dep)
        if dep == node_id:
            raise ResearchError("a node cannot propose itself as a dependency")
    if node_id in proof_cone(w):
        for dep in deps:
            if dep in proof_cone(w):
                raise ResearchError("both nodes are already in the proof cone; use node.plan")
    kept = list(node.get("proposed_dependencies") or [])
    node["proposed_dependencies"] = sorted(set(kept + deps))
    node["propose_reason"] = reason.strip()
    w.store.put("node", node_id, node)
    return node


def registration_limit(w):
    from .policy import current
    budget = current(w).get("exploration_budget") or {}
    value = budget.get("registrations", DEFAULT_REGISTRATION_CAP)
    if type(value) is not int or value < 0:
        raise ResearchError("exploration_budget.registrations must be a non-negative integer")
    return value


def register(w, p, actor):
    """Explicitly admit one node as an exploration candidate (bounded)."""
    if not isinstance(p, dict):
        raise ResearchError("research.register needs an object payload")
    node_id = p.get("node")
    rationale = p.get("rationale")
    if not isinstance(node_id, str) or not node_id.strip():
        raise ResearchError("research.register needs a node id")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ResearchError("research.register needs a rationale")
    w.store.get("node", node_id)
    if node_id in proof_cone(w):
        raise ResearchError("node is already a proof obligation; register only outside the proof cone")
    if w.store.exists("research_node", node_id):
        raise ResearchError("node is already registered: " + node_id)
    rows = w.store.all("research_node")
    limit = registration_limit(w)
    if len(rows) >= limit:
        raise ResearchError("exploration registration budget is exhausted (" + str(limit) +
                            "); settle or retire a candidate first")
    record = {"id": node_id, "node": node_id, "rationale": rationale,
              "budget": p.get("budget") or {}, "registered_by": actor, "registered_at": now()}
    w.store.put("research_node", node_id, record, new=True)
    return record


def debt(w):
    """Proof debt only: open nodes that sit in the goal's proof cone."""
    goal = w.store.get("project", "main")["goal"]
    cone = proof_cone(w)
    open_nodes = [n for n in w.store.all("node")
                  if n["id"] != goal and n["id"] in cone and w.node_status(n["id"]) == "open"]
    return {"open": len(open_nodes), "nodes": sorted(n["id"] for n in open_nodes)}


def rounds_limit(w):
    from .policy import current
    budget = current(w).get("exploration_budget") or {}
    value = budget.get("rounds", 8)
    if type(value) is not int or value < 0:
        raise ResearchError("exploration_budget.rounds must be a non-negative integer")
    return value


def exploration(w):
    """Exploration view: registered candidates plus settled exploration rounds."""
    rows = registered(w)
    open_nodes = sorted(node for node in rows if w.node_status(node) == "open")
    routes = {r["id"]: r for r in w.store.all("route")}
    settled = 0
    for row in w.store.all("round_settled"):
        route = routes.get(row.get("route"))
        if route is not None and route.get("kind") == "exploration":
            settled += 1
    return {"registered": len(rows), "open": len(open_nodes), "nodes": open_nodes,
            "limit": registration_limit(w),
            "rounds_settled": settled, "rounds_limit": rounds_limit(w)}


HANDLERS = {"research.register": register, "node.propose": propose}
