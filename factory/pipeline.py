"""Resolve pipeline configuration and validate its agent/report contracts (stdlib only)."""

import copy
import re
from pathlib import PurePosixPath

TERMINAL = {"approved", "merged", "needs-human"}
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
PROVIDER_DEFAULTS = {
    "claude": {"model": "opus", "effort": "medium"},
    "codex": {"model": "gpt-6.1-sol", "effort": "medium"},
}
MODELS = {"claude": {"opus", "sonnet", "haiku", "fable"},
          "codex": {"gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra"}}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT = [
    {"id": "spec", "uses": "spec-writer"},
    {"id": "build", "uses": "builder"},
    {"id": "review", "parallel": ["reviewer-security", "reviewer-ux", "reviewer-ui", "reviewer-code"], "onChanges": "build"},
    {"id": "approve", "uses": "approver"},
]
for _step in DEFAULT:
    if "parallel" in _step:
        _step["parallel"] = [{"id": role, "uses": role, "providers": copy.deepcopy(PROVIDER_DEFAULTS)}
                             for role in _step["parallel"]]
    else:
        _step["providers"] = copy.deepcopy(PROVIDER_DEFAULTS)


class PipelineError(ValueError):
    pass


def relative_file(value, label):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PipelineError(f"{label} must be a relative file path using forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {".", "..", ".git"} for p in value.split("/")) or "" in value.split("/"):
        raise PipelineError(f"unsafe {label}: {value}")
    return value


def agent_file(root, provider, name):
    if provider not in PROVIDER_DEFAULTS:
        raise PipelineError(f"unknown provider: {provider}")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise PipelineError(f"invalid agent name: {name}")
    relative = f".{provider}/agents/{name}.md"
    path = inside(root, relative)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise PipelineError(f"cannot read instructions for {name}: {path}: {exc}") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise PipelineError(f"{path}: agent needs frontmatter with type and output")
    end = lines.index("---", 1)
    metadata = {}
    for line in lines[1:end]:
        match = re.match(r"^(type|output|contract):\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if key in metadata:
            raise PipelineError(f"{path}: duplicate {key}")
        # Factory metadata uses plain or quoted single-line scalar values.
        if value.startswith(('\"', "'")):
            quote = value[0]
            closing = value.find(quote, 1)
            if closing == -1 or (value[closing + 1:].strip() and not value[closing + 1:].lstrip().startswith("#")):
                raise PipelineError(f"{path}: invalid {key} scalar")
            value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        metadata[key] = value
    kind, output = metadata.get("type"), metadata.get("output")
    if kind not in {"task", "review", "approval"}:
        raise PipelineError(f"{path}: type must be task, review or approval")
    relative_file(output, f"{name} output")
    if not output.endswith(".md") or "/" in output:
        raise PipelineError(f"{path}: output must be a Markdown filename")
    if output.lower() == "request.md" or output.lower().startswith("rework-"):
        raise PipelineError(f"{path}: reserved report filename: {output}")
    contract = metadata.get("contract", {"task": "status", "review": "verdict", "approval": "decision"}[kind])
    if contract != {"task": "status", "review": "verdict", "approval": "decision"}[kind] and not (kind == "task" and contract == "spec"):
        raise PipelineError(f"{path}: invalid contract for {kind}: {contract}")
    if not "\n".join(lines[end + 1:]).strip():
        raise PipelineError(f"{path}: agent instructions are empty")
    return {"type": kind, "output": output, "contract": contract, "instructions": relative}


def resolve(raw=None, *, root, provider):
    """Resolve role names from the selected provider's Markdown files."""
    raw = copy.deepcopy(DEFAULT if raw is None else raw)
    registry = {}
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise PipelineError("pipeline must contain 1 to 32 ordered steps")

    def execution(entry):
        if not isinstance(entry, dict) or set(entry) - {"id", "uses", "providers", "label", "onChanges"}:
            raise PipelineError("each execution needs id, uses and providers")
        role = entry.get("uses")
        if not isinstance(role, str) or not NAME.fullmatch(role):
            raise PipelineError(f"invalid agent name: {role}")
        if role not in registry:
            agent = agent_file(root, provider, role)
            if agent["output"].lower() in {a["output"].lower() for a in registry.values()}:
                raise PipelineError(f"duplicate report filename: {agent['output']}")
            registry[role] = agent
        eid = entry.get("id")
        if not isinstance(eid, str) or not NAME.fullmatch(eid):
            raise PipelineError(f"invalid execution id: {eid}")
        if "providers" not in entry:
            raise PipelineError(f"{eid}: providers with explicit model and effort are required")
        settings = entry["providers"]
        if not isinstance(settings, dict) or not settings or set(settings) - set(PROVIDER_DEFAULTS):
            raise PipelineError(f"{eid}: providers must contain claude and/or codex settings")
        for settings_provider, selection in settings.items():
            if not isinstance(selection, dict) or set(selection) != {"model", "effort"}:
                raise PipelineError(f"{eid}: {settings_provider} needs explicit model and effort")
            model, effort = selection["model"], selection["effort"]
            if not isinstance(model, str) or model not in MODELS[settings_provider]:
                raise PipelineError(f"{eid}: invalid {settings_provider} model: {model}")
            if not isinstance(effort, str) or effort not in EFFORTS:
                raise PipelineError(f"{eid}: invalid {settings_provider} effort: {effort}")
        return {"id": eid, "uses": role, "providers": copy.deepcopy(settings)}

    resolved, ids = [], set()
    for step in raw:
        if not isinstance(step, dict) or set(step) - {"id", "uses", "parallel", "onChanges", "label", "providers"}:
            raise PipelineError("each pipeline step needs id and uses or parallel")
        name = step.get("id")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in ids or name in TERMINAL:
            raise PipelineError(f"duplicate, reserved or invalid step id: {name}")
        ids.add(name)
        if ("uses" in step) == ("parallel" in step):
            raise PipelineError(f"{name} needs exactly one of uses or parallel")
        if "parallel" in step:
            if "providers" in step:
                raise PipelineError(f"{name}: put providers on each parallel reviewer")
            entries = step["parallel"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
                raise PipelineError(f"{name}: invalid parallel group")
            if any(not isinstance(a, dict) or set(a) - {"id", "uses", "providers"} for a in entries):
                raise PipelineError(f"{name}: reviewers need id, uses and providers")
            step["parallel"] = entries
        else:
            entries = [step]
        executions = [execution(entry) for entry in entries]
        if len({e["id"] for e in executions}) != len(executions):
            raise PipelineError(f"{name}: duplicate execution id")
        agents = [e["uses"] for e in executions]
        if len(set(agents)) != len(agents):
            raise PipelineError(f"{name} repeats an agent")
        types = {registry[a]["type"] for a in agents}
        if len(types) != 1 or ("parallel" in step and types != {"review"}):
            raise PipelineError(f"{name}: parallel groups must contain only reviewers")
        label = step.get("label", name.replace("-", " ").title())
        if not isinstance(label, str) or not 1 <= len(label) <= 80:
            raise PipelineError(f"invalid label for {name}")
        kind = types.pop()
        if "onChanges" in step:
            targets = {s["id"] for s in resolved if s["type"] == "task"}
            if kind != "review" or not isinstance(step["onChanges"], str) or step["onChanges"] not in targets:
                raise PipelineError(f"{name}: onChanges must reference an earlier task step")
        resolved.append({"id": name, "label": label, "type": kind, "agents": agents, "executions": executions,
                         **({"onChanges": step["onChanges"]} if "onChanges" in step else {})})
    if resolved[-1]["type"] != "approval" or any(s["type"] == "approval" for s in resolved[:-1]):
        raise PipelineError("the pipeline must end with exactly one approval step")
    return raw, resolved, registry


def snapshot(cfg, root, provider):
    _, steps, registry = resolve(cfg.get("pipeline"), root=root, provider=provider)
    used = {a for s in steps for a in s["agents"]}
    return {"pipeline": steps, "pipelineAgents": {a: registry[a] for a in used},
            "maxRounds": cfg["maxRounds"], "mergePolicy": cfg["merge"],
            "results": {}, "attempts": {}, "runs": [], "resumes": 0}


def active(job):
    return any(s["id"] == job["stage"] for s in job.get("pipeline", []))


def report_path(folder, step, agent, run):
    name = agent["output"]
    relative = f"round-{run['round']}/{name}" if step["type"] == "review" else name
    return inside(folder, relative)


def verdict(agent, text):
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    contract = agent["contract"]
    if contract == "spec" and first.startswith("# "):
        return "READY"
    allowed = {"status": ("STATUS: READY", "STATUS: BLOCKED"),
               "verdict": ("VERDICT: PASS", "VERDICT: CHANGES"),
               "decision": ("APPROVE", "ESCALATE")}
    if first in allowed.get(contract, ()):
        return first.split(": ")[-1]
    raise PipelineError(f"invalid first line for {agent['output']}: {first!r}")


def inside(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise PipelineError(f"path escapes its folder: {relative}")
    return path
