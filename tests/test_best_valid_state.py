import os
import unittest
from unittest import mock

from agent.src.iter import conf
from agent.src.iter import evolve
from agent.src.iter.state_policy import BestValidState, select_final_layout


def _result(iteration, total, connectivity="preserved"):
    return {
        "iter": iteration,
        "end_violations": total,
        "drc_total_after": total,
        "connectivity": connectivity,
        "drc_rendered": True,
    }


class BestValidStateTest(unittest.TestCase):
    def _state(self):
        return BestValidState("initial.py", "initial.drc.json", 765)

    def _accept(self, state, iteration, total, connectivity="preserved",
                complete=True):
        return state.consider(
            iteration, "iter{0}.py".format(iteration),
            "iter{0}.drc.json".format(iteration),
            _result(iteration, total, connectivity), complete)

    def test_valid_regressions_advance_current_but_not_best(self):
        state = self._state()
        for iteration, total in enumerate(
                [627, 609, 598, 603, 602, 604], start=1):
            self._accept(state, iteration, total)

        self.assertEqual(6, state.current.iteration)
        self.assertEqual(604, state.current.drc_total)
        self.assertEqual(3, state.best.iteration)
        self.assertEqual(598, state.best.drc_total)
        self.assertEqual("iter3.py", select_final_layout(True, state, None))

    def test_connectivity_failure_rolls_back_next_input(self):
        state = self._state()
        for iteration, total in enumerate(
                [627, 609, 598, 603, 602, 604], start=1):
            self._accept(state, iteration, total)
        audit = self._accept(state, 7, 614, connectivity="broken")

        self.assertFalse(audit["attempt_accepted"])
        self.assertEqual("connectivity_not_preserved",
                         audit["attempt_rejection_reason"])
        self.assertEqual("iter6.py", state.current.layout_path)
        self.assertEqual("iter6.drc.json", state.current.drc_path)
        self.assertEqual(604, state.current.drc_total)
        self.assertEqual(598, state.best.drc_total)

    def test_valid_recovery_after_failure_can_become_new_best(self):
        state = self._state()
        for iteration, total in enumerate(
                [627, 609, 598, 603, 602, 604], start=1):
            self._accept(state, iteration, total)
        self._accept(state, 7, 614, connectivity="broken")
        audit = self._accept(state, 8, 590)

        self.assertTrue(audit["attempt_accepted"])
        self.assertEqual(8, state.current.iteration)
        self.assertEqual(590, state.current.drc_total)
        self.assertEqual(8, state.best.iteration)
        self.assertEqual(590, state.best.drc_total)

    def test_equal_drc_keeps_earlier_best(self):
        state = self._state()
        first = self._accept(state, 1, 600)
        tied = self._accept(state, 2, 600)

        self.assertTrue(first["best_updated"])
        self.assertFalse(tied["best_updated"])
        self.assertEqual(2, state.current.iteration)
        self.assertEqual(1, state.best.iteration)

    def test_first_attempt_failure_falls_back_to_initial(self):
        state = self._state()
        audit = self._accept(state, 1, 800, connectivity="broken")

        self.assertFalse(audit["attempt_accepted"])
        self.assertEqual(0, state.current.iteration)
        self.assertEqual(0, state.best.iteration)
        self.assertEqual("initial.py",
                         select_final_layout(True, state, "legacy.py"))

    def test_incomplete_evaluation_is_rejected(self):
        state = self._state()
        audit = self._accept(state, 1, 600, complete=False)

        self.assertFalse(audit["attempt_accepted"])
        self.assertEqual("incomplete_evaluation_artifacts",
                         audit["attempt_rejection_reason"])
        self.assertEqual(0, state.current.iteration)

    def test_evaluation_exception_is_rejected(self):
        state = self._state()
        audit = state.consider(
            1, "iter1.py", None,
            _result(1, None, connectivity="unknown"), False,
            evaluation_error="RuntimeError")

        self.assertFalse(audit["attempt_accepted"])
        self.assertEqual("evaluation_exception",
                         audit["attempt_rejection_reason"])
        self.assertEqual(0, state.current.iteration)

    def test_disabled_selection_preserves_legacy_last_good(self):
        state = self._state()
        self._accept(state, 1, 598)

        self.assertEqual(
            "legacy-604.py",
            select_final_layout(False, state, "legacy-604.py"))

    def test_config_switch_defaults_off_and_can_be_enabled(self):
        relevant = dict(os.environ)
        relevant.pop("EVODRC_ENABLE_BEST_VALID_ROLLBACK", None)
        with mock.patch.dict(os.environ, relevant, clear=True), \
                mock.patch.object(conf, "conf_path",
                                  return_value="/definitely/missing.conf"):
            self.assertFalse(conf.resolve(env_writeback=False)
                             .best_valid_rollback)

        with mock.patch.dict(
                os.environ,
                {"EVODRC_ENABLE_BEST_VALID_ROLLBACK": "1"}, clear=True), \
                mock.patch.object(conf, "conf_path",
                                  return_value="/definitely/missing.conf"):
            self.assertTrue(conf.resolve(env_writeback=False)
                            .best_valid_rollback)

    def test_rejected_attempt_is_audited_but_not_learned(self):
        summary = {
            "n_records": 1,
            "touched_layers": ["M1"],
            "n_unmapped": 0,
            "n_appended": {},
            "ledger_path": "ledger_summary.json",
        }
        cfg = mock.Mock(evolution=conf.EVOLUTION_ON)
        with mock.patch.object(evolve.layerdb, "load_deck_map",
                               return_value={}), \
                mock.patch.object(evolve, "build_records",
                                  return_value=summary) as build, \
                mock.patch.object(evolve, "_write_audit") as write_audit:
            returned = evolve.update_all(
                "/tmp/iter7", 7, "Block7", mock.Mock(), ["leaf_1"],
                ["leaf_1"], {"leaf_1": {"ops": []}},
                {"attempt_accepted": False,
                 "attempt_rejection_reason":
                     "connectivity_not_preserved"},
                cfg, work_dir="/tmp/work", accept_attempt=False)

        self.assertIs(returned, summary)
        self.assertFalse(build.call_args.kwargs["append"])
        self.assertEqual("attempt_rejected",
                         write_audit.call_args.kwargs["skipped"])


if __name__ == "__main__":
    unittest.main()
