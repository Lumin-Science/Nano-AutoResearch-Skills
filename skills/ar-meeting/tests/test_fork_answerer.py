#!/usr/bin/env python3

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import labmeet  # noqa: E402


class ForkAnswererTests(unittest.TestCase):
    def test_one_persisted_fork_answers_questions_and_followups(self):
        with tempfile.TemporaryDirectory(prefix="ar-meeting-fork-") as root:
            meeting = Path(root) / "meeting" / "demo"
            meeting.mkdir(parents=True)
            (meeting / "slides.json").write_text(json.dumps([
                {"n": 1, "topic": "Result", "text": "The model improved by 12 percent."}
            ]), encoding="utf-8")
            (meeting / "pack.md").write_text("# Claim ledger\n\nSlide 1 -> results.json\n",
                                             encoding="utf-8")
            store = labmeet.Store(str(meeting))
            store.capture_parent_thread("parent-task-123456")
            cfg = dict(labmeet.DEFAULTS)
            cfg.update({
                "answer_mode": "fork",
                "app_server_cmd": [sys.executable, str(SKILL / "tests" / "fake_app_server.py")],
                "fallback_to_exec": False,
                "answer_timeout_s": 5,
                "project_dir": root,
            })
            answerer = labmeet.Answerer(store, cfg, store.errlog)
            try:
                catalog = answerer.models()
                self.assertEqual([row["id"] for row in catalog],
                                 ["gpt-test-sol", "gpt-test-terra"])
                self.assertEqual(catalog[0]["supportedReasoningEfforts"],
                                 ["low", "medium", "high"])

                thread, _ = store.new_thread(1, 35, 45,
                    "What supports the 12 percent claim?",
                    module={"id": "s1.e1", "kind": "equation",
                            "label": "equation: accuracy = 0.82",
                            "text": "accuracy = 0.82"},
                    model="gpt-test-sol", effort="high")
                self.assertEqual(thread["module"]["id"], "s1.e1")
                self.assertIn("Selected block text:",
                              labmeet.build_prompt(str(meeting), thread, cfg,
                                                   project_root=root))
                answerer.kick(thread["id"])
                self._wait_for_answer(store, thread["id"], 2)
                first_state = store.agent_state()
                self.assertEqual(first_state["parent_thread_id"], "parent-task-123456")
                self.assertEqual(first_state["review_threads"]["t1"]["thread_id"],
                                 "fork-child-1")
                self.assertEqual(first_state["review_threads"]["t1"]["forked_from_id"],
                                 "parent-task-123456")
                self.assertIn("[gpt-test-sol/high]",
                              store.threads[thread["id"]]["messages"][-1]["text"])

                store.add_message(thread["id"], "user", "And is that held out?",
                                  model="gpt-test-terra", effort="low")
                answerer.kick(thread["id"])
                self._wait_for_answer(store, thread["id"], 4)
                messages = store.threads[thread["id"]]["messages"]
                self.assertEqual([item["role"] for item in messages],
                                 ["user", "agent", "user", "agent"])
                self.assertIn("Forked answer 2", messages[-1]["text"])
                self.assertIn("[gpt-test-terra/low]", messages[-1]["text"])
                self.assertEqual(store.agent_state()["review_threads"]["t1"]["thread_id"],
                                 "fork-child-1")

                second, _ = store.new_thread(1, 75, 20, "Is the plot calibrated?",
                                             model="gpt-test-sol", effort="medium")
                answerer.kick(second["id"])
                self._wait_for_answer(store, second["id"], 2)
                self.assertEqual(store.agent_state()["review_threads"]["t2"]["thread_id"],
                                 "fork-child-2")

                md_path, json_path, payload = labmeet.write_feedback_export(store, cfg)
                self.assertTrue(Path(md_path).is_file())
                self.assertTrue(Path(json_path).is_file())
                self.assertEqual(len(payload["threads"]), 2)
                exported = json.loads(Path(json_path).read_text(encoding="utf-8"))
                self.assertEqual(exported["threads"][0]["messages"][0]["model"],
                                 "gpt-test-sol")
                self.assertIn("Anchor: `x=35.00%`, `y=45.00%`",
                              Path(md_path).read_text(encoding="utf-8"))
                self.assertIn("equation: accuracy = 0.82",
                              Path(md_path).read_text(encoding="utf-8"))

                replayed = labmeet.Store(str(meeting))
                self.assertEqual(len(replayed.snapshot()), 2)
                self.assertEqual(replayed.threads["t1"]["messages"][2]["effort"], "low")
            finally:
                answerer.close()

    def _wait_for_answer(self, store, thread_id, count):
        deadline = time.time() + 8
        while time.time() < deadline:
            messages = store.threads[thread_id]["messages"]
            if len(messages) >= count and messages[-1]["role"] == "agent" and \
                    messages[-1]["status"] == "done":
                return
            time.sleep(0.05)
        self.fail("answer did not finish: %r" % store.threads[thread_id]["messages"])


if __name__ == "__main__":
    unittest.main()
