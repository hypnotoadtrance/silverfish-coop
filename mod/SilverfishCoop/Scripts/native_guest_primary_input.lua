local primary = {}
local loot = require("native_loot")
local native_stock_door = require("native_stock_door")
local weapon_profiles = require("guest_weapon_registry")
local shot_view = require("guest_shot_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local held_enabled = enabled and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local camera_map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local camera_quest = "Photo_DB_Turrets"
local event_path, held_event_path, is_downed, binding, fixture_proof, held_fixture_proof, melee_fixture_proof, shovel_intent
local pending_pry
local publications = {}
local held_edges = {press = true, release = true, cancel = true}
local held_proofs = {["mapped-key"] = true, ["edge-window"] = true, ["headless-fixture"] = true}
local melee_kinds = {["old-pipe"] = true, melee = true}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function valid_guid(guid)
    return type(guid) == "string" and #guid == 32 and not guid:find("[^0-9A-F]") and guid:find("[^0]") ~= nil
end

local function valid_sequence(sequence)
    return type(sequence) == "number" and math.tointeger(sequence) and sequence > 0 and sequence < 2 ^ 53
end

local function context(state, phase)
    if not enabled or not event_path or phase ~= "connected" or not state.listening or state.server ~= true
        or not valid(state.world) or type(state.map) ~= "string" or #state.map > 256
        or not state.map:match("^/Game/Classes/Maps/[%w_/]+$")
        or state.world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_path) or not exact(controller, controller_path) or not same(detail.controller_object, controller)
        or pawn:HasAuthority() or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return end
    return {world = state.world, map = state.map, name = detail.name, pawn = pawn, controller = controller}
end

local function same_context(left, right)
    return left and right and left.map == right.map and left.name == right.name
        and same(left.world, right.world) and same(left.controller, right.controller) and same(left.pawn, right.pawn)
end

local function standing(current)
    return current and current.controller.Loading == false and current.pawn.Dead == false
        and type(is_downed) == "function" and is_downed(current.pawn) == false
end

local function can_use(current)
    return standing(current) and current.pawn.IsWeaponBlocked == false
end

local function emit(path, fields)
    local record = table.concat(fields, "\t") .. "\n"
    if embedded then return SilverfishEnqueueLocalBridgeInput(record) == true end
    local file = io.open(path, "ab")
    if not file then return false end
    local written = file:write(record)
    local closed = file:close()
    return written and closed
end

local function commit(channel, address, sequence)
    if embedded and SilverfishCommitNativeGuestPrimaryInput(address, sequence, channel) ~= true then
        error("The queued stock primary sequence no longer matches the native input head", 0)
    end
end

local function publish(channel, path, current, address, sequence, fields)
    if not emit(path, fields) then return false end
    commit(channel, address, sequence)
    if fixtures then publications[channel] = {context = current, sequence = sequence, guid = binding.guid} end
    return true
end

local function drain_held(current, address)
    local take = embedded and SilverfishPeekNativeGuestHeldFireIntent or SilverfishTakeNativeGuestHeldFireIntent
    if not held_event_path or not weapon_profiles.automatic[binding.kind] or type(take) ~= "function" then
        return 0
    end
    local emitted, last = 0, nil
    for _ = 1, 8 do
        local sequence, owner, guid, edge, stroke, x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz = take(address)
        if not valid_sequence(sequence) or not valid_sequence(stroke) or owner ~= address or guid ~= binding.guid
            or not held_edges[edge] or (edge == "press" and stroke ~= sequence)
            or (edge ~= "press" and stroke >= sequence) then break end
        local view = shot_view.format(x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz)
        if not publish("held", held_event_path, current, address, sequence,
            {"native-guest-held-fire-intent", sequence, current.map, current.name,
            guid, edge, stroke, view}) then break end
        emitted, last = emitted + 1, sequence
    end
    return emitted, last
end

local function sample_held_view(current, address)
    if not embedded or not held_enabled or not weapon_profiles.automatic[current.kind]
        or type(SilverfishSampleNativeGuestHeldAim) ~= "function" then return end
    local stroke, x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz = SilverfishSampleNativeGuestHeldAim(address)
    if not valid_sequence(stroke) then return end
    local view = shot_view.format(x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz)
    if view and (current.view_stroke ~= stroke or current.shot_view ~= view)
        and emit(nil, {"native-guest-held-aim", current.map, current.name, current.guid, stroke, view}) then
        current.view_stroke, current.shot_view = stroke, view
    end
end

local function drain_melee(current, address)
    if not melee_kinds[current.kind] or type(SilverfishTakeNativeGuestMeleeInput) ~= "function"
        or type(SilverfishEnqueueLocalBridgeInput) ~= "function" then return end
    local emitted, last = 0, nil
    for _ = 1, 8 do
        local sequence, owner, guid, edge, stroke, duration = SilverfishTakeNativeGuestMeleeInput(address)
        if not valid_sequence(sequence) or not valid_sequence(stroke) or owner ~= address or guid ~= current.guid
            or not held_edges[edge] or (edge == "press" and stroke ~= sequence)
            or (edge ~= "press" and stroke >= sequence) or math.type(duration) ~= "integer"
            or duration < 0 or duration > 9000 or (edge == "press" and duration ~= 0) then break end
        local record = table.concat({"native-guest-melee-edge", sequence, current.map, current.name, guid,
            current.kind, edge, stroke, duration}, "\t") .. "\n"
        if not SilverfishEnqueueLocalBridgeInput(record) then break end
        emitted, last = emitted + 1, sequence
    end
    return emitted, last
end

function primary.reset()
    if binding and type(SilverfishClearNativeGuestPrimaryScope) == "function" then
        SilverfishClearNativeGuestPrimaryScope()
    end
    binding, fixture_proof, held_fixture_proof, melee_fixture_proof, shovel_intent = nil, nil, nil, nil, nil
    pending_pry = nil
    if fixtures then publications = {} end
end

local function pry_name(current, kind)
    local pawn = current.pawn
    local actor = pawn.ActorBeingLookedAt
    local name = valid(actor) and actor:GetFullName()
    local profile = name and loot.profile(current.map, name)
    if not profile or not profile.lockable or actor.Locked ~= true then
        local component = pawn.InteractionTarget_Component
        local owner = valid(component) and component:GetOwner()
        local focused = valid(owner) and loot.profile(current.map, owner:GetFullName())
        if pawn.CanInteract == true and focused and focused.lockable
            and owner.Locked == true and same(component, owner.Box) then
            actor, name = owner, focused.name
        else
            name = native_stock_door.pry_target(pawn, kind)
        end
    end
    if name and actor.Locked == true and (kind ~= "crowbar" or pawn.CanInteract == true) then return name end
end

local function drain_taps(current, address)
    if not standing(current) then
        primary.reset()
        return 0
    end
    local take = embedded and SilverfishPeekNativeGuestPrimaryIntent or SilverfishTakeNativeGuestPrimaryIntent
    local emitted = 0
    for _ = 1, 8 do
        local sequence, owner, guid, kind, x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz = take(address)
        if not valid_sequence(sequence) or owner ~= address or guid ~= binding.guid or kind ~= binding.kind then break end
        if not can_use(current) then break end
        if kind == "blowtorch" or kind == "crowbar" then
            local name = pending_pry and pending_pry.sequence == sequence and pending_pry.name or pry_name(current, kind)
            if not name then
                pending_pry = nil
                commit("tap", address, sequence)
                break
            end
            if not publish("tap", event_path, current, address, sequence,
                {"native-guest-primary-intent", sequence, current.map, current.name, guid, kind, name}) then
                if embedded then pending_pry = {sequence = sequence, name = name} end
                break
            end
            pending_pry = nil
        elseif kind == "spade" then
            shovel_intent = {sequence = sequence, expires = os.clock() + 1.5}
            commit("tap", address, sequence)
        else
            local view = shot_view.format(x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz)
            if not publish("tap", event_path, current, address, sequence,
                {"native-guest-primary-intent", sequence, current.map, current.name, guid, kind, view}) then break end
        end
        emitted = emitted + 1
    end
    return emitted
end

function primary.sync(state, phase)
    if not binding then return end
    local current = context(state, phase)
    if not same_context(current, binding) then
        primary.reset()
        return
    end
    local address = current.pawn:GetAddress()
    local held_emitted, held_last = drain_held(current, address)
    if not standing(current) then
        primary.reset()
    end
    return held_emitted, held_last
end

local function fixture_edge(state, phase, current, edge)
    local ok, status, sequence, stroke = SilverfishProbeNativeGuestHeldFire(current.pawn:GetAddress(), edge)
    if ok ~= true or status ~= "stock_" .. edge or not valid_sequence(sequence) or not valid_sequence(stroke) then
        error("The stock automatic fixture edge was unverified (" .. tostring(status) .. ")", 0)
    end
    local held, last = primary.sync(state, phase)
    if held ~= 1 or last ~= sequence then error("The stock automatic fixture edge could not be published", 0) end
    held_fixture_proof = {context = current, guid = binding.guid, sequence = sequence, stroke = stroke, edge = edge}
    return held_fixture_proof
end

function primary.command(fields, state, phase)
    local action = fields[2]
    if action == "native-guest-primary-clear" and #fields == 2 then
        primary.reset()
        return "stock guest primary scope cleared"
    end
    if action ~= "native-guest-primary-scope" and action ~= "native-guest-primary-input-fixture"
        and action ~= "native-guest-primary-publication-fixture"
        and action ~= "native-guest-held-fire-fixture" and action ~= "native-guest-melee-input-fixture" then return end
    local current = context(state, phase)
    if not standing(current) then
        primary.reset()
        error("Stock primary input requires the exact standing connected guest", 0)
    end
    if action == "native-guest-primary-publication-fixture" then
        if not fixtures or #fields ~= 3 or fields[3] ~= current.name or not same_context(current, binding) then
            error("The stock primary publication readback requires the exact scoped headless guest", 0)
        end
        return "stock primary publication read"
    end
    -- A stock weapon lock can outlast a scope renewal. Keep the same
    -- authenticated equipment bound so input resumes when the lock clears.
    if action ~= "native-guest-primary-scope" and not can_use(current) then
        error("Stock primary input is temporarily blocked by the equipped player's state", 0)
    end
    if action == "native-guest-melee-input-fixture" then
        local edge = fields[4]
        if not fixtures or #fields ~= 4 or fields[3] ~= current.name or not same_context(current, binding)
            or not melee_kinds[binding.kind] or (edge ~= "press" and edge ~= "release" and edge ~= "tap")
            or type(SilverfishProbeNativeGuestMeleeInput) ~= "function" then
            error("The stock melee fixture requires the exact scoped headless melee guest", 0)
        end
        local function probe(one_edge)
            local address = current.pawn:GetAddress()
            local ok, status, sequence, stroke = SilverfishProbeNativeGuestMeleeInput(address, one_edge)
            if ok ~= true or status ~= "stock_" .. one_edge or not valid_sequence(sequence) or not valid_sequence(stroke) then
                error("The stock melee fixture edge was unverified (" .. tostring(status) .. ")", 0)
            end
            local emitted, last = drain_melee(binding, address)
            if emitted ~= 1 or last ~= sequence then error("The stock melee fixture edge could not be published", 0) end
            melee_fixture_proof = {context = current, guid = binding.guid, sequence = sequence, stroke = stroke, edge = one_edge}
        end
        if edge == "tap" then
            probe("press")
            probe("release")
        else
            probe(edge)
        end
        return "stock guest melee fixture observed"
    end
    if action == "native-guest-held-fire-fixture" then
        local edge, duration = fields[4], math.tointeger(fields[5])
        if not held_enabled or not fixtures or (#fields ~= 4 and #fields ~= 5) or fields[3] ~= current.name
            or not same_context(current, binding) or not weapon_profiles.automatic[binding.kind] or (edge ~= "press" and edge ~= "release")
            or (#fields == 5 and (edge ~= "press" or not duration or duration < 300 or duration > 1800
                or type(ExecuteInGameThreadWithDelay) ~= "function"))
            or type(SilverfishProbeNativeGuestHeldFire) ~= "function" then
            error("The stock held input fixture requires the exact scoped headless guest with a stock automatic weapon", 0)
        end
        local press = fixture_edge(state, phase, current, edge)
        if duration then
            ExecuteInGameThreadWithDelay(duration, function()
                if held_fixture_proof ~= press or not same_context(current, binding)
                    or not can_use(current) or binding.guid ~= press.guid then return end
                local owner_address = current.pawn:GetAddress()
                local stroke, owner, guid = SilverfishReadNativeGuestHeldFire(owner_address)
                if stroke ~= press.stroke or owner ~= owner_address or guid ~= press.guid then return end
                fixture_edge(state, phase, current, "release")
            end)
        end
        return "stock guest automatic " .. edge .. " verified"
    end
    if action == "native-guest-primary-input-fixture" then
        local pry_tool = binding and (binding.kind == "blowtorch" or binding.kind == "crowbar")
        if not fixtures or fields[3] ~= current.name or not same_context(current, binding)
            or #fields ~= (pry_tool and 4 or 3)
            or type(SilverfishProbeNativeGuestPrimaryInput) ~= "function" then
            error("The stock primary input fixture requires the exact scoped headless client pawn", 0)
        end
        local function probe()
            local ok, status, sequence, wrappers = SilverfishProbeNativeGuestPrimaryInput(current.pawn:GetAddress())
            if ok ~= true or status ~= "stock_pressed" or not valid_sequence(sequence)
                or wrappers ~= (binding.kind == "crowbar" and 1 or 2) then
                error("The stock primary input fixture did not verify (" .. tostring(status) .. ")", 0)
            end
            -- A forced pry trace only exists inside the scoped fixture callback.
            -- Ordinary taps use the same drain as a real input.
            if pry_tool and drain_taps(current, current.pawn:GetAddress()) ~= 1 then
                error("The stock primary fixture intent could not be published", 0)
            end
            fixture_proof = {context = current, guid = binding.guid, sequence = sequence, wrappers = wrappers}
        end
        if pry_tool then
            local target = fields[4]
            local profile = loot.profile(current.map, target)
            if profile and profile.lockable then
                require("native_loot_crate_input").pry_tool_input_fixture(state, phase, target, probe)
            else
                native_stock_door.input_fixture(state, phase, target, current.pawn, probe)
            end
        else
            probe()
        end
        return "stock guest primary input verified"
    end

    local guid, kind, extra = fields[5], fields[6], fields[7]
    local camera = kind == "camera"
    local mode = weapon_profiles.automatic[kind] and extra
    local melee = melee_kinds[kind]
    if #fields ~= ((camera or mode) and 7 or 6) or fields[3] ~= current.map or fields[4] ~= current.name or not valid_guid(guid)
        or (camera and (current.map ~= camera_map or extra ~= camera_quest))
        or (mode and mode ~= "semi" and mode ~= "auto")
        or (not camera and not melee and kind ~= "blowtorch" and kind ~= "crowbar" and kind ~= "spade"
            and not weapon_profiles.paths[kind])
        or (held_enabled and weapon_profiles.automatic[kind] and (type(SilverfishTakeNativeGuestHeldFireIntent) ~= "function"
            or type(SilverfishReadNativeGuestHeldFire) ~= "function"))
        or type(SilverfishSetNativeGuestPrimaryScope) ~= "function"
        or (embedded and (type(SilverfishEnqueueLocalBridgeInput) ~= "function"
            or type(SilverfishPeekNativeGuestPrimaryIntent) ~= "function" or type(SilverfishCommitNativeGuestPrimaryInput) ~= "function"
            or (held_enabled and weapon_profiles.automatic[kind] and type(SilverfishPeekNativeGuestHeldFireIntent) ~= "function")))
        or type(SilverfishTakeNativeGuestPrimaryIntent) ~= "function" then
        primary.reset()
        error("The authenticated stock equipment selection is unavailable", 0)
    end
    if binding and (not same_context(current, binding) or binding.guid ~= guid or binding.kind ~= kind) then
        primary.reset()
    end
    if mode then require("native_guest_weapon").project_fire_mode(state, phase, guid, mode) end
    local accepted, status
    if camera then
        if not valid(StaticFindObject(camera_path)) then LoadAsset(camera_path) end
        accepted, status = SilverfishSetNativeGuestPrimaryScope(current.pawn:GetAddress(),
            current.controller:GetAddress(), current.world:GetAddress(), guid, kind, extra)
    else
        accepted, status = SilverfishSetNativeGuestPrimaryScope(current.pawn:GetAddress(),
            current.controller:GetAddress(), current.world:GetAddress(), guid, kind)
    end
    if not accepted or (status ~= "stock_held" and status ~= "host_equipped") then
        primary.reset()
        error(tostring(status or "stock_guest_primary_scope_unavailable"), 0)
    end
    current.guid, current.kind, current.mode = guid, kind, status
    if binding then current.view_stroke, current.shot_view = binding.view_stroke, binding.shot_view end
    binding = current
    return "stock guest primary scope refreshed"
end

function primary.rows(state, phase, status_row)
    local current = context(state, phase)
    if not same_context(current, binding) then return {} end
    local rows = { status_row("native-guest-primary-scope", current.map, current.name, binding.guid,
        binding.kind, binding.mode) }
    if binding.kind == "spade" and shovel_intent and os.clock() < shovel_intent.expires and can_use(current) then
        rows[#rows + 1] = status_row("native-guest-spade-intent", current.map, current.name, binding.guid, shovel_intent.sequence)
    end
    if fixture_proof and same_context(current, fixture_proof.context) and fixture_proof.guid == binding.guid then
        rows[#rows + 1] = status_row("native-guest-primary-fixture", current.map, current.name, fixture_proof.guid,
            fixture_proof.sequence, fixture_proof.wrappers)
    end
    if held_enabled and weapon_profiles.automatic[binding.kind] and can_use(current)
        and type(SilverfishReadNativeGuestHeldFire) == "function" then
        local address = current.pawn:GetAddress()
        local stroke, owner, guid, proof = SilverfishReadNativeGuestHeldFire(address)
        if valid_sequence(stroke) and owner == address and guid == binding.guid and held_proofs[proof]
            and (proof ~= "headless-fixture" or fixtures) then
            rows[#rows + 1] = status_row("native-guest-held-fire", current.map, current.name, guid, stroke, "held", proof)
        end
    end
    if held_fixture_proof and same_context(current, held_fixture_proof.context) and held_fixture_proof.guid == binding.guid then
        rows[#rows + 1] = status_row("native-guest-held-fire-fixture", current.map, current.name, held_fixture_proof.guid,
            held_fixture_proof.sequence, held_fixture_proof.stroke, held_fixture_proof.edge)
    end
    if melee_fixture_proof and same_context(current, melee_fixture_proof.context) and melee_fixture_proof.guid == binding.guid then
        rows[#rows + 1] = status_row("native-guest-melee-input-fixture", current.map, current.name, melee_fixture_proof.guid,
            melee_fixture_proof.sequence, melee_fixture_proof.stroke, melee_fixture_proof.edge)
    end
    if fixtures then
        for channel, published in pairs(publications) do
            if same_context(current, published.context) and published.guid == binding.guid then
                rows[#rows + 1] = status_row("native-guest-primary-publication", current.map, current.name,
                    binding.guid, channel, published.sequence, embedded and "memory" or "file")
            end
        end
    end
    return rows
end

function primary.start(bridge, downed)
    event_path = enabled and bridge and bridge ~= "" and bridge .. "/native-guest-primary-events" or nil
    held_event_path = event_path and held_enabled and bridge .. "/native-guest-held-fire-events" or nil
    is_downed = downed
    if not event_path then return end
    LoopInGameThreadWithDelay(20, function()
        local current = binding
        if not current or not valid(current.pawn) then return end
        local ok, failure = pcall(function()
            local address = current.pawn:GetAddress()
            drain_melee(current, address)
            drain_taps(current, address)
            if embedded and binding == current then
                drain_held(current, address)
                sample_held_view(current, address)
            end
        end)
        if not ok then
            primary.reset()
            print("[SilverfishCoop] Stock primary input unavailable: " .. tostring(failure) .. "\n")
        end
    end)
end

return primary
