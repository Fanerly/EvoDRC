#BSD 3-Clause License
#
#Copyright (c) 2026, ASU-VDA-Lab
#
#Redistribution and use in source and binary forms, with or without
#modification, are permitted provided that the following conditions are met:
#
#1. Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
#2. Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
#3. Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
#THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#################################################################################

"""Bounded difficult-unit planning and faithful candidate selection.

This module makes no model calls.  The controller uses :class:`DifficultyTracker`
to allocate a hard per-iteration extra-call budget before dispatch.  The leaf
runner then creates isolated candidate files, and :func:`evaluate_and_select`
validates and measures them with the existing connectivity and faithful crop
DRC interfaces.

Only operations understood by ``drc_preview._apply_ops_to_geom`` receive a DRC
score.  An unmeasurable extra candidate is rejected rather than priced with an
approximation. Candidate 0 remains the original single-call fallback when it
passes parsing, validation and connectivity but faithful DRC is unavailable.
"""

import copy
import hashlib
import json
import os
from collections import Counter

from .. import validator
from ..drc_check import run_faithful_crop_drc
from ..drc_preview import _apply_ops_to_geom
from ..patch_parser import parse_patch_from_file_text
from . import gate as _gate
from .cu_drc import MEASURABLE_OPS


SIGNATURE_GRID_DBU = 4000
HIGH_CONFLICT_DEGREE = 2


def _json_write(path, doc):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _unit_rule_ids(leaf, ctx):
    by_id = dict((v.violation_id, v) for v in (ctx.violations or []))
    return sorted(set(
        getattr(by_id.get(vid), "rule_id", "?")
        for vid in (leaf.violations or [])
        if by_id.get(vid) is not None))


def _quantized_bbox(bbox):
    grid = SIGNATURE_GRID_DBU
    return [int(round(float(v) / grid)) for v in (bbox or (0, 0, 0, 0))]


def stable_unit_signature(leaf, ctx):
    """Return a deterministic signature independent of the repair-unit id.

    PDN rail, coarse region, rule set, and stable editable object identities
    survive ordinary leaf renumbering.  The digest keeps large PDN object lists
    out of logs and audit JSON.
    """
    payload = {
        "pdn": bool(getattr(leaf, "is_pdn", False)),
        "pdn_rail": str(getattr(leaf, "pdn_rail", "") or ""),
        "bbox_grid": _quantized_bbox(getattr(leaf, "bbox_dbu", None)),
        "rules": _unit_rule_ids(leaf, ctx),
        "editable_polygons": sorted(
            str(x) for x in (getattr(leaf, "editable_polygons", []) or [])),
        "owned_instances": sorted(
            str(x) for x in (getattr(leaf, "owned_instances", ()) or ())),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "unit-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _conflict_degree(unit, ctx):
    ids = list(unit.get("member_leaf_ids") or [unit.get("unit_id")])
    return max([int((ctx.leaf_conflict_degree or {}).get(x, 0) or 0)
                for x in ids] or [0])


class DifficultyTracker(object):
    """Track stable unit history and allocate a bounded candidate plan."""

    def __init__(self):
        self.history = {}

    def plan_iteration(self, iteration, units, ctx, cfg):
        ranked = []
        for unit in units or []:
            uid = unit.get("unit_id")
            leaf = (ctx.leaves or {}).get(uid)
            if leaf is None or int(unit.get("n_viol", 0) or 0) <= 0:
                continue
            signature = stable_unit_signature(leaf, ctx)
            hist = self.history.setdefault(signature, {
                "empty_streak": 0, "stagnant_rounds": 0,
                "last_n_viol": None, "last_seen_iteration": None})
            # "Consecutive" history must not bridge an iteration in which the
            # stable unit disappeared.  Repair-unit ids can change, so use the
            # signature's last observation rather than the current id.
            last_seen = hist["last_seen_iteration"]
            if last_seen is not None and last_seen != iteration - 1:
                hist["empty_streak"] = 0
                hist["stagnant_rounds"] = 0
                hist["last_n_viol"] = None
            n_viol = int(unit.get("n_viol", len(leaf.violations)) or 0)
            if hist["last_n_viol"] is not None:
                if n_viol >= hist["last_n_viol"]:
                    hist["stagnant_rounds"] += 1
                else:
                    hist["stagnant_rounds"] = 0
            hist["last_n_viol"] = n_viol
            hist["last_seen_iteration"] = iteration

            pdn = bool(getattr(leaf, "is_pdn", False))
            conflict = _conflict_degree(unit, ctx)
            reasons = []
            if n_viol >= cfg.multi_candidate_min_drv:
                reasons.append("high_drv")
            if pdn and cfg.multi_candidate_allow_pdn:
                reasons.append("pdn")
            if hist["empty_streak"] >= cfg.multi_candidate_empty_streak:
                reasons.append("empty_streak")
            if hist["stagnant_rounds"] >= \
                    cfg.multi_candidate_stagnation_rounds:
                reasons.append("stagnant")
            if conflict >= HIGH_CONFLICT_DEGREE:
                reasons.append("high_conflict")
            if not reasons:
                continue
            ranked.append({
                "unit_id": uid,
                "stable_signature": signature,
                "trigger_reasons": reasons,
                "n_viol": n_viol,
                "is_pdn": pdn,
                "conflict_degree": conflict,
                "empty_streak": hist["empty_streak"],
                "stagnant_rounds": hist["stagnant_rounds"],
            })

        ranked.sort(key=lambda x: (
            -int(x["n_viol"]), -int(x["empty_streak"]),
            -int(x["stagnant_rounds"]), -int(x["conflict_degree"]),
            x["stable_signature"]))
        remaining = cfg.multi_candidate_max_extra_calls
        plan = {}
        for rec in ranked[:cfg.multi_candidate_max_units]:
            extras = min(cfg.multi_candidate_count - 1, remaining)
            if extras < 1:
                break
            item = dict(rec)
            item["candidate_count"] = 1 + extras
            item["planned_extra_calls"] = extras
            plan[item["unit_id"]] = item
            remaining -= extras
        return plan

    def observe_candidate_zero(self, unit_id, leaf, ctx, patch_obj):
        """Update the empty-patch streak using candidate 0 only."""
        signature = stable_unit_signature(leaf, ctx)
        hist = self.history.setdefault(signature, {
            "empty_streak": 0, "stagnant_rounds": 0,
            "last_n_viol": None, "last_seen_iteration": None})
        ops = (patch_obj or {}).get("ops")
        if not isinstance(ops, list) or not ops:
            hist["empty_streak"] += 1
        else:
            hist["empty_streak"] = 0
        return signature


def _load_patch(path, leaf_id):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    return parse_patch_from_file_text(text, leaf_id)


def _load_generation(patch_path):
    path = os.path.join(os.path.dirname(patch_path), "generation.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _counter_metrics(before, after):
    before = Counter(before or {})
    after = Counter(after or {})
    new_count = sum(max(0, n - before.get(k, 0))
                    for k, n in after.items())
    removed_count = sum(max(0, n - after.get(k, 0))
                        for k, n in before.items())
    before_total = sum(before.values())
    after_total = sum(after.values())
    return {
        "drc_before": before_total,
        "drc_after": after_total,
        "new_drv": new_count,
        "removed_drv": removed_count,
        "net_drv_improvement": before_total - after_total,
    }


def _candidate_paths(unit_dir, candidate_count):
    return [
        (idx, os.path.join(unit_dir, "candidates",
                           "candidate_{0}".format(idx), "patch.json"))
        for idx in range(candidate_count)
    ]


def _write_selected(unit_dir, leaf_id, selected, signature):
    path = os.path.join(unit_dir, "patch.json")
    if selected is None:
        doc = {"leaf_id": leaf_id, "ops": [],
               "explanation": "all limited multi-candidates rejected",
               "candidate_index": None,
               "stable_unit_signature": signature}
    else:
        doc = {"leaf_id": leaf_id, "ops": list(selected["patch"].ops),
               "explanation": selected["patch"].explanation or "",
               "candidate_index": selected["candidate_index"],
               "stable_unit_signature": signature}
    _json_write(path, doc)


def evaluate_and_select(iteration, unit_id, unit_dir, leaf, ctx,
                        input_layout_text, golden_conn_path, design_type,
                        scratch_dir, plan,
                        validate_fn=None, gate_fn=None, drc_fn=None,
                        apply_geom_fn=None):
    """Faithfully score one unit's bounded candidates and select one patch.

    Dependency injection is intentionally narrow and used by offline tests;
    production defaults are the existing validator, connectivity gate, crop
    DRC runner and geometry application logic.
    """
    validate_fn = validate_fn or validator.validate
    gate_fn = gate_fn or _gate.gate_leaf
    drc_fn = drc_fn or run_faithful_crop_drc
    apply_geom_fn = apply_geom_fn or _apply_ops_to_geom
    candidate_count = int(plan.get("candidate_count", 1) or 1)
    signature = plan.get("stable_signature") or stable_unit_signature(leaf, ctx)

    baseline = None
    baseline_error = None
    try:
        baseline = drc_fn(leaf, ctx)
    except Exception as exc:                          # noqa: BLE001
        baseline_error = type(exc).__name__

    verdicts = []
    internal = []
    for idx, patch_path in _candidate_paths(unit_dir, candidate_count):
        rec = {
            "candidate_index": idx,
            "patch_path": os.path.relpath(patch_path, unit_dir),
            "parsed": False,
            "validator_ok": False,
            "validator_check": None,
            "connectivity_preserved": False,
            "drc_success": False,
            "drc_error": baseline_error,
            "drc_before": None,
            "drc_after": None,
            "new_drv": None,
            "removed_drv": None,
            "net_drv_improvement": None,
            "n_ops": 0,
            "selected": False,
            "rejection_reason": None,
        }
        generation = _load_generation(patch_path)
        rec["call_attempted"] = generation.get("call_attempted") is True
        rec["backend_status"] = generation.get("status")
        rec["generation_parsed"] = generation.get("parsed")
        if generation and generation.get("parsed") is not True:
            rec["rejection_reason"] = "parse_failed"
            verdicts.append(rec)
            internal.append(None)
            continue
        patch = _load_patch(patch_path, unit_id)
        if patch is None:
            rec["rejection_reason"] = "parse_failed"
            verdicts.append(rec)
            internal.append(None)
            continue
        rec["parsed"] = True
        rec["n_ops"] = len(patch.ops or [])
        if not patch.ops:
            rec["rejection_reason"] = "empty_patch"
            verdicts.append(rec)
            internal.append(None)
            continue

        try:
            vv = validate_fn(patch, leaf, ctx)
        except Exception as exc:                      # noqa: BLE001
            rec["validator_check"] = type(exc).__name__
            rec["rejection_reason"] = "validator_exception"
            verdicts.append(rec)
            internal.append(None)
            continue
        rec["validator_ok"] = bool(vv.ok)
        rec["validator_check"] = vv.check_name or None
        rec["validator_reason"] = str(vv.reason or "")[:400] or None
        if not vv.ok:
            rec["rejection_reason"] = "validator_rejected"
            verdicts.append(rec)
            internal.append(None)
            continue

        candidate_scratch = os.path.join(
            scratch_dir, "candidate_{0}".format(idx))
        try:
            gv = gate_fn(ctx, unit_id, patch_path, input_layout_text,
                         golden_conn_path, design_type, candidate_scratch)
            rec["connectivity_preserved"] = bool(
                gv.get("connectivity_preserved"))
            rec["connectivity_reason"] = gv.get("reason")
        except Exception as exc:                      # noqa: BLE001
            rec["rejection_reason"] = "connectivity_exception"
            rec["connectivity_error"] = type(exc).__name__
            verdicts.append(rec)
            internal.append(None)
            continue
        if not rec["connectivity_preserved"]:
            rec["rejection_reason"] = "connectivity_failed"
            verdicts.append(rec)
            internal.append(None)
            continue

        unsupported = sorted(set(
            str(op.get("op") if isinstance(op, dict) else type(op).__name__)
            for op in patch.ops
            if not isinstance(op, dict)
            or op.get("op") not in MEASURABLE_OPS))
        if unsupported:
            rec["drc_error"] = "unmeasurable_ops"
            rec["unmeasurable_ops"] = unsupported
        elif baseline is not None:
            original_geom = ctx.geometry_model
            candidate_leaf = copy.deepcopy(leaf)
            try:
                ctx.geometry_model = copy.deepcopy(original_geom)
                apply_geom_fn(ctx, candidate_leaf, list(patch.ops))
                after = drc_fn(candidate_leaf, ctx)
                rec.update(_counter_metrics(baseline, after))
                rec["drc_success"] = True
                rec["drc_error"] = None
            except Exception as exc:                  # noqa: BLE001
                rec["drc_error"] = type(exc).__name__
            finally:
                ctx.geometry_model = original_geom

        # Extra candidates require a faithful DRC score. Candidate 0 remains
        # the original validated/connectivity-safe fallback when DRC cannot be
        # obtained, so a 4B measurement failure cannot destroy the legacy path.
        if idx > 0 and not rec["drc_success"]:
            rec["rejection_reason"] = "extra_candidate_drc_unavailable"
        verdicts.append(rec)
        internal.append({"candidate_index": idx, "patch": patch,
                         "record": rec})

    fully_scored = [x for x in internal if x is not None
                    and x["record"]["drc_success"]]
    selected = None
    selection_reason = "all_candidates_rejected"
    if fully_scored:
        selected = sorted(fully_scored, key=lambda x: (
            x["record"]["new_drv"],
            -x["record"]["net_drv_improvement"],
            x["record"]["n_ops"],
            x["candidate_index"]))[0]
        selection_reason = "best_faithful_drc_candidate"
    elif internal and internal[0] is not None:
        selected = internal[0]
        selection_reason = "candidate_0_legal_fallback"

    if selected is not None:
        selected["record"]["selected"] = True
        selected["record"]["rejection_reason"] = None
        selected_index = selected["candidate_index"]
    else:
        selected_index = None
    for rec in verdicts:
        if not rec["selected"] and rec["rejection_reason"] is None:
            rec["rejection_reason"] = "ranked_lower"

    _write_selected(unit_dir, unit_id, selected, signature)
    actual_extra_calls = 0
    for idx in range(1, candidate_count):
        gp = os.path.join(unit_dir, "candidates",
                          "candidate_{0}".format(idx), "generation.json")
        try:
            with open(gp, "r", encoding="utf-8") as fh:
                if json.load(fh).get("call_attempted") is True:
                    actual_extra_calls += 1
        except (OSError, ValueError):
            pass
    audit = {
        "iteration": iteration,
        "repair_unit_id": unit_id,
        "stable_unit_signature": signature,
        "trigger_reasons": list(plan.get("trigger_reasons") or []),
        "candidate_count": candidate_count,
        "planned_extra_model_calls": int(
            plan.get("planned_extra_calls", 0) or 0),
        "actual_extra_model_calls": actual_extra_calls,
        "baseline_drc_success": baseline is not None,
        "baseline_drc_error": baseline_error,
        "selected_candidate": selected_index,
        "selection_reason": selection_reason,
        "candidates": verdicts,
    }
    _json_write(os.path.join(unit_dir, "candidate_verdicts.json"), audit)
    return audit
