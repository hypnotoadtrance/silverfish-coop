"""Prove a packaged stock title hands off to a private embedded Host session."""

import argparse
import asyncio
from contextlib import suppress
import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install_stock
from live_multi_smoke import saved_hashes
from tools.silverfish import embedded_startup, embedded_title_plan, native_title_entry as title, runtime
from tools.silverfish.control import submit
from tools.silverfish.title_handoff import _isolated_stock_title
from tools.silverfish.title_session import STATUS_FILE
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.udp_ownership import owns_udp_port


ID = re.compile(r"[0-9a-f]{32}")
LOG_PREFIX = "[SilverfishCoopBootstrap] "
TRANSITIONS = ("test_selection", "native_launch_started", "native_launch_ready")
WAIT_TIMEOUT = 258


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
        ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
        ("name", wintypes.WCHAR * 260),
    ]


class _OwnedProcess:
    def __init__(self, api, handle, pid, image, created):
        self.api, self.handle, self.pid, self.image, self.created = api, handle, pid, image, created

    def poll(self):
        ready = self.api.wait(self.handle, 0)
        if ready == WAIT_TIMEOUT:
            return None
        value = wintypes.DWORD()
        if ready != 0 or not self.api.exit_code(self.handle, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    @property
    def returncode(self):
        return self.poll()


class _Descendants:
    """Hold process handles only for descendants with an exact expected image path."""

    def __init__(self, root_pid, images):
        self.root_pid, self.images, self.processes = root_pid, frozenset(images), {}
        self.names = {image.name.casefold() for image in self.images}
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        def bind(name, result, *args):
            function = getattr(kernel, name)
            function.restype, function.argtypes = result, args
            return function

        self.snapshot = bind("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
        self.first = bind("Process32FirstW", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        self.next = bind("Process32NextW", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        self.open = bind("OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.path = bind("QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
                         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        self.times = bind("GetProcessTimes", wintypes.BOOL, wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4)
        self.wait = bind("WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD)
        self.exit_code = bind("GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        self.terminate = bind("TerminateProcess", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
        self.close = bind("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
        root = self.open(0x1000, False, self.root_pid)
        if not root:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.root_created = self.created(root)
        finally:
            self.close(root)

    def created(self, process):
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not self.times(process, *(ctypes.byref(stamp) for stamp in stamps)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime

    def refresh(self):
        handle = self.snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        entries, item = [], _ProcessEntry()
        item.size = ctypes.sizeof(item)
        try:
            if not self.first(handle, ctypes.byref(item)):
                raise ctypes.WinError(ctypes.get_last_error())
            while True:
                if item.name.casefold() in self.names:
                    entries.append((item.pid, item.parent))
                if not self.next(handle, ctypes.byref(item)):
                    if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                        raise ctypes.WinError(ctypes.get_last_error())
                    break
        finally:
            self.close(handle)

        while True:
            before = len(self.processes)
            for pid, parent in entries:
                if pid in self.processes or parent != self.root_pid and parent not in self.processes:
                    continue
                process = self.open(0x100000 | 0x1000 | 0x0001, False, pid)  # wait/query/terminate
                if not process:
                    continue
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                if self.path(process, 0, buffer, ctypes.byref(size)):
                    with suppress(OSError):
                        image = Path(buffer.value).resolve(strict=True)
                        since = self.root_created if parent == self.root_pid else self.processes[parent].created
                        created = self.created(process)
                        if image in self.images and created >= since:
                            self.processes[pid] = _OwnedProcess(self, process, pid, image, created)
                            continue
                self.close(process)
            if len(self.processes) == before:
                return

    def live(self, image=None):
        return [process for process in self.processes.values()
                if (image is None or process.image == image) and process.poll() is None]

    def cleanup(self):
        try:
            for process in reversed(self.live()):
                if not self.terminate(process.handle, 1) and process.poll() is None:
                    raise ctypes.WinError(ctypes.get_last_error())
                if self.wait(process.handle, 5000) != 0:
                    raise RuntimeError("An owned packaged handoff process did not exit during cleanup")
        finally:
            for process in self.processes.values():
                self.close(process.handle)


def _snapshot(path):
    try:
        return runtime.read_json(path)
    except (OSError, ValueError):
        return {}


def _new(root, before, *, suffix=""):
    entries = [path for path in root.glob(f"*{suffix}") if path.name not in before
               and ID.fullmatch(path.name.removesuffix(suffix))]
    if len(entries) > 1:
        raise RuntimeError(f"More than one new handoff record appeared in {root.name}")
    return entries[0] if entries else None


def _stock_image(stock_work, work):
    stock, game = stock_work / "stage", work / "stage"
    stock_exe, game_exe = stock / runtime.SHIPPING, game / runtime.SHIPPING
    for path in (stock_exe, game_exe):
        install_stock._ordinary(path)
    if runtime._file_sha256(stock_exe) != runtime._file_sha256(game_exe):
        raise ValueError("The dedicated stock replica and private stage must use the same stock executable")
    mods = stock_exe.parent / "ue4ss/Mods"
    install_stock._ordinary(mods, directory=True)
    if {entry.name for entry in mods.iterdir()} != {install_stock.MOD, "mods.txt"}:
        raise ValueError("Use the dedicated stock replica with only the title bootstrap installed")
    original = install_stock._read(mods / "mods.txt", 1024)
    if original.decode("utf-8-sig").strip() != f"{install_stock.MOD} : 1":
        raise ValueError("Enable the packaged title bootstrap in the dedicated stock replica")
    package = mods / install_stock.MOD
    install_stock._ordinary(package, directory=True)
    if install_stock._files(package) != {*install_stock.BOOTSTRAP_SCRIPTS, install_stock.TITLE_DLL, install_stock.WORKER}:
        raise ValueError("The stock replica must contain the exact full bootstrap package")
    for relative, expected in install_stock.BOOTSTRAP_SCRIPTS.items():
        if (package / relative).read_bytes().replace(b"\r\n", b"\n") != expected.read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError("The stock replica bootstrap scripts differ from this checkout")
    worker, adapter = package / install_stock.WORKER, package / install_stock.TITLE_DLL
    worker_bytes = install_stock._read(worker, install_stock.ARCHIVE_LIMIT)
    adapter_bytes = install_stock._read(adapter, 16 * 1024 * 1024)
    fingerprint = sha256(worker_bytes).hexdigest()
    if (not worker_bytes.startswith(b"MZ") or not adapter_bytes.startswith(b"MZ")
            or fingerprint.encode("ascii") not in adapter_bytes):
        raise ValueError("The stock title adapter and packaged worker pin differ")
    return stock_exe, game_exe, worker, fingerprint


async def _wait(probe, condition, timeout, description):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = probe()
        if condition(value):
            return value
        await asyncio.sleep(.1)
    raise TimeoutError(f"Timed out waiting for {description}")


async def verify(stock_work: Path, work: Path, original: Path):
    if os.name != "nt":
        raise OSError("This packaged stock handoff proof requires Windows")
    if (any(left.is_relative_to(right) for left in (stock_work, work, original)
            for right in (stock_work, work, original) if left != right)
            or len({stock_work, work, original}) != 3):
        raise ValueError("The stock replica, private co-op stage and original saves must be disjoint")
    if "steamapps" in {part.casefold() for part in stock_work.parts}:
        raise ValueError("Use a dedicated stock replica outside the Steam library")
    if not embedded_title_plan.installed(work):
        raise ValueError("The private stage must already contain the matched embedded runtime")
    stock_exe, game_exe, worker, worker_sha = _stock_image(stock_work, work)
    build = runtime.staged_build(work)
    before = saved_hashes(original)
    if not before:
        raise ValueError("Supply the original single-player Saved directory")
    name = "stock-handoff-" + uuid.uuid4().hex[:8]
    case = stock_work / "title-probes" / name
    install_stock._ordinary(case.parent, directory=True, missing=True)
    case.mkdir(parents=True)
    print(case, flush=True)
    profile = case / "stock-game-user"
    profile.mkdir()
    local = case / "local-app-data"
    trigger = case / "selection.txt"
    port = runtime.spare_udp_port()
    host_run = work / "campaigns" / name / "run"
    title_profile = work / "titles/bootstrap"
    title_bridges = title_profile / "run/game-bridge"
    receipt_root = work / "bootstrap-receipts"
    for directory in (title_bridges, receipt_root, host_run.parent):
        install_stock._ordinary(directory, directory=True, missing=True)
    if host_run.parent.exists():
        raise ValueError("The isolated test campaign already exists")
    old_titles = {path.name for path in title_bridges.iterdir()} if title_bridges.exists() else set()
    old_receipts = {path.name for path in receipt_root.iterdir()} if receipt_root.exists() else set()
    runtime.write_json(local / "SilverfishCoop/coordinator.json", {
        "schema": 1, "work": str(work), "join": None,
        "host": {"campaign": name, "bind": "127.0.0.1", "port": port},
    })

    env = {key: value for key, value in os.environ.items() if not key.startswith("SILVERFISH_COOP_")}
    env.update(LOCALAPPDATA=str(local), SILVERFISH_COOP_BOOTSTRAP_HEADLESS="1",
               SILVERFISH_COOP_BOOTSTRAP_TEST_TRIGGER=str(trigger))
    arguments = subprocess.list2cmdline([str(stock_exe)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed'
                  ' -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480'
                  ' -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,'
                  '[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    log = stock_exe.parent / "ue4ss/UE4SS.log"
    prior_log = log.read_bytes() if log.is_file() else b""
    evidence = {"verified": False, "build": build, "coordinator_sha256": worker_sha, "campaign": name, "port": port}
    stock = descendants = host = None
    handed_off = False

    def observe():
        descendants.refresh()
        try:
            current = log.read_bytes()
        except (FileNotFoundError, PermissionError):
            current = b""
        fresh = current[len(prior_log):] if current.startswith(prior_log) else current
        messages = [line.partition(LOG_PREFIX)[2].strip() for line in fresh.decode("utf-8", "replace").splitlines()
                    if LOG_PREFIX in line]
        evidence["stock_markers"] = messages
        receipt_path = _new(receipt_root, old_receipts, suffix=".json")
        receipt = _snapshot(receipt_path) if receipt_path else {}
        if receipt:
            if (type(receipt.get("schema")) is not int or receipt["schema"] != 1
                    or receipt_path.stem != receipt.get("generation")
                    or not ID.fullmatch(str(receipt.get("request_id", "")))
                    or "0" * 32 in (receipt["generation"], receipt["request_id"])):
                raise RuntimeError("The packaged coordinator published an invalid launch receipt")
            evidence["coordinator_receipt"] = receipt
        if receipt.get("state") == "failed" or any(message.startswith("native_launch_failed=")
                or message in ("state=error", "state=failed", "state=refused") for message in messages):
            raise RuntimeError("The packaged stock handoff failed before readiness")
        return messages, receipt

    try:
        stock = subprocess.Popen(arguments, executable=stock_exe, cwd=stock_exe.parent, env=env, startupinfo=startup,
                                 creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)
        evidence["stock_pid"] = stock.pid
        descendants = _Descendants(stock.pid, (game_exe, worker))

        def waiting_for_offer():
            if stock.poll() is not None:
                raise RuntimeError(f"The stock replica exited before offering co-op ({stock.returncode})")
            return observe()

        await _wait(waiting_for_offer, lambda report: "state=ready" in report[0], 45, "the fresh stock bootstrap offer")
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            markers, receipt = waiting_for_offer()
            if (receipt or _new(title_bridges, old_titles) or descendants.live()
                    or any(marker in markers for marker in TRANSITIONS) or trigger.exists()):
                raise AssertionError("The stock title launched before the headless selection")
            await asyncio.sleep(.1)
        evidence["no_launch_before_selection"] = True
        pending = trigger.with_suffix(".tmp")
        pending.write_bytes(b"open-coop\n")
        pending.rename(trigger)

        def waiting_for_adoption():
            markers, receipt = observe()
            if stock.poll() is None and "native_launch_started" in markers and "native_launch_ready" not in markers:
                evidence["stock_alive_awaiting_receipt"] = True
            if stock.poll() is not None and "native_launch_ready" not in markers:
                markers, receipt = observe()
            if stock.poll() is not None and (stock.returncode != 0 or "native_launch_ready" not in markers):
                raise RuntimeError("The stock title exited outside the verified coordinator handoff")
            return markers, receipt, _new(title_bridges, old_titles)

        markers, receipt, title_dir = await _wait(waiting_for_adoption, lambda report:
            all(marker in report[0] for marker in TRANSITIONS) and report[1].get("state") == "title_ready"
            and report[2] is not None and stock.poll() == 0, 125, "stock retirement after the verified coordinator receipt")
        if ([marker for marker in markers if marker in TRANSITIONS] != list(TRANSITIONS)
                or not evidence.get("stock_alive_awaiting_receipt")):
            raise AssertionError("The stock selection, live readiness wait and retirement were not observed in order")
        if "state=handed_off" in markers and markers.index("state=handed_off") < markers.index(TRANSITIONS[-1]):
            raise AssertionError("Stock retirement preceded the matching native readiness receipt")
        handed_off = True
        evidence["stock_returncode"] = stock.returncode
        evidence["stock_handed_off_lua_marker"] = "state=handed_off" in markers
        game_children = descendants.live(game_exe)
        if len(game_children) != 1 or not descendants.live(worker):
            raise RuntimeError("Expected the packaged coordinator and one live isolated title")
        adopted = game_children[0]
        evidence["isolated_title_pid"] = adopted.pid
        evidence["coordinator_pids"] = [process.pid for process in descendants.live(worker)]
        install_stock._ordinary(title_dir, directory=True)
        bridge = runtime.Bridge(title_dir, adopted)
        expected_saved = title_profile / "game-user/Saved"
        ready = await bridge.wait_for(lambda snapshot: _isolated_stock_title(snapshot, expected_saved)
                                      and snapshot.get(title.PANEL_KEY, {}).get("state") == "ready", timeout=8)
        panel = ready[title.PANEL_KEY]
        supervisor = _snapshot(title_dir / STATUS_FILE)
        if ((panel["actions"], panel["mounted"], supervisor.get("state"), supervisor.get("actions"))
                != (["host"], True, "offered", ["host"]) or _snapshot(host_run / "status.json")):
            raise AssertionError("Only the bound Host action may be offered before the fixture selects it")
        evidence["isolated_title"] = {"stock": ready[title.KEY], "panel": panel, "startup": ready[title.STARTUP_KEY]}
        selected = await bridge.command("native-title-panel-press-fixture", "host", timeout=6)
        if selected["ack"]["message"] != "qualified staged title control dispatched":
            raise AssertionError("The exact staged Host fixture did not dispatch")

        def host_handoff():
            observe()
            report = _snapshot(title_dir / STATUS_FILE)
            status = _snapshot(host_run / "status.json")
            if report.get("last_failure") or status.get("state") == "failed":
                raise RuntimeError("The staged Host selection reported a startup failure")
            return report, status

        report, status = await _wait(host_handoff, lambda proof:
            proof[0].get("state") == "ready" and proof[0].get("progress") == "hosting"
            and proof[1].get("state") == "hosting" and proof[1].get("active") is True,
            125, "the selected embedded Host session")
        attempt = report["attempt"]
        host = descendants.processes.get(status.get("pid"))
        host_bridge = _new(host_run / "game-bridge", set())
        if (not host or host.image != game_exe or host.poll() is not None or host.pid == adopted.pid
                or adopted.poll() is None or not host_bridge or attempt.get("action") != "host"
                or status.get("endpoint") != {"address": "127.0.0.1", "port": port}
                or status.get("game", {}).get("map") != OBSERVATORY):
            raise AssertionError("The Host handoff did not reach the exact independent embedded game")
        request = embedded_startup.parse_request((host_bridge / embedded_startup.CLAIM_FILE).read_bytes())
        if (request.role != "host" or request.pid != host.pid or request.profile != name or request.game_build != build
                or (request.title_generation, request.title_request_id) != (attempt["generation"], attempt["request_id"])
                or not embedded_startup.accepted(host_bridge, request)
                or (request.selection.bind, request.selection.public_port, request.selection.start) != ("127.0.0.1", port, "wanderer")):
            raise AssertionError("The embedded game did not adopt the exact selected Host request")
        runtime.verify_loopback_socket(host.pid, request.selection.private_port)
        saved = await submit(host_run, "checkpoint")
        if saved.get("ok") is not True or saved.get("revision", 0) < 1:
            raise AssertionError("The embedded Host session did not durably checkpoint")
        evidence["host"] = {"pid": host.pid, "attempt": attempt, "map": OBSERVATORY, "revision": saved["revision"],
                            "private_port": request.selection.private_port, "public_port": port}
        if (await submit(host_run, "stop")).get("ok") is not True:
            raise AssertionError("The embedded Host session refused graceful shutdown")

        def stopped():
            _, observed = observe()
            return observed, _snapshot(title_dir / STATUS_FILE), _snapshot(host_run / "status.json")

        observed, final_title, final_host = await _wait(stopped, lambda proof:
            proof[0].get("state") == "stopped" and proof[1].get("state") == "stopped"
            and proof[2].get("state") == "stopped" and proof[2].get("active") is False and not descendants.live(),
            30, "the stopped host, title supervisor and packaged coordinator")
        if ((observed["generation"], observed["request_id"]) != (receipt["generation"], receipt["request_id"])
                or final_title.get("attempt") != attempt or final_title.get("last_failure")
                or final_host.get("pid") != host.pid or owns_udp_port(host.pid, request.selection.private_port)):
            raise AssertionError("The same host, title and coordinator did not close cleanly")
        evidence["verified"] = evidence["host_stopped_cleanly"] = True
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        try:
            if host and host.poll() is None and _snapshot(host_run / "status.json").get("active") is True:
                with suppress(OSError, ValueError, RuntimeError, TimeoutError):
                    await asyncio.wait_for(submit(host_run, "stop"), 8)
            if stock is not None and stock.poll() is None:
                stock.terminate()
                await asyncio.to_thread(stock.wait, timeout=8)
            if descendants:
                descendants.refresh()
                descendants.cleanup()
        finally:
            evidence["stock_retired_after_receipt"] = handed_off
            evidence["original_saves_verified"] = saved_hashes(original) == before
            evidence["original_file_count"] = len(before)
            evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
            (case / "live-stock-handoff-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if not evidence["original_saves_verified"]:
            raise AssertionError("The original stock save hashes changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-work", type=Path, required=True, help="owned stock-layout replica workspace")
    parser.add_argument("--work", type=Path, required=True, help="matched private embedded stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="original Saved tree for read-only hashes")
    args = parser.parse_args()
    asyncio.run(verify(args.stock_work.resolve(strict=True), args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
