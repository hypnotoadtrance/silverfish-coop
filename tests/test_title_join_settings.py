import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock
from uuid import UUID, uuid4, uuid5

from tools.silverfish.title_join_settings import MAX_CHARACTERS, MAX_IMPORTS, TitleJoinSettings


GEN = "a" * 32
NEW = "b" * 32
FIRST = "1" * 32
CREATE = "2" * 32
CHARACTER = "ff7de067-5c91-4c8f-befd-3e5bde50bc69"
GUEST = UUID("db220c97-27c2-4d6b-8462-820a249d958a")
SECRET = "private invitation proof only"
ROW = {"character_id": CHARACTER, "name": "Àlex"}


class TitleJoinSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.work = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.bridge = self.work / "titles" / "isolated-title"
        self.bridge.mkdir(parents=True)

    def make(self, callable=None):
        catalog = callable or AsyncMock(return_value={"ok": True, "characters": [ROW]})
        settings = TitleJoinSettings(self.work, self.bridge, catalog=catalog)
        self.addAsyncCleanup(settings.close)
        return settings, catalog

    def invitation(self, variant=0):
        return (json.dumps({"credential": SECRET, "variant": variant, "description": "Prêt"},
                           ensure_ascii=False, indent=2) + "\r\n").encode("utf-8")

    def write_import(self, settings, data, *, operation=FIRST, generation=GEN, count=None):
        supplied = len(data) if count is None else count
        settings.request_path.write_bytes(f"1\t{generation}\t{operation}\timport\t{supplied}\n".encode("ascii") + data)

    def write_create(self, settings, identity, name, *, operation=CREATE, generation=GEN):
        settings.request_path.write_bytes(f"1\t{generation}\t{operation}\tcreate\t{identity}\t{name}\n".encode("utf-8"))

    async def reply(self, settings, *, generation=GEN):
        async with asyncio.timeout(2):
            while not settings.response_path.is_file():
                settings.poll(generation)
                await asyncio.sleep(.002)
        data = settings.response_path.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertNotIn(SECRET.encode(), data)
        rows = data.decode("utf-8").splitlines()
        head = rows[0].split("\t")
        self.assertEqual(head[:2], ["1", generation])
        self.assertEqual(int(head[5]), len(rows) - 1)
        return head, rows[1:]

    async def test_authenticates_exact_private_bytes_and_resolves_the_unchanged_roster_member(self):
        release = asyncio.Event()
        actual = self.invitation()
        imported = []

        async def catalog(work, invitation, name, operation):
            self.assertEqual((work, name, operation), (self.work, None, None))
            self.assertTrue(invitation.is_relative_to(self.bridge))
            self.assertEqual(invitation.read_bytes(), actual)
            imported.append(invitation)
            await release.wait()
            return {"ok": True, "characters": [ROW]}

        settings, _ = self.make(catalog)
        self.write_import(settings, actual)
        settings.poll(GEN)
        self.assertFalse(settings.request_path.exists() or settings.response_path.exists())
        release.set()
        result, rows = await self.reply(settings)
        self.assertEqual((result[2], result[3], rows), (FIRST, "ok", [f"{CHARACTER}\tÀlex"]))
        identity = result[4]
        self.assertRegex(identity, r"^[a-f0-9]{32}$")
        with self.assertRaisesRegex(ValueError, "unverified_character"):
            settings.resolve(GEN, identity, str(GUEST))
        resolved = settings.resolve(GEN, identity, CHARACTER)
        self.assertEqual(resolved, imported[0])
        resolved.write_bytes(actual + b" ")
        with self.assertRaisesRegex(ValueError, "invite_changed"):
            settings.resolve(GEN, identity, CHARACTER)
        resolved.write_bytes(actual)
        settings.poll(None)
        self.assertTrue(resolved.is_file())
        self.assertFalse(settings.response_path.exists())
        with self.assertRaisesRegex(ValueError, "stale_generation"):
            settings.resolve(GEN, identity, CHARACTER)
        await settings.close()
        self.assertFalse(resolved.exists() or settings.request_path.exists())

    async def test_publishes_steam_creator_and_one_local_profile_image_for_the_roster(self):
        account = "76561198000000002"
        creator = {"name": "Steam Test Guest", "steam_account": account}
        catalog = AsyncMock(return_value={"ok": True, "characters": [ROW], "creator": creator})
        photos = AsyncMock(return_value={account: "89504e47"})
        settings = TitleJoinSettings(self.work, self.bridge, catalog=catalog, profile_avatars=photos)
        self.addAsyncCleanup(settings.close)
        self.write_import(settings, self.invitation())
        async with asyncio.timeout(2):
            while not settings.response_path.exists():
                settings.poll(GEN)
                await asyncio.sleep(.002)
        header, attribution, character = settings.response_path.read_text(encoding="utf8").splitlines()
        schema, generation, operation, state, identity, count = header.split("\t")
        self.assertEqual((schema, generation, operation, state, count), ("2", GEN, FIRST, "ok", "1"))
        self.assertEqual(attribution, f"{creator['name']}\t89504e47")
        self.assertEqual(character, f"{CHARACTER}\tÀlex")
        photos.assert_awaited_once_with({account})
        self.assertTrue(settings.resolve(GEN, identity, CHARACTER).is_file())

    async def test_create_retries_one_host_operation_when_roster_refresh_is_lost(self):
        names = {CHARACTER: ROW["name"]}
        operations, lose_next_roster = [], False

        async def catalog(_, invitation, name, operation):
            nonlocal lose_next_roster
            self.assertEqual(invitation.read_bytes(), self.invitation())
            if name is not None:
                self.assertEqual((name, operation), ("New Scout", CREATE))
                created = str(uuid5(GUEST, operation))
                names[created] = name
                operations.append(operation)
                lose_next_roster = len(operations) == 1
                return {"ok": True, "character": {"character_id": created, "name": name}}
            if lose_next_roster:
                lose_next_roster = False
                raise TimeoutError(SECRET)
            return {"ok": True, "characters": [{"character_id": key, "name": value} for key, value in names.items()]}

        settings, _ = self.make(catalog)
        self.write_import(settings, self.invitation())
        offered, _ = await self.reply(settings)
        identity = offered[4]
        self.write_create(settings, identity, "New Scout")
        settings.poll(GEN)
        failure, _ = await self.reply(settings)
        self.assertEqual(failure[2:5], [CREATE, "error", "catalog_unavailable"])
        self.write_create(settings, identity, "New Scout")
        settings.poll(GEN)
        self.assertFalse(settings.response_path.exists() or settings.request_path.exists())
        success, rows = await self.reply(settings)
        new = str(uuid5(GUEST, CREATE))
        self.assertEqual(success[2:5], [CREATE, "ok", identity])
        self.assertEqual(rows, [f"{CHARACTER}\tÀlex", f"{new}\tNew Scout"])
        self.assertTrue(settings.resolve(GEN, identity, new).exists())
        self.write_create(settings, identity, "New Scout")
        settings.poll(GEN)
        self.assertEqual(await self.reply(settings), (success, rows))
        self.assertEqual(operations, [CREATE, CREATE])
        self.write_create(settings, identity, "Different Name")
        settings.poll(GEN)
        self.assertEqual((await self.reply(settings))[0][3:5], ["error", "operation_conflict"])
        self.assertEqual(operations, [CREATE, CREATE])

    async def test_rotating_generation_cancels_work_and_discards_late_reply_and_stale_input(self):
        started, late, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        paths = []

        async def catalog(_, invitation, _name, _operation):
            paths.append(invitation)
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                late.set()
                await release.wait()
            return {"ok": True, "characters": [ROW]}

        settings, _ = self.make(catalog)
        self.write_import(settings, self.invitation())
        settings.poll(GEN)
        await asyncio.wait_for(started.wait(), 1)
        try:
            settings.poll(NEW)
            await asyncio.wait_for(late.wait(), 1)
            self.assertFalse(paths[0].exists() or settings.response_path.exists())
            self.write_import(settings, self.invitation(), generation=GEN)
            settings.poll(NEW)
            self.assertFalse(settings.request_path.exists() or settings.response_path.exists())
        finally:
            release.set()
        await asyncio.sleep(.01)
        settings.poll(NEW)
        self.assertFalse(settings.response_path.exists())

    async def test_rejects_wrong_lengths_and_invalid_utf8_without_authentication(self):
        settings, catalog = self.make()
        excessive_integer = b'{"integer":' + b"9" * 5000 + b"}"
        for body, count, code in ((b"{}", 3, "invalid_request"), (b"\xff", 1, "invalid_invite"),
                                  (b"null", 4, "invalid_invite"), (b"{}", "02", "invalid_request"),
                                  (excessive_integer, len(excessive_integer), "invalid_invite")):
            with self.subTest(body=body, count=count):
                self.write_import(settings, body, count=count)
                settings.poll(GEN)
                self.assertEqual((await self.reply(settings))[0][3:5], ["error", code])
        catalog.assert_not_awaited()

    async def test_full_catalog_remains_selectable_without_creating_a_row_it_cannot_display(self):
        rows = [ROW, *({"character_id": str(uuid4()), "name": "🦉" * 64} for _ in range(MAX_CHARACTERS - 1))]
        settings, catalog = self.make(AsyncMock(return_value={"ok": True, "characters": rows}))
        self.write_import(settings, self.invitation())
        offered, visible = await self.reply(settings)
        self.assertEqual(len(visible), MAX_CHARACTERS)
        self.assertTrue(settings.resolve(GEN, offered[4], CHARACTER).is_file())
        self.write_create(settings, offered[4], "Another character")
        settings.poll(GEN)
        self.assertEqual((await self.reply(settings))[0][3:5], ["error", "create_refused"])
        catalog.assert_awaited_once()

    async def test_refuses_duplicate_or_oversized_rosters_and_removes_failed_imports(self):
        for rows in ([ROW, ROW], [ROW] * (MAX_CHARACTERS + 1)):
            with self.subTest(count=len(rows)):
                settings, _ = self.make(AsyncMock(return_value={"ok": True, "characters": rows}))
                self.write_import(settings, self.invitation())
                self.assertEqual((await self.reply(settings))[0][3:5], ["error", "catalog_invalid"])
                self.assertEqual(list(self.bridge.rglob("*.invite.json")), [])
                await settings.close()

    async def test_limits_private_import_slots_and_reclaims_them_after_new_offer(self):
        settings, catalog = self.make()
        for index in range(MAX_IMPORTS + 1):
            self.write_import(settings, self.invitation(index), operation=f"{index + 1:032x}")
            settings.poll(GEN)
            outcome, _ = await self.reply(settings)
            if index == MAX_IMPORTS:
                self.assertEqual(outcome[3:5], ["error", "import_limit"])
            else:
                self.assertEqual(outcome[3], "ok")
        self.assertEqual(catalog.await_count, MAX_IMPORTS)
        self.assertEqual(len(list(self.bridge.rglob("*.invite.json"))), MAX_IMPORTS)
        settings.poll(NEW)
        self.assertEqual(list(self.bridge.rglob("*.invite.json")), [])
        self.write_import(settings, self.invitation(99), generation=NEW)
        self.assertEqual((await self.reply(settings, generation=NEW))[0][3], "ok")


if __name__ == "__main__":
    unittest.main()
