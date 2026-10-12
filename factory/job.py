"""Read and update a factory job's job.json. The orchestrator is its only caller.

  python factory/job.py new <job-id> --feature "<text>" --worktree <path>
  python factory/job.py set <job-id> key=value [key=value ...]
  python factory/job.py show <job-id>

Keys for set: stage, round, resumes, builder, feature, reviews.<lens>, heads.<round>,
and reviews=pending to reset all four. A change of stage appends a history entry
(with the round on review entries). Every write sets updated and replaces the
file in one step, so the dashboard never reads half of it.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

JOBS = Path(__file__).resolve().parent / "jobs"
STAGES = {"spec", "build", "review", "approve", "merged", "needs-human"}
LENSES = ("security", "ux", "ui", "code")
VERDICTS = {"pending", "PASS", "CHANGES"}
INTS = {"round", "resumes"}
STRINGS = {"stage", "builder", "feature"}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_of(job_id):
    return JOBS / job_id / "job.json"


def load(job_id):
    path = path_of(job_id)
    if not path.exists():
        sys.exit(f"no job.json for {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(job):
    job["updated"] = now()
    path = path_of(job["id"])
    tmp = path.with_name("job.json.tmp")
    tmp.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new(args):
    if not (JOBS / args.job_id).is_dir():
        sys.exit(f"claim the job first: mkdir {JOBS / args.job_id}")
    if path_of(args.job_id).exists():
        sys.exit(f"{args.job_id} already has a job.json")
    at = now()
    save({
        "id": args.job_id,
        "feature": args.feature,
        "branch": f"factory/{args.job_id}",
        "worktree": args.worktree.replace("\\", "/"),
        "stage": "spec",
        "round": 0,
        "resumes": 0,
        "reviews": {k: "pending" for k in LENSES},
        "heads": {},
        "created": at,
        "history": [{"stage": "spec", "at": at}],
    })


def apply(job, key, value):
    if key == "reviews":
        if value != "pending":
            sys.exit("reviews=pending is the only whole-map value")
        job["reviews"] = {k: "pending" for k in LENSES}
    elif key.startswith("reviews."):
        lens = key.split(".", 1)[1]
        if lens not in LENSES or value not in VERDICTS:
            sys.exit(f"bad review: {key}={value}")
        job.setdefault("reviews", {})[lens] = value
    elif key.startswith("heads."):
        job.setdefault("heads", {})[key.split(".", 1)[1]] = value
    elif key in INTS:
        job[key] = int(value)
    elif key in STRINGS:
        if key == "stage" and value not in STAGES:
            sys.exit(f"unknown stage: {value}")
        job[key] = value
    else:
        sys.exit(f"unknown key: {key}")


def set_(args):
    job = load(args.job_id)
    before = job.get("stage")
    for pair in args.pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            sys.exit(f"expected key=value, got {pair}")
        apply(job, key, value)
    if job.get("stage") != before:
        entry = {"stage": job["stage"], "at": now()}
        if job["stage"] == "review":
            entry["round"] = job.get("round", 0)
        job.setdefault("history", []).append(entry)
    save(job)


def show(args):
    print(json.dumps(load(args.job_id), indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new")
    p.add_argument("job_id")
    p.add_argument("--feature", required=True)
    p.add_argument("--worktree", required=True)
    p.set_defaults(run=new)
    p = sub.add_parser("set")
    p.add_argument("job_id")
    p.add_argument("pairs", nargs="+")
    p.set_defaults(run=set_)
    p = sub.add_parser("show")
    p.add_argument("job_id")
    p.set_defaults(run=show)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
