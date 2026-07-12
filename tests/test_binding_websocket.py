import json
import tempfile
import unittest
from pathlib import Path

from activity_store import ActivityStore
from agent_server import AgentWebSocketServer


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class BindingWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_and_bind_source_broadcasts_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                server = AgentWebSocketServer(store)
                websocket = FakeWebSocket()
                server.clients.add(websocket)

                await server.handle_message(websocket, json.dumps({"type": "create_group", "name": "Game"}))
                created = websocket.messages[-1]
                self.assertEqual(created["type"], "group_created")
                group = created["group"]
                self.assertEqual(created["snapshot"]["bindingGroups"], [group])

                await server.handle_message(
                    websocket,
                    json.dumps(
                        {
                            "type": "set_group_icon",
                            "groupId": group["groupId"],
                            "iconId": "gamepad",
                        }
                    ),
                )
                icon_changed = websocket.messages[-1]
                self.assertEqual(icon_changed["type"], "group_icon_changed")
                self.assertEqual(icon_changed["group"]["iconId"], "gamepad")

                await server.handle_message(
                    websocket,
                    json.dumps(
                        {
                            "type": "bind_source",
                            "groupId": group["groupId"],
                            "sourceKey": "browser:tooli.com",
                        }
                    ),
                )
                bound = websocket.messages[-1]
                self.assertEqual(bound["type"], "source_bound")
                self.assertEqual(bound["sourceKey"], "browser:tooli.com")
                self.assertIn(f"group:{group['groupId']}", bound["snapshot"]["totals"])
            finally:
                store.close()

    async def test_rejects_other_browser_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                server = AgentWebSocketServer(store)
                websocket = FakeWebSocket()
                server.clients.add(websocket)
                group = store.create_binding_group("Game")

                await server.handle_message(
                    websocket,
                    json.dumps(
                        {
                            "type": "bind_source",
                            "groupId": group["groupId"],
                            "sourceKey": "browser:other",
                        }
                    ),
                )

                self.assertEqual(websocket.messages[-1]["type"], "error")
            finally:
                store.close()

    async def test_browser_detail_ignore_command_broadcasts_and_rejects_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                server = AgentWebSocketServer(store)
                websocket = FakeWebSocket()
                server.clients.add(websocket)

                await server.handle_message(
                    websocket,
                    json.dumps(
                        {
                            "type": "ignore_browser_detail",
                            "sourceKey": "browser:youtube.com",
                            "displayName": "YouTube",
                        }
                    ),
                )
                ignored = websocket.messages[-1]
                self.assertEqual(ignored["type"], "browser_detail_ignored")
                self.assertEqual(ignored["sourceKey"], "browser:youtube.com")
                ignored_item = ignored["snapshot"]["ignoredActivities"][0]
                self.assertEqual(ignored_item["activity_key"], "browser:youtube.com")
                self.assertEqual(ignored_item["sourceType"], "browser")

                await server.handle_message(
                    websocket,
                    json.dumps({"type": "unignore_browser_detail", "sourceKey": "browser:youtube.com"}),
                )
                self.assertEqual(websocket.messages[-1]["type"], "browser_detail_unignored")

                await server.handle_message(
                    websocket,
                    json.dumps(
                        {
                            "type": "ignore_browser_detail",
                            "sourceKey": "browser:other",
                            "displayName": "Other",
                        }
                    ),
                )
                self.assertEqual(websocket.messages[-1]["type"], "error")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
