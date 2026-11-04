"""Registration data flow must reach dispatchers without inventing import cycles."""

import unittest
from pathlib import Path

from tools.quality.architecture_report import components
from tools.quality.file_map import attach, query
from tools.quality.impact import consumers, mapping_gaps, uncertainties
from tools.quality.shared_state import analyze
from tools.quality.source_relations import relationships


def source_fixture():
    return {
        "store.py": b"registry = {}\n"
        b"def register(key):\n"
        b" def apply(cls):\n  registry[key] = cls\n  return cls\n"
        b" return apply\n",
        "api/__init__.py": b"from store import register as attach\n",
        "plugin.py": b"from api import attach\n@attach('voice')\nclass Voice: pass\n",
        "model.py": b"class Backend: pass\n",
        "manual.py": b"from store import registry as bindings\n"
        b"from model import Backend\n"
        b"def setup(): bindings['custom'] = Backend\n",
        "dispatcher.py": b"from store import registry as adapters\n"
        b"def dispatch(key): return adapters[key]()\n",
        "other.py": b"registry = {}\ndef add(): registry['x'] = 1\n",
    }


class RegistryMapTests(unittest.TestCase):
    def test_factory_reexports_and_manual_writes_reach_real_reader(self):
        sources = source_fixture()
        facts = analyze(sources)
        dependencies = facts["dispatcher.py"]["business_dependencies"]
        self.assertIn("plugin.py", dependencies)
        self.assertIn("manual.py", dependencies)
        self.assertNotIn("other.py", dependencies)
        via = facts["plugin.py"]["shared_state_accesses"]
        self.assertIn("store.register", {s.get("via") for s in via})
        evidence = {}
        routes = consumers(sources, ["model.py"], evidence=evidence)
        self.assertEqual(
            routes["dispatcher.py"], ["model.py", "manual.py", "dispatcher.py"]
        )
        graph, _ = relationships(sources)
        self.assertNotIn("manual.py", graph["dispatcher.py"])
        self.assertEqual(components(graph), [])

    def test_shadowing_rebinding_and_escaped_aliases_stay_explicit(self):
        sources = source_fixture() | {
            "shadow.py": b"from store import registry\n"
            b"def f(registry): return registry['x']\n",
            "replaced.py": b"import api\napi.attach = lambda key: key\napi.attach('x')\n",
            "escaped.py": b"from store import registry\nlocal = registry\n",
        }
        facts = analyze(sources)
        self.assertEqual(facts["shadow.py"]["business_dependencies"], [])
        self.assertEqual(facts["replaced.py"]["shared_state_accesses"], [])
        for path in ("shadow.py", "replaced.py", "escaped.py"):
            self.assertTrue(facts[path]["shared_state_gaps"], path)
            evidence = {}
            routes = consumers(sources, [path], evidence=evidence)
            self.assertIn(path, mapping_gaps([path], routes, evidence))
        evidence = {}
        routes = consumers(sources, ["plugin.py"], evidence=evidence)
        # No false edge is needed to retain the unresolved reader's risk.
        self.assertIn("shadow.py", uncertainties(routes, evidence))

    def test_invalid_source_does_not_become_an_empty_success(self):
        with self.assertRaises(SyntaxError):
            consumers({"broken.py": b"def ("}, ["broken.py"])

    def test_map_query_keeps_business_edges_separate_and_reports_route_kind(self):
        sources = source_fixture()
        records = [{"path": p, "role": "production"} for p in sources]
        influence = attach(records, sources, {})
        report = {"commit": "frozen", "tree": "tree", "files": records}
        result = query(report, "model.py")
        self.assertEqual(
            result["consumer_route_kinds"]["dispatcher.py"], ["source", "shared_state"]
        )
        self.assertGreater(influence["business_edges"], 0)
        dispatcher = next(r for r in records if r["path"] == "dispatcher.py")
        self.assertNotIn("manual.py", dispatcher["dependencies"])
        self.assertIn("manual.py", dispatcher["business_dependencies"])

    def test_actual_codex_and_platform_registries_select_native_contracts(self):
        root = Path(__file__).resolve().parents[2]
        oauth = "data/plugins/astrbot_plugin_codex_oauth_plug/oauth_plug_openai_codex/"
        paths = [
            "astrbot/core/provider/register.py",
            "astrbot/core/provider/manager.py",
            oauth + "registration.py",
            oauth + "provider.py",
            "astrbot/core/platform/register.py",
            "astrbot/core/platform/manager.py",
            "astrbot/core/platform/sources/webchat/webchat_adapter.py",
        ]
        sources = {p: (root / p).read_bytes() for p in paths}
        records = [{"path": p, "role": "production"} for p in sources]
        attach(records, sources, {})
        report = {"commit": "worktree", "tree": "fixture", "files": records}
        result = query(report, oauth + "provider.py")
        manager = "astrbot/core/provider/manager.py"
        self.assertIn(manager, result["consumer_routes"])
        self.assertIn("shared_state", result["consumer_route_kinds"][manager])
        self.assertIn("registration_contracts", result["test_groups"])
        platform = analyze(sources)["astrbot/core/platform/manager.py"]
        self.assertIn(paths[-1], platform["business_dependencies"])
