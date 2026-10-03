"""Real MongoDB + job backend + SDK + pinned gateway; only Arena HTTP is mocked.

Explicit TEST_MONGO_URL required (loopback, no credentials/database). Never falls
back to an application's database. Each test creates/drops its own DB and repo dir.
Run: TEST_MONGO_URL=mongodb://127.0.0.1:27018/ ./venv/bin/python backend/tests/test_gateway_pipeline.py
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import time
import unittest
import zipfile

import httpx
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gateway_fixture import MODEL
from integration_support import GatewayFixture, require_mongo, test_mongo_url
from isolated_server import IsolatedServer


class GatewayPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mongo_url = test_mongo_url()
        version = require_mongo(cls.mongo_url)
        print(f"Real MongoDB {version}; synthetic gateway responses only", flush=True)
        cls.gateway = GatewayFixture()
        cls.gateway.__enter__()
        cls.addClassCleanup(cls.gateway.__exit__, None, None, None)

    def setUp(self):
        self.gateway.control("/_test/reset", body={})
        self.srv = IsolatedServer(step_delay=0, extra_env={
            "MONGO_URL": self.mongo_url, "ARENA2API_URL": self.gateway.base,
            "ARENA2API_MODEL": MODEL, "ARENA2API_API_KEY": self.gateway.key,
            "ARENA_REQUEST_TIMEOUT_SECONDS": "30", "CORS_ORIGINS": "*",
        })
        self.base = self.srv.__enter__()
        self.addCleanup(self.cleanup_server)
        self.http = httpx.Client(base_url=self.base + "/", trust_env=False, timeout=15)
        self.addCleanup(self.http.close)

    def cleanup_server(self):
        name = self.srv.db_name
        data = Path(self.srv._data_dir.name)
        self.srv.__exit__(None, None, None)
        with MongoClient(self.mongo_url, serverSelectionTimeoutMS=3000) as client:
            self.assertNotIn(name, client.list_database_names(), "Throwaway database was not dropped")
        self.assertFalse(data.exists(), "Temporary repositories were not removed")

    def create(self, steps=2, **overrides):
        roadmap = "# Gateway pipeline\n\n" + "".join(f"### Step {i}\nBuild synthetic turn {i}.\n\n" for i in range(1, steps + 1))
        response = self.http.post("jobs", json={"roadmap_md": roadmap, **overrides})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["job_id"]

    def wait(self, jid, wanted="done", timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.http.get(f"jobs/{jid}").json()
            if job["status"] == wanted:
                return job
            if job["status"] in {"error", "stopped", "cancelled"} and wanted == "done":
                self.fail(f"Job ended as {job['status']}: {job.get('error')}")
            time.sleep(0.1)
        self.fail(f"Job did not reach {wanted}")

    def requests(self):
        return self.gateway.control("/_test/requests")["requests"]

    def test_two_steps_persist_history_artifacts_commits_and_zip(self):
        jid = self.create()
        job = self.wait(jid)
        self.assertEqual(job["steps_done"], 2)
        with self.srv.db().client as client:
            db = client[self.srv.db_name]
            self.assertEqual(db.jobs.find_one({"id": jid})["status"], "done")
            steps = list(db.steps.find({"job_id": jid}).sort("index", 1))
            self.assertEqual([s["status"] for s in steps], ["done", "done"])
            self.assertNotIn(self.gateway.key, json.dumps(steps, default=str))
            self.assertNotIn(self.gateway.key, json.dumps(db.jobs.find_one({"id": jid}), default=str))
        calls = self.requests()
        self.assertEqual([r["turn"] for r in calls], [1, 2])
        self.assertIn("<|assistant|>", calls[1]["prompt"])
        self.assertIn("pipeline turn 1", calls[1]["prompt"])
        transcript = self.http.get(f"jobs/{jid}/transcript").json()
        self.assertEqual(len(transcript["turns"]), 2)
        git = self.http.get(f"jobs/{jid}/git").json()
        self.assertEqual(git["commit_count"], 2)
        self.assertEqual([c["step_index"] for c in git["commits"]], [2, 1])
        download = self.http.get(f"jobs/{jid}/download")
        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"app/main.py", "README.md"})
            self.assertIn(b"pipeline turn 2", archive.read("app/main.py"))
        self.assertNotIn(self.gateway.key, self.http.get("settings").text)

    def test_rate_limit_fails_fast_and_resume_rebuilds_history(self):
        self.gateway.control("/_test/reset", body={"fail_turn": 2})
        jid = self.create(3)
        job = self.wait(jid, "error")
        self.assertEqual([s["status"] for s in job["steps"]], ["done", "error", "pending"])
        self.assertIn("429", job["error"])
        self.assertEqual(len(self.requests()), 2, "SDK must not retry automatically")
        first_sha = job["steps"][0]["commit_sha"]
        response = self.http.post(f"jobs/{jid}/resume")
        self.assertEqual(response.status_code, 200, response.text)
        done = self.wait(jid)
        self.assertEqual(done["steps_done"], 3)
        self.assertEqual(done["steps"][0]["commit_sha"], first_sha)
        calls = self.requests()
        self.assertEqual([r["turn"] for r in calls], [1, 2, 2, 3])
        self.assertIn("pipeline turn 1", calls[2]["prompt"])
        self.assertEqual(self.http.get(f"jobs/{jid}/git").json()["commit_count"], 3)

    def test_disconnected_503_then_resume(self):
        self.gateway.control("/_test/reset", body={"active": False})
        jid = self.create()
        job = self.wait(jid, "error")
        self.assertIn("503", job["error"])
        self.assertEqual([s["status"] for s in job["steps"]], ["error", "pending"])
        self.assertEqual(self.requests(), [])
        self.gateway.control("/_test/reset", body={})
        self.assertEqual(self.http.post(f"jobs/{jid}/resume").status_code, 200)
        self.assertEqual(self.wait(jid)["steps_done"], 2)

    def test_url_alias_gets_no_server_key(self):
        alias = self.gateway.base.replace("127.0.0.1", "localhost")
        jid = self.create(arena_url=alias)
        job = self.wait(jid, "error")
        self.assertIn("401", job["error"])
        self.assertNotIn(self.gateway.key, json.dumps(job))
        self.assertEqual(self.requests(), [])

    def test_stop_abandons_inflight_turn_then_resume(self):
        self.gateway.control("/_test/reset", body={"delay": 1})
        jid = self.create()
        deadline = time.monotonic() + 10
        while not self.requests():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.assertEqual(self.http.post(f"jobs/{jid}/stop").status_code, 200)
        stopped = self.wait(jid, "stopped")
        self.assertEqual(stopped["steps_done"], 0)
        self.gateway.control("/_test/reset", body={})
        self.assertEqual(self.http.post(f"jobs/{jid}/resume").status_code, 200)
        self.assertEqual(self.wait(jid)["steps_done"], 2)

    def test_queue_pause_unpause_with_real_gateway(self):
        self.gateway.control("/_test/reset", body={"delay": 1})
        first = self.create(2)
        second = self.create(1)
        third = self.create(1)
        self.assertEqual(self.http.post(f"queue/{third}/pause").status_code, 200)
        self.wait(first)
        self.wait(second)
        self.assertEqual(self.http.get(f"jobs/{third}").json()["status"], "paused")
        self.assertEqual(self.http.post(f"queue/{third}/unpause").status_code, 200)
        self.wait(third)
        self.assertEqual(len(self.requests()), 4)

    def test_delete_removes_persisted_job_steps_and_repository(self):
        jid = self.create()
        self.wait(jid)
        repo = Path(self.srv._data_dir.name, "repos", jid)
        self.assertTrue(repo.exists())
        response = self.http.delete(f"jobs/{jid}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.http.get(f"jobs/{jid}").status_code, 404)
        self.assertFalse(repo.exists())
        with self.srv.db().client as client:
            db = client[self.srv.db_name]
            self.assertEqual(db.jobs.count_documents({"id": jid}), 0)
            self.assertEqual(db.steps.count_documents({"job_id": jid}), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
