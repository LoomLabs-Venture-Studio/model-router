#!/usr/bin/env python3
"""report.py: generate REPORT.md from a run's JSON artefacts.

Reads results.json, costs.json, decisions.json, and manifest.json (unused
directly but validated to exist for degrade-gracefully purposes). Missing
results.json or costs.json is a hard error; other inputs degrade to
"not available" / "pending" sections.

REPORT.md sections in order:
1. Headline: pass-bar result (optionally as-scored vs corrected) and cost per arm
2. Per-task table: tests passed/total, target, guard, cost, cost ratio, + totals row
3. Arm A routing decisions per task
4. Cost breakdown: per arm per model (token counts + USD) and per arm per role
5. Blind review: reviewer output embedded verbatim, de-anonymised via blind_key.json
6. Threats to validity: from README.md, plus "Observed in this run"
7. Operator notes (only when --notes is given)
8. Reproduce: the exact commands, including the flags actually used

Usage: report.py --run <id> [--runs-root <path>] [--review <path>]
                  [--as-scored <path>] [--notes <path>] [--out <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_RUNS_ROOT = Path("~/.cache/ab-routing/runs").expanduser()
DEFAULT_OUT = Path(__file__).resolve().parent / "REPORT.md"

TOKEN_LABELS = [
    ("input_tokens", "Input"),
    ("output_tokens", "Output"),
    ("cache_read_input_tokens", "Cache Read"),
    ("cache_write_5m_tokens", "Cache Write 5m"),
    ("cache_write_1h_tokens", "Cache Write 1h"),
]


def load_json(path: Path, required: bool = False) -> Any:
    """Load JSON from path, optionally requiring it to exist. Raises ValueError on error."""
    if not path.exists():
        if required:
            raise ValueError(f"required file not found: {path}")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"failed to read {path}: {e}")


def check_no_secrets(data: Any, path: Path) -> None:
    """Scan data for secrets (keys matching *api_key*/*authorization*/*token* with long
    string values). Raises ValueError on a hit."""
    def scan(obj: Any, key_path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_lower = k.lower()
                if any(x in k_lower for x in ["api_key", "authorization", "token"]):
                    if isinstance(v, str) and len(v) > 20:
                        raise ValueError(f"potential secret found in {path} at {key_path}.{k}: "
                                         f"key looks like secret, value is long string")
                scan(v, f"{key_path}.{k}" if key_path else k)
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                scan(item, f"{key_path}[{i}]")

    scan(data)


def format_usd(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"${value:.{decimals}f}"


# ----------------------------------------------------------------------------
# Headline
# ----------------------------------------------------------------------------
def task_totals(results: dict) -> dict:
    """Per-arm total tests PASSED (target+guard) and the grand total test count,
    computed independently from the pass_bar's B-relative ratio (which only
    tracks the overlap with whatever B passed, not each arm's own total)."""
    tasks = results.get("tasks", {})
    passed = {"A": 0, "B": 0}
    grand_total = 0
    for t in tasks.values():
        for arm in ("A", "B"):
            c = t.get(arm, {}).get("counts", {})
            passed[arm] += c.get("target", {}).get("passed", 0) + c.get("guard", {}).get("passed", 0)
        c = t.get("A", {}).get("counts") or t.get("B", {}).get("counts") or {}
        grand_total += c.get("target", {}).get("total", 0) + c.get("guard", {}).get("total", 0)
    return {"A": passed["A"], "B": passed["B"], "total": grand_total}


def format_pass_bar_row(label: str, results: dict) -> str:
    totals = task_totals(results)
    overall = results.get("pass_bar", {}).get("overall", {})
    ratio = overall.get("ratio")
    ratio_str = f"{ratio:.3f}" if ratio is not None else "undefined (B passed 0 tests)"
    verdict = "MEETS" if overall.get("meets_bar") else "does NOT meet"
    both = overall.get("tests_passed_by_both", 0)
    b = overall.get("tests_passed_by_B", 0)
    return (f"- **{label}:** A passed {totals['A']}/{totals['total']}, "
            f"B passed {totals['B']}/{totals['total']}. Pass bar (of what B passed, "
            f"how much did A also pass): {both}/{b} = {ratio_str} -> **{verdict}** the bar.")


def generate_headline(results: dict, costs: dict, as_scored: dict | None) -> str:
    threshold = results.get("pass_bar", {}).get("threshold", 0.90)
    lines = ["# A/B Routing Experiment Report\n", "\n## Headline\n\n"]

    if as_scored is not None:
        lines.append(format_pass_bar_row("As scored", as_scored) + "\n")
        lines.append(format_pass_bar_row("Corrected", results) + "\n")
    else:
        lines.append(format_pass_bar_row("Result", results) + "\n")
    lines.append(f"\nThreshold: {threshold:.0%}.\n")

    grand_total = costs.get("grand_total_usd", {})
    a_cost = grand_total.get("A", 0.0)
    b_cost = grand_total.get("B", 0.0)
    ratio_cost = (a_cost / b_cost) if b_cost else None
    ratio_cost_str = f"{ratio_cost:.3f}" if ratio_cost is not None else "N/A"
    lines.append(f"\n**Cost:** Arm A {format_usd(a_cost)}, Arm B {format_usd(b_cost)}. "
                 f"Ratio A/B: {ratio_cost_str}.\n")
    return "".join(lines)


# ----------------------------------------------------------------------------
# Per-task table
# ----------------------------------------------------------------------------
def generate_per_task_table(results: dict, costs: dict) -> str:
    tasks_data = results.get("tasks", {})
    per_task_costs = costs.get("per_task", {})

    lines = ["\n## Per-Task Results\n\n"]
    lines.append("| Task | A passed/total | B passed/total | A target | B target | "
                  "A guard | B guard | Cost A | Cost B | Ratio A/B |\n")
    lines.append("|------|-----------------|-----------------|----------|----------|"
                  "---------|---------|--------|--------|-----------|\n")

    totals = {k: 0 for k in ("a_p", "b_p", "total", "a_tp", "a_tt", "b_tp", "b_tt",
                              "a_gp", "a_gt", "b_gp", "b_gt")}
    totals["cost_a"] = totals["cost_b"] = 0.0

    for task_id in sorted(tasks_data.keys(), key=int):
        task_info = tasks_data[task_id]
        ac = task_info.get("A", {}).get("counts", {})
        bc = task_info.get("B", {}).get("counts", {})
        a_tp, a_tt = ac.get("target", {}).get("passed", 0), ac.get("target", {}).get("total", 0)
        b_tp, b_tt = bc.get("target", {}).get("passed", 0), bc.get("target", {}).get("total", 0)
        a_gp, a_gt = ac.get("guard", {}).get("passed", 0), ac.get("guard", {}).get("total", 0)
        b_gp, b_gt = bc.get("guard", {}).get("passed", 0), bc.get("guard", {}).get("total", 0)
        a_passed, b_passed = a_tp + a_gp, b_tp + b_gp
        task_total = a_tt + a_gt  # arm-independent: same hidden suite for both

        costs_for_task = per_task_costs.get(task_id, {})
        cost_a = costs_for_task.get("A", {}).get("usd", 0.0)
        cost_b = costs_for_task.get("B", {}).get("usd", 0.0)
        ratio_ab = (cost_a / cost_b) if cost_b else None
        ratio_ab_str = f"{ratio_ab:.3f}" if ratio_ab is not None else "N/A"

        lines.append(f"| {task_id} | {a_passed}/{task_total} | {b_passed}/{task_total} | "
                     f"{a_tp}/{a_tt} | {b_tp}/{b_tt} | {a_gp}/{a_gt} | {b_gp}/{b_gt} | "
                     f"{format_usd(cost_a)} | {format_usd(cost_b)} | {ratio_ab_str} |\n")

        totals["a_p"] += a_passed; totals["b_p"] += b_passed; totals["total"] += task_total
        totals["a_tp"] += a_tp; totals["a_tt"] += a_tt; totals["b_tp"] += b_tp; totals["b_tt"] += b_tt
        totals["a_gp"] += a_gp; totals["a_gt"] += a_gt; totals["b_gp"] += b_gp; totals["b_gt"] += b_gt
        totals["cost_a"] += cost_a; totals["cost_b"] += cost_b

    total_ratio = (totals["cost_a"] / totals["cost_b"]) if totals["cost_b"] else None
    total_ratio_str = f"{total_ratio:.3f}" if total_ratio is not None else "N/A"
    lines.append(f"| **Total** | {totals['a_p']}/{totals['total']} | {totals['b_p']}/{totals['total']} | "
                 f"{totals['a_tp']}/{totals['a_tt']} | {totals['b_tp']}/{totals['b_tt']} | "
                 f"{totals['a_gp']}/{totals['a_gt']} | {totals['b_gp']}/{totals['b_gt']} | "
                 f"{format_usd(totals['cost_a'])} | {format_usd(totals['cost_b'])} | {total_ratio_str} |\n")

    return "".join(lines)


# ----------------------------------------------------------------------------
# Routing decisions
# ----------------------------------------------------------------------------
def render_claude_decision(decision: dict) -> str:
    """A Phase 2 (router: "claude") decision: model, mode (with shards for
    fan-out), probe_first, verifier, confidence, reason."""
    mode = decision.get("mode")
    shards = decision.get("shards")
    mode_str = f"{mode} (shards={shards})" if mode == "fan-out" else str(mode)
    lines = [
        f"- Model: {decision.get('model')}\n",
        f"- Mode: {mode_str}\n",
        f"- Probe first: {decision.get('probe_first')}\n",
        f"- Verifier: {decision.get('verifier')}\n",
        f"- Confidence: {decision.get('confidence')}\n",
        f"- Reason: {decision.get('reason')}\n",
    ]
    if decision.get("probe_first_ignored"):
        lines.append("- (probe_first was re-requested after a probe already ran; ignored)\n")
    return "".join(lines)


def generate_routing_decisions(decisions: list[dict], has_notes: bool) -> str:
    if not decisions:
        return "\n## Arm A Routing Decisions\n\nNo routing decisions recorded.\n"

    lines = ["\n## Arm A Routing Decisions\n"]

    for task_n in (1, 2, 3, 4):
        task_decisions = [d for d in decisions if d.get("task") == task_n]
        if not task_decisions:
            continue
        lines.append(f"\n### Task {task_n}\n\n")

        for decision in task_decisions:
            call_type = decision.get("call_type", "initial")
            if call_type != "initial":
                lines.append(f"**Call type:** {call_type}\n\n")

            if decision.get("router") == "claude":
                lines.append(render_claude_decision(decision))
            else:
                scores_line = decision.get("scores_line", "")
                if scores_line:
                    lines.append(f"Scores: {scores_line}\n\n")

                one_liner = decision.get("one_liner")
                if one_liner:
                    lines.append(f"- Decision: {one_liner}\n")

            if "error" in decision:
                lines.append(f"- **Error:** {decision['error']}\n")

            lines.append("\n")

    if not has_notes:
        lines.append("**Deviations:** (operator to fill in)\n")

    return "".join(lines)


# ----------------------------------------------------------------------------
# Cost breakdown
# ----------------------------------------------------------------------------
def aggregate_breakdown_by(line_items: list[dict], group_fields: tuple[str, ...]) -> dict:
    """Groups line_items by group_fields (e.g. ("arm","model") or ("arm","role")),
    summing each breakdown token-type's tokens/usd plus the line's own total usd.
    per_arm_model in costs.json does NOT carry a breakdown (only "tokens"/"usd") --
    that only lives on individual line_items, which is why this reads from there."""
    groups: dict = {}
    for li in line_items:
        key = tuple(li.get(f) for f in group_fields)
        g = groups.setdefault(key, {"usd": 0.0, "by_type": {}})
        g["usd"] += li.get("usd", 0.0)
        for field, entry in (li.get("breakdown") or {}).items():
            bt = g["by_type"].setdefault(field, {"tokens": 0, "usd": 0.0})
            bt["tokens"] += entry.get("tokens", 0)
            bt["usd"] += entry.get("usd", 0.0)
    return groups


def generate_cost_breakdown(costs: dict) -> str:
    line_items = costs.get("line_items", [])
    lines = ["\n## Cost Breakdown\n"]

    by_arm_model = aggregate_breakdown_by(line_items, ("arm", "model"))
    header = ["Model"] + [label for _, label in TOKEN_LABELS] + ["Total"]
    for arm in ("A", "B"):
        lines.append(f"\n### Arm {arm} by model\n\n")
        lines.append("| " + " | ".join(header) + " |\n")
        lines.append("|" + "---|" * len(header) + "\n")
        models = sorted({k[1] for k in by_arm_model if k[0] == arm})
        arm_total = 0.0
        for model in models:
            g = by_arm_model[(arm, model)]
            cells = [model]
            for field, _ in TOKEN_LABELS:
                bt = g["by_type"].get(field)
                cells.append(f"{bt['tokens']:,} ({format_usd(bt['usd'], 4)})" if bt else "-")
            cells.append(format_usd(g["usd"], 4))
            arm_total += g["usd"]
            lines.append("| " + " | ".join(cells) + " |\n")
        lines.append(f"\n**Total for Arm {arm}:** {format_usd(arm_total, 4)}\n")

    by_arm_role = aggregate_breakdown_by(line_items, ("arm", "role"))
    lines.append("\n### Per role\n\n")
    lines.append("| Arm | Role | Total |\n|-----|------|-------|\n")
    for arm, role in sorted(by_arm_role, key=lambda k: (k[0], k[1])):
        lines.append(f"| {arm} | {role} | {format_usd(by_arm_role[(arm, role)]['usd'], 4)} |\n")

    lines.append("\n### Pricing Source\n\n")
    for provider, info in sorted(costs.get("pricing_source", {}).items()):
        lines.append(f"- **{provider}:** {info.get('source_url', 'N/A')} "
                     f"(retrieved {info.get('retrieved', 'N/A')})\n")

    return "".join(lines)


# ----------------------------------------------------------------------------
# Blind review
# ----------------------------------------------------------------------------
def parse_review_table(text: str) -> list[dict] | None:
    """Parses the FIRST markdown table in text whose header looks like
    'ticket | better (...) | X correctness | X scope | X quality | Y correctness
    | Y scope | Y quality' (columns are matched positionally, since we control
    the exact prompt that asks for this shape). Returns None if none is found
    or it doesn't have enough columns -- never raises."""
    lines = text.splitlines()
    start = None
    for i in range(len(lines) - 1):
        if lines[i].strip().startswith("|") and re.match(r"^\|?[\s:|-]+\|?$", lines[i + 1].strip()):
            start = i
            break
    if start is None:
        return None

    table_lines = [lines[start]]
    for line in lines[start + 1:]:
        if not line.strip().startswith("|"):
            break
        table_lines.append(line)
    if len(table_lines) < 3:
        return None

    header = [c.strip() for c in table_lines[0].strip().strip("|").split("|")]
    if len(header) < 8 or "ticket" not in header[0].lower() or "better" not in header[1].lower():
        return None

    rows = []
    for line in table_lines[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        m = re.search(r"(\d+)", cells[0])
        if not m:
            continue
        rows.append({
            "task": m.group(1), "better": cells[1].upper(),
            "x_correctness": cells[2], "x_scope": cells[3], "x_quality": cells[4],
            "y_correctness": cells[5], "y_scope": cells[6], "y_quality": cells[7],
        })
    return rows or None


def deanonymize_review_rows(rows: list[dict], blind_key: dict) -> list[dict]:
    mapping = blind_key.get("tasks", {})
    out = []
    for row in rows:
        task_map = mapping.get(row["task"])
        if not task_map:
            continue
        x_arm, y_arm = task_map.get("X"), task_map.get("Y")
        better = row["better"]
        better_arm = x_arm if better == "X" else y_arm if better == "Y" else "tie"
        scores = {}
        for metric in ("correctness", "scope", "quality"):
            scores[f"{x_arm.lower()}_{metric}"] = row[f"x_{metric}"]
            scores[f"{y_arm.lower()}_{metric}"] = row[f"y_{metric}"]
        out.append({"task": row["task"], "x_is": x_arm, "y_is": y_arm, "better": better_arm, **scores})
    return out


def render_deanonymized_table(rows: list[dict]) -> str:
    lines = ["| Task | X is | Y is | Better | A correctness | A scope | A quality | "
             "B correctness | B scope | B quality |\n",
             "|------|------|------|--------|----------------|---------|-----------|"
             "----------------|---------|-----------|\n"]
    for row in sorted(rows, key=lambda r: int(r["task"])):
        lines.append(f"| {row['task']} | {row['x_is']} | {row['y_is']} | {row['better']} | "
                     f"{row.get('a_correctness', '-')} | {row.get('a_scope', '-')} | "
                     f"{row.get('a_quality', '-')} | {row.get('b_correctness', '-')} | "
                     f"{row.get('b_scope', '-')} | {row.get('b_quality', '-')} |\n")
    return "".join(lines)


def generate_blind_review(run_dir: Path, review_text_path: Path | None) -> str:
    lines = ["\n## Blind Review\n"]

    if review_text_path is None or not review_text_path.exists():
        lines.append("\nStatus: **pending** (awaiting reviewer output; pass "
                      "`--review <file>` once available; see the review folder's "
                      "REVIEW_PROMPT.md for the prompt used).\n")
        return "".join(lines)

    review_text = review_text_path.read_text(encoding="utf-8")
    blind_key_path = run_dir / "blind_key.json"
    blind_key = load_json(blind_key_path) if blind_key_path.exists() else None

    if blind_key is None:
        lines.append(f"\n(could not de-anonymise: {blind_key_path} not found)\n")
    else:
        rows = parse_review_table(review_text)
        if rows is None:
            lines.append("\n(could not parse the reviewer's table into the expected "
                          "columns -- embedding the raw reviewer output below as-is)\n")
        else:
            deanon = deanonymize_review_rows(rows, blind_key)
            lines.append("\n### De-anonymised\n\n")
            lines.append(render_deanonymized_table(deanon))

    lines.append("\n### Reviewer output (verbatim)\n\n")
    lines.append(review_text.rstrip() + "\n")
    return "".join(lines)


# ----------------------------------------------------------------------------
# Three-way comparison (--compare-run): Jev-routed (compare-run arm A) vs
# Claude-routed (this run's arm A) vs all-session-model baseline (this run's arm B)
# ----------------------------------------------------------------------------
def role_usd(line_items: list[dict], arm: str, task_id: int, roles: set[str]) -> float:
    return sum(li.get("usd", 0.0) for li in line_items
               if li.get("arm") == arm and li.get("task") == task_id and li.get("role") in roles)


def role_marginal_usd(line_items: list[dict], arm: str, task_id: int, roles: set[str]) -> float:
    total = 0.0
    for li in line_items:
        if li.get("arm") != arm or li.get("task") != task_id or li.get("role") not in roles:
            continue
        if li.get("role") == "router":
            total += li.get("router_marginal_usd", li.get("usd", 0.0))
        else:
            total += li.get("usd", 0.0)
    return total


def task_summary(costs: dict, results: dict, arm: str, task_id: int) -> dict:
    task_id_str = str(task_id)
    counts = results.get("tasks", {}).get(task_id_str, {}).get(arm, {}).get("counts", {})
    passed = counts.get("target", {}).get("passed", 0) + counts.get("guard", {}).get("passed", 0)
    total = counts.get("target", {}).get("total", 0) + counts.get("guard", {}).get("total", 0)
    line_items = costs.get("line_items", [])
    total_cost = costs.get("per_task", {}).get(task_id_str, {}).get(arm, {}).get("usd", 0.0)
    return {
        "passed": passed,
        "total": total,
        "task_agent_cost": role_usd(line_items, arm, task_id, {"engineer"}),
        "overhead_full": role_usd(line_items, arm, task_id, {"router", "probe"}),
        "overhead_marginal": role_marginal_usd(line_items, arm, task_id, {"router", "probe"}),
        "total_cost": total_cost,
    }


def latest_decision_per_task(decisions: list[dict]) -> dict[int, dict]:
    """The last decision recorded per task (in a probe->reroute sequence, the
    after-probe call, since that is what actually got dispatched)."""
    latest: dict[int, dict] = {}
    for d in decisions:
        task = d.get("task")
        if task is not None:
            latest[task] = d
    return latest


def format_jev_choice(decision: dict | None) -> str:
    if decision is None:
        return "(no decision recorded)"
    one_liner = decision.get("one_liner")
    return one_liner if one_liner else "(no one-liner recorded)"


def format_claude_choice(decision: dict | None) -> str:
    if decision is None:
        return "(no decision recorded)"
    if decision.get("router") != "claude":
        return format_jev_choice(decision)
    mode = decision.get("mode")
    mode_str = f"fan-out x{decision.get('shards')}" if mode == "fan-out" else str(mode)
    return (f"model={decision.get('model')} mode={mode_str} "
            f"probe_first={decision.get('probe_first')} verifier={decision.get('verifier')} "
            f"confidence={decision.get('confidence')}")


def fraction_str(value: float, baseline: float) -> str:
    if not baseline:
        return "N/A"
    return f"{value / baseline:.3f}"


def generate_comparison_section(this_costs: dict, this_results: dict, this_decisions: list[dict],
                                 compare_costs: dict, compare_results: dict, compare_decisions: list[dict],
                                 compare_run_id: str) -> str:
    lines = ["\n## Comparison\n\n",
             f"Jev-routed is Arm A of run `{compare_run_id}` (Phase 1). Claude-routed is Arm A of "
             "this run (Phase 2). The all-session-model baseline is this run's Arm B.\n\n"]

    this_baseline_total = sum(task_summary(this_costs, this_results, "B", n)["total_cost"] for n in (1, 2, 3, 4))
    compare_baseline_total = sum(task_summary(compare_costs, compare_results, "B", n)["total_cost"]
                                  for n in (1, 2, 3, 4))
    this_baseline_tests = task_totals(this_results)["B"]
    compare_baseline_tests = task_totals(compare_results)["B"]
    if (round(this_baseline_total, 6) != round(compare_baseline_total, 6)
            or this_baseline_tests != compare_baseline_tests):
        lines.append(
            f"**Note:** the two runs' baselines (Arm B) differ -- {compare_run_id}: "
            f"{format_usd(compare_baseline_total, 4)}, {compare_baseline_tests} tests passed; "
            f"this run: {format_usd(this_baseline_total, 4)}, {this_baseline_tests} tests passed. "
            "This is not hidden: treat the comparison below as approximate if the baseline was not "
            "imported identically (see import_baseline.py).\n\n")

    header = ("| Task | Row | Tests passed/total | Task-agent cost | Overhead (full) | "
              "Overhead (marginal) | Total cost | Cost / baseline |\n")
    sep = "|------|-----|---------------------|------------------|------------------|" \
          "----------------------|------------|-----------------|\n"
    lines.append(header)
    lines.append(sep)

    rows_spec = [
        ("Jev-routed", compare_costs, compare_results, "A"),
        ("Claude-routed", this_costs, this_results, "A"),
        ("Baseline", this_costs, this_results, "B"),
    ]

    totals = {label: {"passed": 0, "total": 0, "task_agent_cost": 0.0, "overhead_full": 0.0,
                       "overhead_marginal": 0.0, "total_cost": 0.0} for label, *_ in rows_spec}

    for task_id in (1, 2, 3, 4):
        baseline_cost = task_summary(this_costs, this_results, "B", task_id)["total_cost"]
        for label, costs, results, arm in rows_spec:
            s = task_summary(costs, results, arm, task_id)
            lines.append(f"| {task_id} | {label} | {s['passed']}/{s['total']} | "
                         f"{format_usd(s['task_agent_cost'], 4)} | {format_usd(s['overhead_full'], 4)} | "
                         f"{format_usd(s['overhead_marginal'], 4)} | {format_usd(s['total_cost'], 4)} | "
                         f"{fraction_str(s['total_cost'], baseline_cost)} |\n")
            t = totals[label]
            t["passed"] += s["passed"]; t["total"] += s["total"]
            t["task_agent_cost"] += s["task_agent_cost"]; t["overhead_full"] += s["overhead_full"]
            t["overhead_marginal"] += s["overhead_marginal"]; t["total_cost"] += s["total_cost"]

    for label, *_ in rows_spec:
        t = totals[label]
        lines.append(f"| **Total** | {label} | {t['passed']}/{t['total']} | "
                     f"{format_usd(t['task_agent_cost'], 4)} | {format_usd(t['overhead_full'], 4)} | "
                     f"{format_usd(t['overhead_marginal'], 4)} | {format_usd(t['total_cost'], 4)} | "
                     f"{fraction_str(t['total_cost'], this_baseline_total)} |\n")

    lines.append("\n### What each router chose\n\n")
    lines.append("| Task | Jev (compare run) | Claude (this run) |\n|------|--------------------|--------------------|\n")
    jev_latest = latest_decision_per_task(compare_decisions)
    claude_latest = latest_decision_per_task(this_decisions)
    for task_id in (1, 2, 3, 4):
        lines.append(f"| {task_id} | {format_jev_choice(jev_latest.get(task_id))} | "
                     f"{format_claude_choice(claude_latest.get(task_id))} |\n")

    return "".join(lines)


# ----------------------------------------------------------------------------
# Threats, operator notes, reproduce
# ----------------------------------------------------------------------------
def generate_threats_to_validity(has_notes: bool) -> str:
    threats = [
        "Four tasks, one run per arm per task: differences are indicative, not statistically significant.",
        "The fixture is 139 lines, so scope (S) barely varies across tasks.",
        "Agents have filesystem access and could in principle find the hidden tests even though run copies live outside the repo and briefs never mention them.",
        "Arm B's model is whatever the CTO session happens to run on, not a fixed reference model.",
    ]

    lines = ["\n## Threats to Validity\n\n", "### Pre-registered (from PLAYBOOK.md)\n\n"]
    for threat in threats:
        lines.append(f"- {threat}\n")

    lines.append("\n### Observed in this run\n\n")
    if has_notes:
        lines.append("See Operator Notes below.\n")
    else:
        lines.append("(operator to fill in)\n")

    return "".join(lines)


def generate_operator_notes(notes_path: Path) -> str:
    return "\n## Operator Notes\n\n" + notes_path.read_text(encoding="utf-8").rstrip() + "\n"


def generate_reproduce(run_id: str, runs_root_arg: str | None, review_arg: str | None,
                        as_scored_arg: str | None, notes_arg: str | None, compare_run_arg: str | None) -> str:
    runs_root_flag = f" --runs-root {runs_root_arg}" if runs_root_arg else ""
    lines = ["\n## Reproduce\n\n", "```bash\n"]
    lines.append(f"# Create the run\npython3 experiments/ab-routing/make_run.py --run {run_id}{runs_root_flag}\n\n")
    lines.append("# Dispatch Arm B then Arm A per README.md's order of operations, fill manifest.json\n\n")
    lines.append(f"# Score the run\npython3 experiments/ab-routing/score.py --run {run_id}{runs_root_flag}\n\n")
    lines.append(f"# Account for costs\npython3 experiments/ab-routing/account.py --run {run_id}{runs_root_flag}\n\n")
    lines.append(f"# Generate blind diffs\npython3 experiments/ab-routing/blind_pack.py --run {run_id}{runs_root_flag}\n\n")
    report_cmd = f"python3 experiments/ab-routing/report.py --run {run_id}{runs_root_flag}"
    if review_arg:
        report_cmd += f" --review {review_arg}"
    if as_scored_arg:
        report_cmd += f" --as-scored {as_scored_arg}"
    if notes_arg:
        report_cmd += f" --notes {notes_arg}"
    if compare_run_arg:
        report_cmd += f" --compare-run {compare_run_arg}"
    lines.append(f"# Generate this report\n{report_cmd}\n")
    lines.append("```\n")
    return "".join(lines)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--runs-root", help=f"default: {DEFAULT_RUNS_ROOT}")
    parser.add_argument("--review", help="path to the reviewer's raw output text (optional)")
    parser.add_argument("--as-scored", help="path to an earlier results.json to compare against the "
                                             "current one (optional; e.g. before a hidden-test fix)")
    parser.add_argument("--notes", help="path to a markdown file of operator notes, embedded verbatim (optional)")
    parser.add_argument("--compare-run", help="a Phase 1 (Jev-routed) run id, same --runs-root, for a "
                                               "three-way Comparison section (optional)")
    parser.add_argument("--out", help=f"default: {DEFAULT_OUT}")
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root).expanduser() if args.runs_root else DEFAULT_RUNS_ROOT
    run_dir = runs_root / args.run

    if not run_dir.is_dir():
        print(f"report: run {args.run!r} not found at {run_dir}", file=sys.stderr)
        return 1

    try:
        results = load_json(run_dir / "results.json", required=True)
        costs = load_json(run_dir / "costs.json", required=True)
        check_no_secrets(costs, run_dir / "costs.json")

        as_scored = None
        if args.as_scored:
            as_scored = load_json(Path(args.as_scored).expanduser(), required=True)

        decisions = load_json(run_dir / "decisions.json") or []
        load_json(run_dir / "manifest.json")  # validated readable; not otherwise used here

        notes_path = Path(args.notes).expanduser() if args.notes else None
        has_notes = bool(notes_path and notes_path.exists())
        review_path = Path(args.review).expanduser() if args.review else None

        comparison = ""
        if args.compare_run:
            compare_dir = runs_root / args.compare_run
            if not compare_dir.is_dir():
                raise ValueError(f"--compare-run {args.compare_run!r} not found at {compare_dir}")
            compare_results = load_json(compare_dir / "results.json", required=True)
            compare_costs = load_json(compare_dir / "costs.json", required=True)
            compare_decisions = load_json(compare_dir / "decisions.json") or []
            comparison = generate_comparison_section(costs, results, decisions,
                                                      compare_costs, compare_results, compare_decisions,
                                                      args.compare_run)

        report = "".join([
            generate_headline(results, costs, as_scored),
            generate_per_task_table(results, costs),
            comparison,
            generate_routing_decisions(decisions, has_notes),
            generate_cost_breakdown(costs),
            generate_blind_review(run_dir, review_path),
            generate_threats_to_validity(has_notes),
            generate_operator_notes(notes_path) if has_notes else "",
            generate_reproduce(args.run, args.runs_root, args.review, args.as_scored, args.notes, args.compare_run),
        ])

        out_path = Path(args.out) if args.out else DEFAULT_OUT
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

        print(f"report: wrote {out_path}")
        return 0
    except (ValueError, OSError) as e:
        print(f"report: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
