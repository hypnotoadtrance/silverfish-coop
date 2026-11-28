"""Read the immediate stock pickup preview from private portable test instances."""

from contextlib import contextmanager


PREVIEW_FIXTURE = "native-loose-pickup-preview-fixture"


@contextmanager
def pickup_preview_overlay(game, overlay, *, projected_drops, track_drop_motion=False):
    with overlay(game, "native_authored_pickup.lua", instrument_preview):
        if projected_drops:
            def projected(source):
                source = instrument_drop_press(source)
                return instrument_drop_motion(source) if track_drop_motion else source

            with overlay(game, "native_guest_drop_projection.lua", projected):
                yield
        else:
            yield


def instrument_drop_press(source):
    module = "local projection = {}\n"
    press = "            local ok, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)"
    taken = '            return "world-loot:" .. epoch .. ":" .. id .. ":" .. tostring(sequence)'
    assert all(source.count(anchor) == 1 for anchor in (module, press, taken))
    return (source.replace(module, module + "local preview_fixture\n")
            .replace(press, """            preview_fixture = {id = id, started = os.clock(), collision = actor:GetActorEnableCollision()}
""" + press)
            .replace(taken, """            if preview_fixture and preview_fixture.id == id then
                local active, seen_epoch, seen_id, name = SilverfishReadNativeGuestDropProjection(item.slot)
                local path = active and seen_epoch == epoch and seen_id == id and name:match("^%S+ (.+)$")
                local actor = path and StaticFindObject(path)
                if valid(actor) and actor:GetFullName() == name then
                    SilverfishPickupPreviewFixtureProjected(id, actor.bHidden,
                        preview_fixture.collision and actor:GetActorEnableCollision(),
                        (os.clock() - preview_fixture.started) * 1000)
                end
                preview_fixture = nil
            end
""" + taken))


def instrument_drop_motion(source):
    anchor = "            local was_pickable = previous ~= nil and not previous.error and not previous.moving\n"
    assert source.count(anchor) == 1
    return source.replace(anchor, '''            if phase == "connected" and ok and item.class:match("%.MeleeWep_OldPipe_C$") then
                local active, _, identity, name = SilverfishReadNativeGuestDropProjection(slot)
                local path = active and identity == id and name:match("^%S+ (.+)$")
                local actor = path and StaticFindObject(path)
                if valid(actor) and not actor.bHidden then
                    local output = assert(io.open(os.getenv("SILVERFISH_COOP_BRIDGE") .. "/portable-enemy-drop-motion.tsv", "ab"))
                    local location = actor:K2_GetActorLocation()
                    output:write(id, "\\t", item.moving and "moving" or "resting", "\\t",
                        ("%.3f"):format(location.Z), "\\t", name, "\\n")
                    output:close()
                end
            end
''' + anchor)


def recorded_drop_motion(guest, identity):
    report = guest.bridge.directory / "portable-enemy-drop-motion.tsv"
    return [(phase, float(height), actor) for candidate, phase, height, actor in
            (line.split("\t") for line in report.read_text(encoding="utf-8").splitlines()) if candidate == identity]


async def read_preview(guest, target):
    reply = await guest.bridge.command(PREVIEW_FIXTURE, "read", target, timeout=4, update_status=False)
    meshes, collision, restored, latency = reply["ack"]["message"].split("|")
    return int(meshes), int(collision), int(restored), float(latency)


async def verified_drop_preview(guest, identity):
    hidden, collision, restored, latency = await read_preview(guest, identity)
    assert hidden == 1 and collision == 1 and restored == 0 and 0 <= latency < 100, (hidden, collision, restored, latency)
    return {"actor_hidden": True, "delay_ms": latency, "collision_preserved": True}


def instrument_preview(source):
    def insert(before, after):
        nonlocal source
        assert source.count(before) == 1, before
        source = source.replace(before, after)

    anchor = "local previews, mesh_class = {}, nil"
    insert(anchor, anchor + "\nlocal preview_probe = {}")
    anchor = '    if action == "native-pickup-cancel-preview" then'
    insert(anchor, """    if action == "native-loose-pickup-preview-fixture" and fixtures and title_fixtures and #fields == 4 then
        local mode, target = fields[3], fields[4]
        if phase == "hosting" and mode == "reject" then
            preview_probe.reject = target
            return "next matching placed pickup will be refused"
        end
        local expected = preview_probe.target == target or #target == 32 and target:match("^%x+$")
            and preview_probe.target and preview_probe.target:match("^world%-loot:%x+:" .. target .. ":%d+$")
        if phase == "connected" and mode == "read" and expected then
            return ("%d|%d|%d|%.1f"):format(preview_probe.meshes, preview_probe.collision_kept and 1 or 0,
                preview_probe.rolled_back and 1 or 0, preview_probe.latency or -1)
        end
        error("placed pickup preview probe scope unavailable", 0)
    end
""" + anchor)
    anchor = '        restore_preview(fields[3])\n        return "pickup preview released"'
    insert(anchor, """        local prior = previews[fields[3]]
        restore_preview(fields[3])
        if prior and preview_probe.target == fields[3] then
            preview_probe.rolled_back = true
            for _, mesh in ipairs(prior.meshes) do
                if not valid(mesh) or not mesh:IsVisible() then preview_probe.rolled_back = false end
            end
        end
        return "pickup preview released"
""".rstrip())
    anchor = '    if action == "native-loose-pickup-claim" then'
    insert(anchor, anchor + """
        if phase == "hosting" and preview_probe.reject and fields[5] == preview_probe.reject then
            preview_probe.reject = nil
            error("loose_pickup_source_unavailable", 0)
        end""")
    anchor = "local preview_probe = {}"
    insert(anchor, anchor + """
local function preview_pressed()
    preview_probe.pressed = os.clock()
end
SilverfishPickupPreviewFixtureProjected = function(target, hidden, collision, latency)
    preview_probe.target, preview_probe.meshes = target, hidden and 1 or 0
    preview_probe.collision_kept, preview_probe.rolled_back = collision, false
    preview_probe.latency = latency
end
""")
    anchor = '        return stock_input_fixture.press_world_loot(state, pawn, actor, actor.Box, function()'
    insert(anchor, anchor + "\n            preview_pressed()")
    anchor = '    local preview = {actor = actor, meshes = {}, expires = os.clock() + 1.5}'
    insert(anchor, '    local collision = actor:GetActorEnableCollision()\n' + anchor)
    anchor = '    if #preview.meshes == 0 then previews[target] = nil end'
    insert(anchor, """    local hidden = 0
    for _, mesh in ipairs(preview.meshes) do
        if not mesh:IsVisible() then hidden = hidden + 1 end
    end
    preview_probe.target, preview_probe.meshes = target, hidden
    preview_probe.collision_kept = actor.bHidden == false and actor:GetActorEnableCollision() == collision and collision
    preview_probe.rolled_back = false
    preview_probe.latency = preview_probe.pressed and (os.clock() - preview_probe.pressed) * 1000
""" + anchor)
    return source

