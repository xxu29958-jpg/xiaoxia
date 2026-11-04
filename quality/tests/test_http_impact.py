"""HTTP consumers are real dependencies even without a Python import edge."""

import unittest
from pathlib import Path

from tools.quality.changes import Change
from tools.quality.file_map import attach, query
from tools.quality.http_protocol import uncertainties
from tools.quality.impact import consumers, mapping_gaps
from tools.quality.source_relations import relationships
from tools.quality.test_selection import required_tests


def service(port=6190):
    return (
        "from fastapi import FastAPI\nimport uvicorn\napp = FastAPI()\n"
        "@app.post('/speech')\ndef speak(): return b'audio'\n"
        f"uvicorn.run(app, host='127.0.0.1', port={port})\n"
    ).encode()


class HttpImpactTests(unittest.TestCase):
    def test_actual_voice_gateway_reaches_chat_client_and_consumers(self):
        root = Path(__file__).resolve().parents[2]
        gateway = "services/voice/server_voice.py"
        client = "data/plugins/astrbot_plugin_ada_companion/local_voice.py"
        chat = "data/plugins/astrbot_plugin_ada_companion/main.py"
        sources = {p: (root / p).read_bytes() for p in (gateway, client, chat)}
        routes = consumers(sources, [gateway])
        self.assertIn(client, routes)
        self.assertIn(chat, routes)
        selected = required_tests([Change(p, None, "M") for p in routes])
        self.assertTrue(selected["cognition_contracts"])
        self.assertTrue(selected["performance_contracts"])
        graph, _ = relationships(sources)
        self.assertNotIn(gateway, graph[client])
        records: list[dict] = [{"path": p, "role": "production"} for p in sources]
        attach(records, sources, {})
        result = query(
            {"commit": "fixture", "tree": "fixture", "files": records}, gateway
        )
        self.assertEqual(result["consumer_route_kinds"][client], ["http_protocol"])

    def test_direct_library_alias_and_session_calls_match_address_not_path_alone(self):
        sources = {
            "server.py": service(),
            "unrelated.py": service(9999),
            "client.py": b"import requests as wire\nwire.post('http://localhost:6190/speech')\n",
            "other.py": b"import requests\nrequests.post('https://elsewhere.example/speech')\n",
        }
        routes = consumers(sources, ["server.py"])
        self.assertIn("client.py", routes)
        self.assertNotIn("other.py", routes)
        self.assertNotIn("unrelated.py", routes)
        self.assertEqual(list(consumers(sources, ["unrelated.py"])), ["unrelated.py"])

    def test_scoped_session_names_do_not_inherit_another_functions_address(self):
        sources = {
            "server.py": service(),
            "client.py": b"import aiohttp\n"
            b"async def known():\n"
            b" async with aiohttp.ClientSession() as client:\n"
            b"  await client.post('http://localhost:6190/speech')\n"
            b"async def unknown(client):\n await client.post('http://localhost:9999/speech')\n",
        }
        evidence = {}
        consumers(sources, ["server.py"], evidence=evidence)
        self.assertEqual(len(evidence["client.py"]["http_clients"]), 1)
        self.assertTrue(evidence["client.py"]["http_binding_gaps"])

    def test_import_shadowing_and_rebinding_cannot_invent_a_service_consumer(self):
        variants = (
            "import requests\ndef f(requests): requests.post('http://localhost:6190/speech')\n",
            "import requests\nrequests = object()\nrequests.post('http://localhost:6190/speech')\n",
            "def f():\n import requests\ndef g():\n requests.post('http://localhost:6190/speech')\n",
            "import requests\nrequests.post = lambda url: None\nrequests.post('http://localhost:6190/speech')\n",
            "import requests\nrequests.Session = lambda: object()\nclient = requests.Session()\nclient.post('http://localhost:6190/speech')\n",
        )
        for source in variants:
            with self.subTest(source=source):
                evidence = {}
                routes = consumers(
                    {"server.py": service(), "client.py": source.encode()},
                    ["server.py"],
                    evidence=evidence,
                )
                self.assertNotIn("client.py", routes)
                self.assertIn("client.py", uncertainties(routes, evidence))
                self.assertIn(
                    "client.py",
                    mapping_gaps(["client.py"], {"client.py": ["client.py"]}, evidence),
                )

    def test_constructor_alias_is_evaluated_in_its_own_scope(self):
        source = b"import httpx as wire\nclient = wire.Client()\n"
        source += b"def f():\n import requests as wire\n return client.post('http://localhost:6190/speech')\n"
        evidence = {}
        consumers(
            {"server.py": service(), "client.py": source},
            ["server.py"],
            evidence=evidence,
        )
        self.assertEqual(len(evidence["client.py"]["http_clients"]), 1)
        source = b"wire = object()\nclient = wire.Client()\n"
        source += b"def f():\n import requests as wire\n return client.post('http://localhost:6190/speech')\n"
        evidence = {}
        routes = consumers(
            {"server.py": service(), "client.py": source},
            ["server.py"],
            evidence=evidence,
        )
        self.assertNotIn("client.py", routes)

    def test_unknown_target_is_visible_even_when_only_server_changes(self):
        sources = {
            "server.py": service(),
            "client.py": b"import requests\ndef send(url):\n return requests.post(url)\n",
        }
        evidence = {}
        routes = consumers(sources, ["server.py"], evidence=evidence)
        self.assertNotIn("client.py", routes)
        self.assertIn("client.py", uncertainties(routes, evidence))

    def test_multiple_listeners_remain_candidates_and_do_not_become_import_cycles(self):
        sources = {
            "server.py": service(),
            "alternate.py": service(),
            "client.py": b"import requests\nrequests.post('http://localhost:6190/speech')\n",
        }
        evidence = {}
        consumers(sources, ["server.py"], evidence=evidence)
        self.assertEqual(
            evidence["client.py"]["protocol_dependencies"],
            ["alternate.py", "server.py"],
        )
        self.assertEqual(
            evidence["client.py"]["dynamic_gaps"][0]["candidates"],
            ["alternate.py", "server.py"],
        )
        graph, _ = relationships(sources)
        self.assertEqual(graph["client.py"], set())

    def test_report_keeps_only_protocol_address_not_url_credentials(self):
        sources = {
            "server.py": service(),
            "client.py": b"import requests\nrequests.post('http://user:synthetic@localhost:6190/speech?key=not-real')\n",
        }
        evidence = {}
        consumers(sources, ["server.py"], evidence=evidence)
        report = repr(evidence["client.py"]["protocol_relations"])
        self.assertNotIn("synthetic", report)
        self.assertNotIn("not-real", report)

    def test_unresolved_listener_and_existing_non_http_gaps_stay_visible(self):
        sources = {
            "server.py": service(0),
            "client.py": b"import requests, importlib\ndef f(url, name):\n requests.post(url)\n importlib.import_module(name)\n",
        }
        records: list[dict] = [{"path": p, "role": "production"} for p in sources]
        attach(records, sources, {})
        result = query(
            {"commit": "fixture", "tree": "fixture", "files": records}, "client.py"
        )
        self.assertEqual(
            {g["kind"] for g in result["coverage_gaps"]["client.py"]},
            {"computed_import", "http_target"},
        )
        self.assertTrue(records[0]["http_listeners"][0]["unresolved"])


if __name__ == "__main__":
    unittest.main()
