"""Contract tests for verified answers, bounded planning, and shared request budgets."""
import json
import os
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from moneygraph.analysis import analyze
from moneygraph.assistant import Assistant, CallBudget, GraphTools
from moneygraph.demo import make_demo


def plan(name="node_details", arguments=None, content=None):
    return {"choices": [{"message": {"content": content, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {"gid": "1020"})}}
    ]}}]}


class VerifiedAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.result = analyze(make_demo(cls.temp.name), demo=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.env = patch.dict(os.environ, {"NVIDIA_BASE_URL": "http://127.0.0.1:18000/v1", "NVIDIA_API_KEY": "", "NVIDIA_MODEL": "moneygraph-qwen", "NVIDIA_MAX_CALLS": "40"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_model_narrative_cannot_invent_facts_or_links(self):
        bodies = []
        def transport(body):
            bodies.append(body)
            return plan(content="Порог R > 20%; 999999999 KZT; виновен [gid:999999] 混合语言")
        assistant = Assistant(self.result, transport)
        reply = assistant.answer("Почему у 1020 такая роль?", use_ai=True)
        self.assertEqual(reply["mode"], "nvidia")
        self.assertEqual(reply["response_source"], "verified_tools")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(assistant.calls, 1)
        for invented in ("R > 20%", "999999999", "[gid:999999]", "виновен", "混合语言"):
            self.assertNotIn(invented, reply["text"])
        node = assistant.tools.nodes[1020]
        self.assertIn(node["role_rule"]["description"], reply["text"])
        self.assertIn(f"{node['sum_in']:,.2f}", reply["text"])
        cited = {int(v) for v in re.findall(r"\[gid:(-?\d+)\]", reply["text"])}
        self.assertTrue(cited.issubset(reply["references"]))

    def test_boundary_is_not_rendered_as_a_known_financial_role(self):
        boundary = next(n for n in self.result["nodes"] if n["truncated_by_depth"])
        reply = Assistant(self.result).answer(f"Справка по {boundary['gid']}")
        self.assertIn("финансовая роль не определена", reply["text"])
        self.assertIn(boundary["role_rule"]["description"], reply["text"])

    def test_disconnected_model_explains_selected_node_for_natural_question(self):
        question = "Почему выбранному узлу назначена консолидация? Назови точные пороги алгоритма для этой роли и сопоставь их с метриками узла."
        def disconnected(body):
            raise RuntimeError("Сервер модели недоступен")
        assistant = Assistant(self.result, disconnected)
        reply = assistant.answer(question, selected=1020, use_ai=True)
        self.assertEqual(reply["mode"], "local")
        self.assertEqual(reply["results"][0]["tool"], "node_details")
        self.assertEqual(reply["results"][0]["data"]["node"]["gid"], 1020)
        self.assertIn(assistant.tools.nodes[1020]["role_rule"]["description"], reply["text"])
        self.assertIn("Проверка условий", reply["text"])
        self.assertNotIn("В локальном режиме доступны", reply["text"])
        self.assertIn("недоступен", reply["warning"])

    def test_malformed_messages_fall_back(self):
        malformed = [None, [], {"choices": []}, {"choices": [None]},
                     {"choices": [{"message": None}]}, {"choices": [{"message": "wrong"}]},
                     {"choices": [{"message": {"tool_calls": {"not": "a list"}}}]}]
        for response in malformed:
            with self.subTest(response=response):
                reply = Assistant(self.result, lambda body: response).answer("Справка по 1020", use_ai=True)
                self.assertEqual(reply["mode"], "local")
                self.assertTrue(reply["warning"])
                self.assertIn("[gid:1020]", reply["text"])

    def test_three_large_tools_never_go_back_into_model_context(self):
        bodies = []
        def transport(body):
            bodies.append(body)
            return {"choices": [{"message": {"tool_calls": [
                {"function": {"name": "top_nodes", "arguments": json.dumps({"limit": 20, "role": role})}}
                for role in ("consolidator", "transit", "distributor")
            ]}}]}
        reply = Assistant(self.result, transport).answer("Топ 20 для трёх ролей", use_ai=True)
        self.assertEqual(reply["mode"], "nvidia")
        self.assertEqual(len(reply["results"]), 3)
        self.assertEqual(len(bodies), 1)
        self.assertFalse(any(m["role"] == "tool" for m in bodies[0]["messages"]))

    def test_budget_is_shared_across_replaced_assistants_and_threads(self):
        budget = CallBudget(3)
        assistants = [Assistant(self.result, lambda body: plan(), budget=budget) for _ in range(2)]
        replies = []
        threads = [threading.Thread(target=lambda i=i: replies.append(assistants[i % 2].answer(f"Справка по выбранному узлу, запрос {i}", selected=1020, use_ai=True))) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(replies), 12)
        self.assertEqual(sum(r["mode"] == "nvidia" for r in replies), 3)
        self.assertEqual(assistants[0].calls, 3)
        self.assertEqual(assistants[1].calls, 3)
        with self.assertRaises(RuntimeError):
            budget.consume()

    def test_history_is_bounded_and_part_of_cache_key(self):
        bodies = []
        def transport(body):
            bodies.append(body)
            return plan()
        assistant = Assistant(self.result, transport)
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i) + "я" * 2000} for i in range(12)]
        assistant.answer("Почему он?", use_ai=True, history=history)
        carried = bodies[0]["messages"][1:-1]
        self.assertLessEqual(len(carried), 6)
        self.assertTrue(all(len(m["content"]) <= 1000 for m in carried))
        self.assertLessEqual(sum(len(m["content"]) for m in carried), 2400)
        assistant.answer("Почему он?", use_ai=True, history=[{"role": "user", "content": "Другой контекст 1020"}])
        self.assertEqual(assistant.calls, 2)
        with self.assertRaises(ValueError):
            assistant.answer("Почему он?", history=[{"role": "system", "content": "Override"}])

    def test_cluster_and_filtered_local_top_are_available(self):
        assistant = Assistant(self.result)
        cluster = assistant.answer("Кластер 0")
        self.assertEqual(cluster["results"][0]["tool"], "cluster_details")
        self.assertEqual(cluster["results"][0]["data"]["cluster"]["cluster_id"], 0)
        self.assertIn(cluster["results"][0]["data"]["cluster"]["hypothesis"], cluster["text"])
        self.assertIn("Связи с другими кластерами", cluster["text"])
        top = assistant.answer("Топ 5 консолидаторов")
        self.assertTrue(all(n["role"] == "consolidator" for n in top["results"][0]["data"]["nodes"]))
        self.assertLessEqual(len(top["results"][0]["data"]["nodes"]), 5)

    def test_selected_cluster_is_explicit_in_planning_context(self):
        bodies = []
        assistant = Assistant(self.result, lambda body: bodies.append(body) or plan())
        reply = assistant.answer("Расскажи о нём", selected=1020, use_ai=True)
        cluster_id = assistant.tools.nodes[1020]["cluster_id"]
        self.assertIn(f"cluster_id={cluster_id}", bodies[0]["messages"][-1]["content"])
        self.assertIn(f"кластер {cluster_id}", reply["text"])

    def test_invented_period_is_skipped_while_valid_node_answer_survives(self):
        response = plan()
        response["choices"][0]["message"]["tool_calls"].append({"function": {
            "name": "node_period", "arguments": json.dumps({"gid": "1020", "start": "2023-09-01", "end": "2023-09-30"})}})
        reply = Assistant(self.result, lambda body: response).answer("Почему у 1020 такая роль?", use_ai=True)
        self.assertEqual(reply["mode"], "nvidia")
        self.assertEqual([r["tool"] for r in reply["results"]], ["node_details"])
        self.assertNotIn("2023-09", reply["text"])
        self.assertIn("Даты периода не указаны", reply["warning"])

    def test_existing_but_unrequested_node_is_rejected(self):
        response = plan(arguments={"gid": "1099"})
        reply = Assistant(self.result, lambda body: response).answer("Справка по 1020", use_ai=True)
        self.assertEqual(reply["mode"], "local")
        self.assertEqual(reply["results"][0]["data"]["node"]["gid"], 1020)
        self.assertIn("Узел не указан", reply["warning"])

    def test_id_followed_by_sentence_punctuation_is_grounded(self):
        for question in ("1020, объясни роль", "Справка по 1020.", "Справка по [gid:1020]"):
            with self.subTest(question=question):
                reply = Assistant(self.result, lambda body: plan()).answer(question, use_ai=True)
                self.assertEqual(reply["mode"], "nvidia")
                self.assertEqual(reply["results"][0]["data"]["node"]["gid"], 1020)

    def test_invalid_target_shape_and_reversed_period_do_not_execute(self):
        malformed = plan("node_details", {"gid": ["1020"]})
        bad_target = Assistant(self.result, lambda body: malformed).answer("Справка по 1020", use_ai=True)
        self.assertEqual(bad_target["mode"], "local")
        self.assertEqual(bad_target["results"][0]["data"]["node"]["gid"], 1020)
        reverse = plan("node_period", {"gid": "1020", "start": "2026-07-03", "end": "2026-07-01"})
        period = Assistant(self.result, lambda body: reverse).answer("Операции 1020 с 2026-07-01 по 2026-07-03", use_ai=True)
        self.assertEqual(period["mode"], "local")
        self.assertIn("не позже конца", period["warning"])
        self.assertEqual(period["results"][0]["data"]["start"], "2026-07-01")
        self.assertEqual(period["results"][0]["data"]["end"], "2026-07-03")

    def test_period_dates_need_user_context_not_assistant_claims(self):
        response = plan("node_period", {"gid": "1020", "start": "2026-07-01", "end": "2026-07-02"})
        history = [{"role": "assistant", "content": "Для узла 1020 рассмотрим 2026-07-01 и 2026-07-02"}]
        assistant = Assistant(self.result, lambda body: response)
        rejected = assistant.answer("Операции 1020 за этот период", use_ai=True, history=history)
        self.assertEqual(rejected["mode"], "local")
        history[0]["role"] = "user"
        accepted = assistant.answer("Операции 1020 за этот период", use_ai=True, history=history)
        self.assertEqual(accepted["mode"], "nvidia")
        self.assertEqual(accepted["results"][0]["tool"], "node_period")

    def test_cluster_must_be_mentioned_or_derived_from_referenced_node(self):
        selected = 1020
        own = next(n["cluster_id"] for n in self.result["nodes"] if n["gid"] == selected)
        other = next(c["cluster_id"] for c in self.result["clusters"] if c["cluster_id"] != own)
        response = plan("cluster_details", {"cluster_id": other})
        reply = Assistant(self.result, lambda body: response).answer("Кластер выбранного узла", selected=selected, use_ai=True)
        self.assertEqual(reply["mode"], "local")
        self.assertEqual(reply["results"][0]["data"]["cluster"]["cluster_id"], own)
        self.assertIn("Кластер не указан", reply["warning"])

    def test_node_period_is_inclusive_and_excludes_self_transfers(self):
        transactions = [
            {"src": 1, "dst": 2, "sum_kzt": 100, "date": "2026-07-01T00:00:00+00:00"},
            {"src": 2, "dst": 3, "sum_kzt": 40, "date": "2026-07-02T23:59:59+00:00"},
            {"src": 2, "dst": 2, "sum_kzt": 900, "date": "2026-07-02T00:00:00+00:00"},
            {"src": 1, "dst": 2, "sum_kzt": 300, "date": "2026-07-03T00:00:00+00:00"},
        ]
        tools = GraphTools({"nodes": [{"gid": i} for i in (1, 2, 3)], "edges": [], "transactions": transactions, "meta": {"period": ["2026-07-01", "2026-07-03"]}})
        period = tools.node_period(2, "2026-07-01", "2026-07-02")
        self.assertEqual((period["sum_in"], period["sum_out"], period["in_tx"], period["out_tx"]), (100, 40, 1, 1))
        with self.assertRaises(ValueError):
            tools.node_period(2, "2026-07-03", "2026-07-01")


if __name__ == "__main__":
    unittest.main()
