"""Same-process proof for the private, Lua-disabled title command lane."""

import asyncio
import uuid

from tools.silverfish import native_host_save, native_title_entry, runtime
from tools.silverfish.embedded_engine import _current_startup, assert_private_saved_tree
from tools.silverfish.local_bridge import LocalBridge, NativeTitleStartup
from tools.silverfish.title_handoff import _isolated_stock_title


async def verify_native_title_scope(engine, *, timeout=6):
    """Run after native startup qualification in the isolated embedded worker."""
    image = _current_startup(()).image
    profile = engine.profile
    saved = profile / "Saved"
    game = engine.bridge
    assert game.local_bridge is None
    assert_private_saved_tree(profile)

    def files():
        return {path.relative_to(saved).as_posix(): runtime._file_sha256(path)
                for path in (saved / "SaveGames").rglob("*") if path.is_file()}

    before = files()
    assert before, "The cold scope fixture requires copied stock saves"
    startup = NativeTitleStartup.connect(image)
    assert startup is not None, "The independent native startup reader is unavailable"

    async def startup_frame(after=0):
        async with asyncio.timeout(timeout):
            while True:
                raw = startup.read()
                frame = native_title_entry.parse_native_startup(*raw) if raw else None
                title = _isolated_stock_title(frame, saved)
                if title and title[0] > after:
                    return title
                await asyncio.sleep(.025)

    initial_startup, title_identity = await startup_frame()
    ordinary = LocalBridge.connect(image)
    if ordinary is not None:
        ordinary.close()
        raise AssertionError("The ordinary Lua command bridge opened in the cold native title")

    lane = LocalBridge.connect_native_title(image)
    assert lane is not None, "The scoped native title bridge did not open"
    game.local_bridge = lane
    try:
        duplicate = LocalBridge.connect_native_title(image)
        if duplicate is not None:
            duplicate.close()
            raise AssertionError("The native title accepted two command owners")

        async def snapshot(scope, sequence=0):
            def current(frame):
                title = _isolated_stock_title(frame, saved)
                return (title and title[1] == title_identity and title[0] >= sequence
                        and "save_scope" in frame and frame["save_scope"] == scope)

            frame = await game.wait_for(current, timeout=timeout, interval=.025)
            assert game.status_source == "memory", "The native title used a file status fallback"
            return frame

        base = await snapshot(None)
        last_sequence = base["sequence"]
        for command in ("ping", "native-save-scope-select\tnot-a-scope", "native-save-scope-release\t" + "0" * 32 + "\textra"):
            invalid = f"{uuid.uuid4().hex}\t{command}\n".encode("ascii")
            assert lane._submit(invalid, len(invalid)) == 0, "An invalid action entered the native title queue"

        scopes = [native_host_save.SaveScope(profile, uuid.uuid4().hex) for _ in range(2)]
        for scope in scopes:
            directory = native_host_save.directory(scope)
            directory.mkdir(parents=True, exist_ok=False)
            assert native_host_save.directory(scope) == directory
        first, second = (scope.id for scope in scopes)
        receipts = {}

        async def run(label, action, requested_scope, expected_scope, *, error=None):
            nonlocal last_sequence
            request_id = uuid.uuid4().hex
            request = f"{request_id}\tnative-save-scope-{action}\t{requested_scope}\n".encode("ascii")
            raw = await lane.command(request, timeout)
            frame = game._parse_status(raw, cache=False)
            title = _isolated_stock_title(frame, saved)
            assert title and title[1] == title_identity and title[0] > last_sequence, raw
            assert "save_scope" in frame and frame["save_scope"] == expected_scope, raw
            ack = frame.get("ack", {})
            assert ack.get("id") == request_id and ack.get("result") == ("error" if error else "accepted"), raw
            assert f"native-title-scope-result\t{int(error is None)}" in raw.splitlines(), raw
            if error:
                assert ack.get("message") == error, raw
            published = await snapshot(expected_scope, title[0])
            last_sequence = published["sequence"]
            receipts[label] = {"request": request_id, "requested_scope": requested_scope, "scope": expected_scope,
                               "sequence": title[0], "published_sequence": last_sequence, "result": ack["result"],
                               "message": ack["message"]}

        await run("select_first", "select", first, first)
        await run("select_busy", "select", second, first, error="Release the current co-op save scope first")
        await run("release_wrong", "release", second, first, error="The co-op save scope did not match")
        await run("release_first", "release", first, None)
        await run("select_second", "select", second, second)
        await run("release_second", "release", second, None)
        final_startup, final_title = await startup_frame(initial_startup)
        assert final_title == title_identity, "The title changed during a save-scope-only operation"
        assert_private_saved_tree(profile)
        assert files() == before, "Native scope selection changed the copied save files"
    finally:
        game.local_bridge = None
        lane.close()

    return {"verified": True, "pid": engine.pid, "ordinary_attach_refused": True, "native_attach": True,
            "duplicate_attach_refused": True, "unrelated_action_refused_before_submit": True,
            "initial_command_sequence": base["sequence"], "initial_startup_sequence": initial_startup,
            "final_startup_sequence": final_startup, "receipts": receipts, "detached": True,
            "copied_saves_unchanged": True}
