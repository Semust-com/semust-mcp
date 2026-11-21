"""SEMUST_MCP_TOOLSETS: which tools the server exposes per family.

Run from services/mcp (any Python with requirements.txt installed):

    python -m unittest discover -s tests -v

No network: nothing here calls a tool, it only registers and removes them.
"""

import asyncio
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "semust_mcp.py")
os.environ.setdefault("SEMUST_API_KEY", "smk_test_key")


def fresh_server():
    """A new copy of the module, so removing tools in one test never leaks into another."""
    name = f"semust_mcp_toolsets_{fresh_server.n}"
    fresh_server.n += 1
    spec = importlib.util.spec_from_file_location(name, SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fresh_server.n = 0


def listed(module):
    """Tool names as an MCP client sees them (the list_tools call, not internals)."""
    return sorted(t.name for t in asyncio.run(module.mcp.list_tools()))


class ToolsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all_tools = listed(fresh_server())

    def test_every_tool_belongs_to_exactly_one_family(self):
        m = fresh_server()
        for name in self.all_tools:
            matches = [f for f, p in m.TOOLSET_PREFIXES.items() if name.startswith(p)]
            self.assertEqual(len(matches), 1, f"{name} matches {matches}")

    def test_unset_empty_and_all_keep_every_tool(self):
        for spec in (None, "", "  ", "all", "ALL"):
            m = fresh_server()
            self.assertEqual(m.apply_toolsets(m.mcp, spec), [], spec)
            self.assertEqual(listed(m), self.all_tools, spec)

    def test_single_family(self):
        m = fresh_server()
        m.apply_toolsets(m.mcp, "gsc")
        tools = listed(m)
        self.assertIn("list_projects", tools)
        self.assertIn("get_keywords", tools)
        self.assertIn("report_striking_distance", tools)
        self.assertFalse([t for t in tools if t.startswith(("ga_", "gads_", "social_", "rank_"))])
        self.assertEqual(len(tools), 1 + 19)

    def test_rank_does_not_swallow_ai_rank(self):
        m = fresh_server()
        m.apply_toolsets(m.mcp, "rank")
        tools = listed(m)
        self.assertTrue(any(t.startswith("rank_") for t in tools))
        self.assertFalse(any(t.startswith("ai_rank_") for t in tools))

    def test_family_keys_ignore_case_and_separators(self):
        for spec in ("aiRank", "ai_rank", "AIRANK", "ai-rank", " aiRank , "):
            m = fresh_server()
            m.apply_toolsets(m.mcp, spec)
            tools = listed(m)
            self.assertEqual(sorted(t for t in tools if t != "list_projects"),
                             sorted(t for t in self.all_tools if t.startswith("ai_rank_")), spec)

    def test_seo_page_combination(self):
        # The website's "SEO MCP" page = these families (mcp-catalog.json pages[seo]).
        m = fresh_server()
        m.apply_toolsets(m.mcp, "gsc,rank,aiRank,indexing,changes,competitor")
        tools = listed(m)
        self.assertNotIn("ga_get_overview", tools)
        self.assertNotIn("social_create_post", tools)
        self.assertIn("competitor_get_analysis", tools)
        self.assertIn("changes_list", tools)

    def test_unknown_family_warns_and_keeps_every_tool(self):
        m = fresh_server()
        err = io.StringIO()
        with redirect_stderr(err):
            removed = m.apply_toolsets(m.mcp, "gsc,serach-console")
        self.assertEqual(removed, [])
        self.assertEqual(listed(m), self.all_tools)
        self.assertIn("serach-console", err.getvalue())

    def test_removed_tools_cannot_be_called(self):
        m = fresh_server()
        m.apply_toolsets(m.mcp, "social")
        with self.assertRaises(Exception):
            asyncio.run(m.mcp.call_tool("ga_get_overview", {"project_id": "1"}))


if __name__ == "__main__":
    unittest.main()
