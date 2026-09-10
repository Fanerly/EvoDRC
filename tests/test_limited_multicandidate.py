import json
import os
import tempfile
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest import mock

from agent.src.iter import conf
from agent.src.iter import leaf_runner
from agent.src.iter.multicandidate import (
    DifficultyTracker, evaluate_and_select, stable_unit_signature)
from agent.src.iter.state_policy import BestValidState
from agent.src.types import Verdict


def _ctx(unit_id="leaf_0001", n_viol=60, pdn=False, conflict=0):
    vids = ["v{0}".format(i) for i in range(n_viol)]
    leaf = SimpleNamespace(
        leaf_id=unit_id, violations=vids, is_pdn=pdn,
        pdn_rail=("VDD" if pdn else ""), bbox_dbu=(0, 0, 8000, 8000),
        editable_polygons=["p1", "p2"], owned_instances=("i1",), depth=0)
    violations = [SimpleNamespace(violation_id=v, rule_id="M1.S.2")
                  for v in vids]
    ctx = SimpleNamespace(
        leaves={unit_id: leaf}, violations=violations,
        leaf_conflict_degree={unit_id: conflict},
        geometry_model={"original": True})
    unit = {"unit_id": unit_id, "n_viol": n_viol,
            "member_leaf_ids": [unit_id]}
    return ctx, leaf, unit


def _cfg(**overrides):
    values = {
        "multi_candidate_count": 2,
        "multi_candidate_max_units": 1,
        "multi_candidate_max_extra_calls": 1,
        "multi_candidate_min_drv": 50,
        "multi_candidate_empty_streak": 2,
        "multi_candidate_stagnation_rounds": 2,
        "multi_candidate_allow_pdn": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_candidate(root, index, ops, parsed=True):
    cdir = os.path.join(root, "candidates",
                        "candidate_{0}".format(index))
    os.makedirs(cdir)
    with open(os.path.join(cdir, "patch.json"), "w",
              encoding="utf-8") as fh:
        if parsed:
            json.dump({"leaf_id": "leaf_0001", "ops": ops,
                       "explanation": "test"}, fh)
        else:
            fh.write("not json")
    with open(os.path.join(cdir, "generation.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"candidate_index": index, "call_attempted": True,
                   "status": "success", "parsed": parsed}, fh)


class FakeBackend(object):
    def __init__(self, args):
        self.args = args
        self.calls = []

    def call_agent(self, *pos, **kw):
        call_id = kw.get("call_id")
        self.calls.append(call_id)
        index = int(call_id.rsplit("_", 1)[1])
        cdir = os.path.join(self.args.work_dir,
                            "candidate_{0}".format(index))
        with open(os.path.join(cdir, "patch_raw.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"leaf_id": self.args.leaf_id,
                       "ops": [{"op": "resize", "polygon_id":
                                "p{0}".format(index)}],
                       "explanation": "alternative"}, fh)
        return {"status": "success", "raw_data": {}}


class LimitedMultiCandidateTest(unittest.TestCase):
    def test_disabled_or_unselected_unit_generates_no_extra_call(self):
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                candidate_count="1", out_dir=os.path.join(td, "published"),
                work_dir=os.path.join(td, "work"), leaf_id="leaf_0001",
                call_id="Block7_iter1_leaf_0001", model="model",
                workspace="", score_calls_dir="")
            os.makedirs(args.out_dir)
            os.makedirs(args.work_dir)
            backend = FakeBackend(args)
            leaf_runner._generate_additional_candidates(
                args, backend, "prompt", "/original/patch_raw.json",
                "/original/trace.md", "success", True,
                [{"op": "resize", "polygon_id": "p0"}], "candidate zero")
            self.assertEqual([], backend.calls)
            self.assertFalse(os.path.exists(
                os.path.join(args.out_dir, "candidates")))

    def test_difficult_unit_gets_bounded_isolated_alternatives(self):
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                candidate_count="3", out_dir=os.path.join(td, "published"),
                work_dir=os.path.join(td, "work"), leaf_id="leaf_0001",
                call_id="Block7_iter1_leaf_0001", model="model",
                workspace="", score_calls_dir="")
            os.makedirs(args.out_dir)
            os.makedirs(args.work_dir)
            backend = FakeBackend(args)
            leaf_runner._generate_additional_candidates(
                args, backend,
                "write /original/patch_raw.json and /original/trace.md",
                "/original/patch_raw.json", "/original/trace.md", "success",
                True, [{"op": "resize", "polygon_id": "p0"}], "zero")

            self.assertEqual(2, len(backend.calls))
            paths = [os.path.join(args.out_dir, "candidates",
                                  "candidate_{0}".format(i), "patch.json")
                     for i in range(3)]
            self.assertTrue(all(os.path.isfile(p) for p in paths))
            self.assertEqual(3, len(set(paths)))
            with open(paths[0], encoding="utf-8") as fh:
                self.assertEqual("p0", json.load(fh)["ops"][0]["polygon_id"])
            with open(paths[1], encoding="utf-8") as fh:
                self.assertEqual("p1", json.load(fh)["ops"][0]["polygon_id"])

    def test_leaf_runner_hard_caps_even_untrusted_candidate_count(self):
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                candidate_count="999", out_dir=os.path.join(td, "published"),
                work_dir=os.path.join(td, "work"), leaf_id="leaf_0001",
                call_id="Block7_iter1_leaf_0001", model="model",
                workspace="", score_calls_dir="")
            os.makedirs(args.out_dir)
            os.makedirs(args.work_dir)
            backend = FakeBackend(args)
            leaf_runner._generate_additional_candidates(
                args, backend, "prompt", "/original/patch_raw.json",
                "/original/trace.md", "success", True,
                [{"op": "resize", "polygon_id": "p0"}], "zero")
            self.assertEqual(2, len(backend.calls))

    def test_not_difficult_has_empty_plan(self):
        ctx, _leaf, unit = _ctx(n_viol=4, pdn=False, conflict=0)
        plan = DifficultyTracker().plan_iteration(1, [unit], ctx, _cfg())
        self.assertEqual({}, plan)

    def test_difficult_plan_obeys_unit_and_extra_call_budgets(self):
        tracker = DifficultyTracker()
        contexts = [_ctx("leaf_{0:04d}".format(i), n_viol=100 - i)
                    for i in range(1, 5)]
        ctx = SimpleNamespace(
            leaves={}, violations=[], leaf_conflict_degree={},
            geometry_model={})
        units = []
        for item_ctx, leaf, unit in contexts:
            ctx.leaves[leaf.leaf_id] = leaf
            ctx.violations.extend(item_ctx.violations)
            ctx.leaf_conflict_degree[leaf.leaf_id] = 0
            units.append(unit)
        plan = tracker.plan_iteration(
            1, units, ctx,
            _cfg(multi_candidate_count=3, multi_candidate_max_units=2,
                 multi_candidate_max_extra_calls=3))
        self.assertLessEqual(len(plan), 2)
        self.assertLessEqual(sum(x["planned_extra_calls"]
                                 for x in plan.values()), 3)
        self.assertTrue(all(2 <= x["candidate_count"] <= 3
                            for x in plan.values()))

    def test_signature_does_not_depend_on_leaf_id(self):
        ctx1, leaf1, _unit1 = _ctx("leaf_0001")
        ctx2, leaf2, _unit2 = _ctx("leaf_9999")
        self.assertEqual(stable_unit_signature(leaf1, ctx1),
                         stable_unit_signature(leaf2, ctx2))

    def test_stagnation_history_does_not_bridge_missing_iteration(self):
        ctx, _leaf, unit = _ctx(n_viol=4)
        tracker = DifficultyTracker()
        cfg = _cfg(multi_candidate_min_drv=50,
                   multi_candidate_stagnation_rounds=2)
        self.assertEqual({}, tracker.plan_iteration(1, [unit], ctx, cfg))
        self.assertEqual({}, tracker.plan_iteration(3, [unit], ctx, cfg))
        self.assertEqual({}, tracker.plan_iteration(4, [unit], ctx, cfg))
        plan = tracker.plan_iteration(5, [unit], ctx, cfg)
        self.assertIn("stagnant", plan[unit["unit_id"]]["trigger_reasons"])

    def _evaluate(self, candidates, after_by_score, conn_by_score=None,
                  baseline_error=False, validate_by_score=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = td.name
        for idx, candidate in enumerate(candidates):
            if candidate == "parse_fail":
                _write_candidate(root, idx, [], parsed=False)
            else:
                score, n_ops = candidate
                ops = [{"op": "resize", "polygon_id": "p1",
                        "score": score, "conn": True}]
                ops.extend({"op": "resize", "polygon_id": "p1",
                            "score": score, "conn": True}
                           for _ in range(n_ops - 1))
                _write_candidate(root, idx, ops)
        ctx, leaf, _unit = _ctx(n_viol=5)
        baseline = Counter({("R", (0, 0, 1, 1)): 5})

        def validate_fn(patch, _leaf, _ctx):
            score = patch.ops[0]["score"]
            ok = (validate_by_score or {}).get(score, True)
            return Verdict(ok=ok, check_name=("test" if not ok else ""),
                           reason=("invalid" if not ok else ""))

        def gate_fn(_ctx, _uid, patch_path, _text, _golden, _dtype,
                    _scratch):
            with open(patch_path, encoding="utf-8") as fh:
                score = json.load(fh)["ops"][0]["score"]
            ok = (conn_by_score or {}).get(score, True)
            return {"connectivity_preserved": ok,
                    "reason": "conn_preserved" if ok else "conn_broken"}

        def apply_fn(_ctx, candidate_leaf, ops):
            candidate_leaf.depth = ops[0]["score"]

        def drc_fn(candidate_leaf, _ctx):
            if baseline_error:
                raise RuntimeError("offline simulated DRC failure")
            if candidate_leaf.depth == 0:
                return baseline
            return after_by_score[candidate_leaf.depth]

        plan = {"candidate_count": len(candidates),
                "planned_extra_calls": len(candidates) - 1,
                "stable_signature": "unit-test",
                "trigger_reasons": ["high_drv"]}
        audit = evaluate_and_select(
            1, "leaf_0001", root, leaf, ctx, "layout", "golden", "block",
            os.path.join(root, "scratch"), plan,
            validate_fn=validate_fn, gate_fn=gate_fn, drc_fn=drc_fn,
            apply_geom_fn=apply_fn)
        with open(os.path.join(root, "patch.json"), encoding="utf-8") as fh:
            selected_patch = json.load(fh)
        return audit, selected_patch

    def test_parse_failure_does_not_affect_candidate_zero(self):
        audit, selected = self._evaluate(
            [(1, 1), "parse_fail"],
            {1: Counter({("R", (0, 0, 1, 1)): 4})})
        self.assertEqual(0, audit["selected_candidate"])
        self.assertEqual("parse_failed",
                         audit["candidates"][1]["rejection_reason"])
        self.assertEqual(0, selected["candidate_index"])

    def test_connectivity_failed_candidate_never_wins_or_reaches_patch(self):
        audit, selected = self._evaluate(
            [(1, 1), (2, 1)],
            {1: Counter({("R", (0, 0, 1, 1)): 4}),
             2: Counter()}, conn_by_score={2: False})
        self.assertEqual(0, audit["selected_candidate"])
        self.assertEqual("connectivity_failed",
                         audit["candidates"][1]["rejection_reason"])
        self.assertEqual([1], [op["score"] for op in selected["ops"]])

    def test_fewer_new_drv_ranks_first(self):
        audit, _selected = self._evaluate(
            [(1, 1), (2, 1)],
            {1: Counter({("R", (0, 0, 1, 1)): 4}),
             2: Counter({("R", (0, 0, 1, 1)): 2,
                         ("NEW", (1, 1, 2, 2)): 2})})
        self.assertEqual(0, audit["selected_candidate"])
        self.assertEqual(2, audit["candidates"][1]["new_drv"])

    def test_larger_net_improvement_wins(self):
        audit, selected = self._evaluate(
            [(1, 1), (2, 1)],
            {1: Counter({("R", (0, 0, 1, 1)): 4}),
             2: Counter({("R", (0, 0, 1, 1)): 2})})
        self.assertEqual(1, audit["selected_candidate"])
        self.assertEqual(1, selected["candidate_index"])

    def test_identical_scores_prefer_lower_candidate_number(self):
        same = Counter({("R", (0, 0, 1, 1)): 4})
        audit, _selected = self._evaluate(
            [(1, 1), (2, 1)], {1: same, 2: same})
        self.assertEqual(0, audit["selected_candidate"])

    def test_all_extra_measurements_fail_falls_back_to_candidate_zero(self):
        audit, selected = self._evaluate(
            [(1, 1), (2, 1)], {}, baseline_error=True)
        self.assertEqual(0, audit["selected_candidate"])
        self.assertEqual("candidate_0_legal_fallback",
                         audit["selection_reason"])
        self.assertEqual(0, selected["candidate_index"])

    def test_all_candidates_invalid_applies_empty_patch(self):
        audit, selected = self._evaluate(
            [(1, 1), (2, 1)], {}, baseline_error=True,
            validate_by_score={1: False, 2: False})
        self.assertIsNone(audit["selected_candidate"])
        self.assertEqual([], selected["ops"])

    def test_4a_rolls_back_even_after_local_candidate_selection(self):
        state = BestValidState("initial.py", "initial.json", 765)
        for iteration, total in enumerate([627, 609, 598, 604], start=1):
            state.consider(iteration, "i{0}.py".format(iteration),
                           "i{0}.json".format(iteration),
                           {"end_violations": total,
                            "connectivity": "preserved"}, True)
        state.consider(5, "selected-candidate.py", "broken.json",
                       {"end_violations": 590,
                        "connectivity": "broken"}, True)
        self.assertEqual(4, state.current.iteration)
        self.assertEqual(604, state.current.drc_total)
        self.assertEqual(3, state.best.iteration)
        self.assertEqual(598, state.best.drc_total)

    def test_config_requires_4a_and_clamps_hard_limits(self):
        with mock.patch.object(conf, "conf_path",
                               return_value="/definitely/missing.conf"):
            with mock.patch.dict(os.environ, {}, clear=True):
                disabled = conf.resolve(env_writeback=False)
            self.assertFalse(disabled.limited_multi_candidate)
            self.assertEqual(2, disabled.multi_candidate_count)

            with mock.patch.dict(os.environ, {
                    "EVODRC_ENABLE_LIMITED_MULTI_CANDIDATE": "1",
                    "EVODRC_ENABLE_BEST_VALID_ROLLBACK": "0"}, clear=True):
                with self.assertRaises(RuntimeError):
                    conf.resolve(env_writeback=False)

            with mock.patch.dict(os.environ, {
                    "EVODRC_ENABLE_LIMITED_MULTI_CANDIDATE": "1",
                    "EVODRC_ENABLE_BEST_VALID_ROLLBACK": "1",
                    "EVODRC_MULTI_CANDIDATE_COUNT": "999",
                    "EVODRC_MULTI_CANDIDATE_MAX_UNITS_PER_ITER": "999",
                    "EVODRC_MULTI_CANDIDATE_MAX_EXTRA_CALLS_PER_ITER":
                        "999"}, clear=True):
                cfg = conf.resolve(env_writeback=False)
        self.assertEqual(3, cfg.multi_candidate_count)
        self.assertEqual(2, cfg.multi_candidate_max_units)
        self.assertEqual(4, cfg.multi_candidate_max_extra_calls)


if __name__ == "__main__":
    unittest.main()
