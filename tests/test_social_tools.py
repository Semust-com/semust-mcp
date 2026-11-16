"""Social tools against a fake Semust API on localhost — never the real one.

Run from services/mcp (any Python with requirements.txt installed):

    python -m unittest discover -s tests -v

Checks what the Python layer owns: method, path, query and body per tool; the API key
and version headers; that dry_run defaults to true; that a partial update sends only the
fields given; that ids are checked before they reach a URL; that API errors come back as
"<code>: <message>"; and the tool annotations the client shows the user.
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


class _FakeAPI(BaseHTTPRequestHandler):
    calls = []
    reply = (200, {"ok": True})

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        _FakeAPI.calls.append({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(raw) if raw else None,
        })
        status, payload = _FakeAPI.reply
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _handle

    def log_message(self, *args):
        pass


_server = HTTPServer(("127.0.0.1", 0), _FakeAPI)
threading.Thread(target=_server.serve_forever, daemon=True).start()

# Before the import: the module reads these once (load_dotenv never overrides them).
os.environ["SEMUST_API_KEY"] = "smk_test_key"
os.environ["SEMUST_BASE_URL"] = f"http://127.0.0.1:{_server.server_port}/v1/mcp"

import semust_mcp as m  # noqa: E402

SOCIAL_TOOLS = {
    "social_get_accounts", "social_list_posts", "social_get_post", "social_create_post",
    "social_update_post", "social_delete_post", "social_retry_post", "social_import_media",
}


def _last():
    return _FakeAPI.calls[-1]


class SocialToolsTest(unittest.TestCase):
    def setUp(self):
        _FakeAPI.calls.clear()
        _FakeAPI.reply = (200, {"ok": True})

    def test_registers_82_tools_with_social_annotations(self):
        tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
        self.assertEqual(len(tools), 82)
        self.assertTrue(SOCIAL_TOOLS <= set(tools))

        # What the client receives: the camelCase wire names (mcp 2.x stores them as
        # read_only_hint etc. with camelCase aliases; 1.x stores the camelCase names).
        def hints(name):
            return tools[name].annotations.model_dump(by_alias=True)

        reads = {"social_get_accounts", "social_list_posts", "social_get_post"}
        for name in reads:
            self.assertTrue(hints(name)["readOnlyHint"], name)
        for name in SOCIAL_TOOLS - reads:
            self.assertFalse(hints(name)["readOnlyHint"], name)
        self.assertTrue(hints("social_delete_post")["destructiveHint"])
        self.assertTrue(hints("social_update_post")["destructiveHint"])
        self.assertTrue(hints("social_create_post")["openWorldHint"])
        # Every tool before this release has no annotations — unchanged.
        self.assertIsNone(tools["get_keywords"].annotations)

    def test_every_request_carries_key_and_version(self):
        m.social_get_accounts("p1")
        h = _last()["headers"]
        self.assertEqual(h.get("X-API-Key"), "smk_test_key")
        self.assertEqual(h.get("X-Semust-MCP-Version"), m.__version__)
        m.list_projects()
        self.assertEqual(_last()["headers"].get("X-Semust-MCP-Version"), m.__version__)

    def test_reads(self):
        m.social_get_accounts("p1")
        self.assertEqual((_last()["method"], _last()["path"]), ("GET", "/v1/mcp/projects/p1/social/accounts"))

        m.social_list_posts("p1", status="failed,partially_published", platforms="linkedin",
                            from_time="2026-11-01T00:00:00+03:00")
        call = _last()
        self.assertEqual(call["method"], "GET")
        self.assertTrue(call["path"].startswith("/v1/mcp/projects/p1/social/posts?"))
        self.assertIn("status=failed%2Cpartially_published", call["path"])
        self.assertIn("platforms=linkedin", call["path"])
        self.assertIn("from=2026-11-01T00%3A00%3A00%2B03%3A00", call["path"])
        self.assertIn("limit=20", call["path"])
        self.assertNotIn("search=", call["path"])  # empty filters are not sent
        self.assertNotIn("to=", call["path"])

        m.social_get_post("p1", "a1b2-c3")
        self.assertEqual(_last()["path"], "/v1/mcp/projects/p1/social/posts/a1b2-c3")

    def test_create_is_a_dry_run_unless_told_otherwise(self):
        m.social_create_post("p1", "Merhaba", ["dst_A"])
        call = _last()
        self.assertEqual((call["method"], call["path"]), ("POST", "/v1/mcp/projects/p1/social/posts"))
        self.assertEqual(call["body"], {"body": "Merhaba", "destination_ids": ["dst_A"], "mode": "draft", "dry_run": True})

        _FakeAPI.reply = (201, {"post_id": "x1", "status": "scheduled", "warnings": []})
        out = m.social_create_post(
            "p1", "Merhaba", ["dst_A", "dst_B"], mode="schedule", publish_at="2026-11-14T10:00:00+03:00",
            media_urls=["https://cdn.semust.com/social-posts/p1/a.jpg"], link="https://semust.com",
            caption_overrides={"dst_B": "Kısa"}, dry_run=False,
        )
        self.assertEqual(json.loads(out)["post_id"], "x1")  # 201 is a success
        self.assertEqual(_last()["body"], {
            "body": "Merhaba", "destination_ids": ["dst_A", "dst_B"], "mode": "schedule", "dry_run": False,
            "publish_at": "2026-11-14T10:00:00+03:00", "media_urls": ["https://cdn.semust.com/social-posts/p1/a.jpg"],
            "link": "https://semust.com", "caption_overrides": {"dst_B": "Kısa"},
        })

    def test_update_sends_only_given_fields(self):
        m.social_update_post("p1", "x1", body="Düzeltildi")
        call = _last()
        self.assertEqual((call["method"], call["path"]), ("PATCH", "/v1/mcp/projects/p1/social/posts/x1"))
        self.assertEqual(call["body"], {"body": "Düzeltildi"})

        m.social_update_post("p1", "x1", media_urls=[], link="")  # clear both
        self.assertEqual(_last()["body"], {"media_urls": [], "link": ""})

        m.social_update_post("p1", "x1", publish_at="2026-11-15T09:00:00+03:00", destination_ids=["dst_A"],
                             caption_overrides={"dst_A": "x"})
        self.assertEqual(_last()["body"], {"publish_at": "2026-11-15T09:00:00+03:00", "destination_ids": ["dst_A"],
                                           "caption_overrides": {"dst_A": "x"}})

        before = len(_FakeAPI.calls)
        with self.assertRaises(m.SemustAPIError):
            m.social_update_post("p1", "x1")
        self.assertEqual(len(_FakeAPI.calls), before)  # nothing sent

    def test_delete_retry_import(self):
        m.social_delete_post("p1", "x1")
        self.assertEqual((_last()["method"], _last()["path"]), ("DELETE", "/v1/mcp/projects/p1/social/posts/x1"))
        m.social_retry_post("p1", "x1")
        self.assertEqual((_last()["method"], _last()["path"]), ("POST", "/v1/mcp/projects/p1/social/posts/x1/retry"))
        m.social_import_media("p1", "https://example.com/a.jpg")
        call = _last()
        self.assertEqual((call["method"], call["path"]), ("POST", "/v1/mcp/projects/p1/social/media"))
        self.assertEqual(call["body"], {"source_url": "https://example.com/a.jpg"})

    def test_ids_are_checked_before_any_request(self):
        for bad in ("../x", "x/../../projects", "a b", "", "x?y=1"):
            with self.assertRaises(m.SemustAPIError):
                m.social_get_post("p1", bad)
            with self.assertRaises(m.SemustAPIError):
                m.social_delete_post("p1", bad)
            with self.assertRaises(m.SemustAPIError):
                m.social_get_accounts(bad)
        self.assertEqual(_FakeAPI.calls, [])

    def test_errors_carry_the_code(self):
        _FakeAPI.reply = (403, {"error": "write_scope_required", "message": "This API key cannot create or change posts.", "isError": True})
        with self.assertRaises(m.SemustAPIError) as ctx:
            m.social_create_post("p1", "x", ["dst_A"], dry_run=False)
        self.assertEqual(str(ctx.exception), "write_scope_required: This API key cannot create or change posts.")

        _FakeAPI.reply = (500, {"message": "boom"})
        with self.assertRaises(m.SemustAPIError) as ctx:
            m.social_get_accounts("p1")
        self.assertEqual(str(ctx.exception), "boom")


if __name__ == "__main__":
    unittest.main()
