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

"""Pure state policy for optional best-valid selection and rollback.

The controller owns artifact creation.  This module only decides whether a
measured attempt is a complete, connectivity-preserving state, then tracks two
independent snapshots:

* ``current`` is the most recent valid state and is allowed to get worse in
  DRC count.  It is the next iteration's input.
* ``best`` is the lowest-DRV valid state and is used only for final selection.

Keeping this policy free of KLayout, model calls and filesystem writes makes
the critical transition rules directly unit-testable.
"""


class ValidSnapshot(object):
    """The artifacts and measurement needed to resume or select one state."""

    __slots__ = ("layout_path", "drc_path", "iteration", "drc_total",
                 "result", "connectivity")

    def __init__(self, layout_path, drc_path, iteration, drc_total, result,
                 connectivity):
        self.layout_path = layout_path
        self.drc_path = drc_path
        self.iteration = iteration
        self.drc_total = drc_total
        self.result = dict(result or {})
        self.connectivity = connectivity

    def as_dict(self):
        return {
            "layout_path": self.layout_path,
            "drc_path": self.drc_path,
            "iteration": self.iteration,
            "drc_total": self.drc_total,
            "connectivity": self.connectivity,
        }


class BestValidState(object):
    """Track current-valid and best-valid snapshots for one iterative run."""

    def __init__(self, initial_layout, initial_drc, initial_total):
        initial_result = {
            "iter": 0,
            "end_violations": initial_total,
            "drc_total_after": initial_total,
            "connectivity": "preserved",
            "note": "initial input fallback",
        }
        initial = ValidSnapshot(
            initial_layout, initial_drc, 0, initial_total, initial_result,
            "preserved")
        self.current = initial
        self.best = initial
        self.attempts = []

    @staticmethod
    def _rejection_reason(result, artifacts_complete, evaluation_error=None):
        if evaluation_error:
            return "evaluation_exception"
        if not artifacts_complete:
            return "incomplete_evaluation_artifacts"
        if (result or {}).get("connectivity") != "preserved":
            return "connectivity_not_preserved"
        end_total = (result or {}).get("end_violations")
        if not isinstance(end_total, int) or isinstance(end_total, bool):
            return "missing_drc_total"
        return ""

    def consider(self, iteration, layout_path, drc_path, result,
                 artifacts_complete, evaluation_error=None):
        """Record one attempt and return its deterministic transition audit.

        A valid attempt always becomes ``current``, even when its DRC count is
        higher.  ``best`` changes only on a strict DRC improvement; ties retain
        the earlier snapshot, which is the stable tie-break rule.
        """
        reason = self._rejection_reason(
            result, artifacts_complete, evaluation_error=evaluation_error)
        accepted = not reason
        best_updated = False
        attempt_total = (result or {}).get("end_violations")

        if accepted:
            snapshot = ValidSnapshot(
                layout_path, drc_path, iteration, attempt_total, result,
                "preserved")
            self.current = snapshot
            if (self.best.drc_total is None
                    or attempt_total < self.best.drc_total):
                self.best = snapshot
                best_updated = True

        audit = {
            "iteration": iteration,
            "attempt_accepted": accepted,
            "attempt_rejection_reason": reason or None,
            "attempt_drc": attempt_total,
            "attempt_connectivity": (result or {}).get("connectivity"),
            "current_valid_iteration": self.current.iteration,
            "current_valid_drc": self.current.drc_total,
            "best_valid_iteration": self.best.iteration,
            "best_valid_drc": self.best.drc_total,
            "best_updated": best_updated,
        }
        self.attempts.append(dict(audit))
        return audit

    def summary(self, output_emitted=None):
        return {
            "enabled": True,
            "tie_break": "earlier valid iteration wins when DRC totals tie",
            "last_valid_iteration": self.current.iteration,
            "last_valid_drc": self.current.drc_total,
            "best_iteration": self.best.iteration,
            "best_drc": self.best.drc_total,
            "final_selected_iteration": self.best.iteration,
            "final_selected_drc": self.best.drc_total,
            "output_emitted": output_emitted,
            "attempts": list(self.attempts),
        }


def select_final_layout(enabled, state, legacy_last_good):
    """Select the final layout without changing the disabled legacy policy."""
    if enabled and state is not None:
        return state.best.layout_path
    return legacy_last_good
