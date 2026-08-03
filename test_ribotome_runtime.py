import tempfile
import unittest
from pathlib import Path

from ribotome_graph import Graph, Node, Port
from ribotome_runtime import RiboTomeRuntime


class DurableRuntimeTests(unittest.TestCase):
    def make_runtime(self, root, calls, fail_once=False):
        def first(v):
            calls.append("first")
            return {"draft": v["source"].upper()}

        def gate(v):
            calls.append("gate")
            return {"chosen": f"{v['draft']}:{v['choice']}"}

        failed = {"value": False}
        def last(v):
            calls.append("last")
            if fail_once and not failed["value"]:
                failed["value"] = True
                raise OSError("temporary worker failure")
            return {"result": v["chosen"] + "!"}

        graph = Graph([
            Node("first", {"source": Port("str")}, {"draft": Port("str")}, run=first),
            Node("gate", {"draft": Port("str"), "choice": Port("str")}, {"chosen": Port("str")}, ("first",), gate, decision=True, decision_inputs=("choice",)),
            Node("last", {"chosen": Port("str")}, {"result": Port("str")}, ("gate",), last),
        ])
        return RiboTomeRuntime(graph, root)

    def test_suspends_and_resumes_without_rerunning_accepted_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            runtime = self.make_runtime(tmp, calls)
            run = runtime.create("first", "last", {"source": "fact"})
            suspended = runtime.advance(run["id"])
            self.assertEqual(suspended["status"], "suspended")
            self.assertEqual(suspended["waiting"]["required_inputs"], ["choice"])
            self.assertEqual(calls, ["first"])

            # Reconstructing the runtime simulates a process restart.
            runtime = self.make_runtime(tmp, calls)
            completed = runtime.advance(run["id"], {"choice": "A"})
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["outputs"], {"result": "FACT:A!"})
            self.assertEqual(calls, ["first", "gate", "last"])
            self.assertEqual(len(runtime.artifacts(run["id"])), 3)
            self.assertTrue(any(e["kind"] == "run.suspended" for e in runtime.events(run["id"])))

    def test_failure_resume_retries_only_failed_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            runtime = self.make_runtime(tmp, calls, fail_once=True)
            run = runtime.create("first", "last", {"source": "fact", "choice": "B"})
            failed = runtime.advance(run["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["completed"], ["first", "gate"])
            completed = runtime.advance(run["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(calls, ["first", "gate", "last", "last"])

    def test_presupplied_decision_runs_uninterrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            runtime = self.make_runtime(tmp, calls)
            run = runtime.create("first", "last", {"source": "fact", "choice": "C"})
            done = runtime.advance(run["id"])
            self.assertEqual(done["status"], "completed")
            self.assertIsNone(done["waiting"])

    def test_human_worker_suspends_and_accepts_typed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Graph([
                Node("approval", outputs={"approved": Port("bool")}, human=True),
                Node("finish", {"approved": Port("bool")}, {"result": Port("str")}, ("approval",), lambda v: {"result": "yes" if v["approved"] else "no"}),
            ])
            runtime = RiboTomeRuntime(graph, tmp)
            run = runtime.create("approval", "finish")
            waiting = runtime.advance(run["id"])
            self.assertEqual(waiting["waiting"]["kind"], "human_decision")
            done = runtime.complete_human(run["id"], {"approved": True})
            self.assertEqual(done["outputs"], {"result": "yes"})


if __name__ == "__main__":
    unittest.main()
