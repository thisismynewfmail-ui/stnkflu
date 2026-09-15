"""Goals, outcomes and scoring.

A goal here is a plain statement of what the workflow is trying to achieve,
plus two conditions: what counts as getting it right, and what counts as
getting it wrong. Everything the interface shows about progress comes from
these, so they are recorded verbatim in the run log.

Scoring counts what happened. It does not adjust for chance, and a rising
score is not by itself evidence that anything was learned: a workflow that
always answers the same way will score well on a task where that answer is
usually right. The controls in ``docs/model.md`` are what settle that.
"""

import time

OUTCOMES = ("correct", "incorrect", "neutral")


class GoalTracker:
    """Running tally of attempts, outcomes and trials for one run."""

    def __init__(self):
        self.goal = ""
        self.success_rule = ""
        self.failure_rule = ""
        self.attempts = 0
        self.correct = 0
        self.incorrect = 0
        self.neutral = 0
        self.streak = 0
        self.best_streak = 0
        self.trial = 0
        self.history = []
        self.started_at = time.time()
        self.reached = False
        self.reached_at = None

    def define(self, goal, success_rule="", failure_rule=""):
        self.goal = str(goal)
        self.success_rule = str(success_rule)
        self.failure_rule = str(failure_rule)

    def record(self, outcome, iteration, detail=None):
        if outcome not in OUTCOMES:
            raise ValueError(f"Outcome must be one of {OUTCOMES}")
        if outcome == "neutral":
            self.neutral += 1
        else:
            self.attempts += 1
            if outcome == "correct":
                self.correct += 1
                self.streak += 1
                self.best_streak = max(self.best_streak, self.streak)
            else:
                self.incorrect += 1
                self.streak = 0
        entry = {
            "iteration": int(iteration),
            "trial": int(self.trial),
            "outcome": outcome,
            "at": time.time(),
            "detail": detail or {},
        }
        self.history.append(entry)
        del self.history[:-2000]
        return entry

    def begin_trial(self):
        self.trial += 1
        return self.trial

    def mark_reached(self):
        if not self.reached:
            self.reached = True
            self.reached_at = time.time()
        return self.reached

    @property
    def accuracy(self):
        return (self.correct / self.attempts) if self.attempts else 0.0

    def recent_accuracy(self, window=20):
        scored = [h for h in self.history if h["outcome"] != "neutral"][-int(window):]
        if not scored:
            return 0.0
        return sum(1 for h in scored if h["outcome"] == "correct") / len(scored)

    def summary(self):
        return {
            "goal": self.goal,
            "success_rule": self.success_rule,
            "failure_rule": self.failure_rule,
            "trial": self.trial,
            "attempts": self.attempts,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "neutral": self.neutral,
            "accuracy": round(self.accuracy, 4),
            "recent_accuracy": round(self.recent_accuracy(), 4),
            "streak": self.streak,
            "best_streak": self.best_streak,
            "goal_reached": self.reached,
            "seconds": round(time.time() - self.started_at, 2),
            "caveat": "Counts only. A score says nothing about whether the"
            " network learned; compare against a frozen-synapse run and a"
            " shuffled-teaching run before concluding anything.",
        }
