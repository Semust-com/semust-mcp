"""Content-group tools against a fake Semust API on localhost — never the real one.

Run from services/mcp (any Python with requirements.txt installed):

    python -m unittest discover -s tests -v

Checks what the Python layer owns: method, path, query and body per tool; repeated
`pattern` query parameters for the preview; that a partial update sends only the fields
given; that ids are checked before they reach a URL; the preview's URL-length guard; the
error format; and the tool annotations the client uses for its permission prompts.
"""

import asyncio
import importlib.util
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "semust_mcp.py")


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


def _load_server():
    """A private copy of the module pointed at this file's fake API (test_social_tools.py
    imports its own copy against its own fake server)."""
    os.environ["SEMUST_API_KEY"] = "smk_test_key"
    os.environ["SEMUST_BASE_URL"] = f"http://127.0.0.1:{_server.server_port}/v1/mcp"
    spec = importlib.util.spec_from_file_location("semust_mcp_content_groups", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load_server()

CG_TOOLS = {
    "content_group_list", "content_group_get", "content_group_suggest", "content_group_preview",
    "content_group_create", "content_group_update", "content_group_delete",
}
BASE = "/v1/mcp/projects/p1/search-console/content-groups"


def _last():
    return _FakeAPI.calls[-1]


def _split(path):
    u = urlsplit(path)
    return u.path, parse_qs(u.query)


class ContentGroupToolsTest(unittest.TestCase):
    def setUp(self):
        _FakeAPI.calls.clear()
        _FakeAPI.reply = (200, {"ok": True})

    def test_registered_with_annotations_in_the_gsc_family(self):
        tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
        self.assertTrue(CG_TOOLS <= set(tools))

        def hints(name):
            return tools[name].annotations.model_dump(by_alias=True)

        reads = {"content_group_list", "content_group_get", "content_group_suggest", "content_group_preview"}
        for name in reads:
            self.assertTrue(hints(name)["readOnlyHint"], name)
        for name in CG_TOOLS - reads:
            self.assertFalse(hints(name)["readOnlyHint"], name)
        self.assertFalse(hints("content_group_create")["destructiveHint"])
        self.assertTrue(hints("content_group_update")["destructiveHint"])
        self.assertTrue(hints("content_group_delete")["destructiveHint"])
        for name in CG_TOOLS:
            self.assertFalse(hints(name)["openWorldHint"], name)  # Semust-internal, nothing public
            self.assertEqual(m.tool_family(name), "gsc", name)

    def test_reads(self):
        m.content_group_list("p1")
        self.assertEqual((_last()["method"], _last()["path"]), ("GET", BASE))
        self.assertEqual(_last()["headers"].get("X-API-Key"), "smk_test_key")
        self.assertEqual(_last()["headers"].get("X-Semust-MCP-Version"), m.__version__)

        m.content_group_get("p1", "42", "2026-10-22", "2026-11-19")
        path, q = _split(_last()["path"])
        self.assertEqual(path, BASE + "/42")
        self.assertEqual(q, {"start_date": ["2026-10-22"], "end_date": ["2026-11-19"], "limit": ["25"]})

        m.content_group_get("p1", 42, "2026-10-22", "2026-11-19", limit=100)  # an int id works too
        self.assertEqual(_split(_last()["path"])[0], BASE + "/42")

        m.content_group_suggest("p1", "2026-10-22", "2026-11-19")
        path, q = _split(_last()["path"])
        self.assertEqual((path, q["start_date"], q["end_date"]), (BASE + "/suggest", ["2026-10-22"], ["2026-11-19"]))

    def test_preview_sends_each_pattern(self):
        m.content_group_preview("p1", "query", ["ankara avukat", "sigorta avukatı", "c#"], "2026-10-22", "2026-11-19")
        call = _last()
        path, q = _split(call["path"])
        self.assertEqual((call["method"], path), ("GET", BASE + "/preview"))
        self.assertEqual(q["pattern"], ["ankara avukat", "sigorta avukatı", "c#"])
        self.assertEqual(q["type"], ["query"])
        self.assertIsNone(call["body"])

        m.content_group_preview("p1", "page", "/blog/", "2026-10-22", "2026-11-19")  # a bare string is one pattern
        self.assertEqual(_split(_last()["path"])[1]["pattern"], ["/blog/"])

    def test_preview_refuses_a_url_too_long(self):
        long = ["ğ" * 150 + str(i) for i in range(30)]  # percent-encoding makes each ~1.8 KB
        with self.assertRaises(m.SemustAPIError) as ctx:
            m.content_group_preview("p1", "query", long, "2026-10-22", "2026-11-19")
        self.assertIn("two halves", str(ctx.exception))
        self.assertEqual(_FakeAPI.calls, [])

    def test_writes(self):
        _FakeAPI.reply = (201, {"status": "created"})
        m.content_group_create("p1", "blog", "page", ["/blog/", "/rehber/"])
        call = _last()
        self.assertEqual((call["method"], call["path"]), ("POST", BASE))
        self.assertEqual(call["body"], {"name": "blog", "type": "page", "patterns": ["/blog/", "/rehber/"]})

        _FakeAPI.reply = (200, {"status": "updated"})
        m.content_group_update("p1", "42", patterns=["/blog/"])
        call = _last()
        self.assertEqual((call["method"], call["path"]), ("PATCH", BASE + "/42"))
        self.assertEqual(call["body"], {"patterns": ["/blog/"]})  # name not sent

        m.content_group_update("p1", "42", name="blog yazıları")
        self.assertEqual(_last()["body"], {"name": "blog yazıları"})

        m.content_group_update("p1", "42", patterns=[])  # an empty list is sent; the API refuses it
        self.assertEqual(_last()["body"], {"patterns": []})

        _FakeAPI.calls.clear()
        with self.assertRaises(m.SemustAPIError):
            m.content_group_update("p1", "42")
        self.assertEqual(_FakeAPI.calls, [])

        _FakeAPI.reply = (200, {"status": "deleted"})
        m.content_group_delete("p1", "42")
        self.assertEqual((_last()["method"], _last()["path"]), ("DELETE", BASE + "/42"))

    def test_ids_are_checked_before_the_url(self):
        for bad in ("../1", "42/x", "0", "-3", "4 2", "", True, None, 1.5):
            with self.assertRaises(m.SemustAPIError, msg=repr(bad)):
                m.content_group_delete("p1", bad)
        with self.assertRaises(m.SemustAPIError):
            m.content_group_list("../p1")
        with self.assertRaises(m.SemustAPIError):
            m.content_group_create("p1", "x", "page", [1, 2])
        self.assertEqual(_FakeAPI.calls, [])

    def test_errors_come_back_as_code_and_message(self):
        _FakeAPI.reply = (409, {"error": "content_group_exists", "message": "This project already has a content group with that name.", "isError": True})
        with self.assertRaises(m.SemustAPIError) as ctx:
            m.content_group_create("p1", "blog", "page", ["/blog/"])
        self.assertTrue(str(ctx.exception).startswith("content_group_exists: "))


if __name__ == "__main__":
    unittest.main()
