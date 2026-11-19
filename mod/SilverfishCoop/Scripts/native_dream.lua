local dream = {}
local bed = require("native_bed")
local transport = require("native_transport")
local routes = require("zone_maps")
local terminal = require("native_nothing_return")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local exchange = "/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P"
local rook = "/Game/Classes/Maps/Dreams/TheRook_P"
local pyroclast = "/Game/Classes/Maps/Dreams/Pyroclast_P"
local slate = "/Game/Classes/Maps/Dreams/Slate_P"
local black_ocean = "/Game/Classes/Maps/Dreams/BlackOcean_P"
local nothing = "/Game/Classes/Maps/Dreams/Nothing_P"
local slate_return_plane = -18785.228689655527
local countdown_name = "Dream_CountDown_BP_C_1"
local countdown_class = "Dream_CountDown_BP_C"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local stock_code_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local stock_global_ping = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C:GlobalPing"
local profiles = {
    [exchange] = { level = "Exchange_01_P", multiplier = 1, startup = 3, fade = 3, early_power = true },
    [rook] = { level = "TheRook_P", multiplier = 2, startup = 3, fade = 3, early_power = true },
    [pyroclast] = { level = "Pyroclast_P", multiplier = 3, startup = 3, fade = 3 },
    [slate] = { level = "Slate_P", class = "Slate_P_C", actor = "Slate_P_C_0", multiplier = 1, startup = 0, fade = 2 },
    [black_ocean] = {
        level = "BlackOcean_P", class = "BlackOcean_P_C", actor = "BlackOcean_P_C_0",
        fixed_seconds = 160, startup = 0, fade = 2,
    },
    [nothing] = { level = "Nothing_P", fixed_seconds = 0, startup = 0, fade = 0 },
}
local tarot_routes = {
    { flag = "Tarot_Obelisk", destination = pyroclast },
    { flag = "Tarot_Night", destination = slate },
    { flag = "Tarot_BlackStag", destination = black_ocean },
    { flag = "Tarot_Rook", destination = rook },
}
local stock_items = {
    stardust = { seconds = 90, reputation = 10 },
    ["fruiting-stardust"] = { seconds = 180, reputation = 20 },
}
local continuation = { [exchange] = rook, [rook] = pyroclast, [pyroclast] = slate,
    [slate] = black_ocean, [black_ocean] = nothing, [nothing] = nothing }
local current

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function exact_world(world, map)
    local package_name = map:match("/([^/]+)$")
    return package_name and valid(world) and world:GetFullName() == "World " .. map .. "." .. package_name
end

local function host_context(state, phase, map, require_fresh)
    local host, fresh = transport.scoped_host(state, phase)
    if not enabled or not host or (require_fresh and not fresh) or state.map ~= map
        or not exact_world(state.world, map) or not host.pawn:GetFullName():match("^Player_FP_C ") then
        error("An authoritative native Stardust host is required in the qualified map", 0)
    end

    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not same(host.pawn.Save, save) or not save:GetFullName():match("^SaveGame_BP_C ") then
        error("The Stardust route requires the canonical native host save", 0)
    end
    return host, instance
end

function dream.supports(map)
    return profiles[map] ~= nil
end

local function placed_actor(world, map, class, name, only)
    local expected = class .. " " .. map .. "." .. profiles[map].level .. ":PersistentLevel." .. name
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) then
            if actor:GetFullName() == expected then
                if match then error("The qualified stock dream actor is ambiguous", 0) end
                match = actor
            elseif only then
                error("The dream world contains an unqualified stock countdown", 0)
            end
        end
    end
    if not match then error("The qualified stock dream actor is unavailable: " .. name, 0) end
    return match
end

local function require_guard()
    if type(SilverfishInspectNativeExchangeCountdown) ~= "function"
        or type(SilverfishTakeNativeExchangeReturn) ~= "function" then
        error("The installed native dream countdown guard is unavailable", 0)
    end
end

local function inspect(state)
    require_guard()
    if not valid(current.actor) then
        local profile = profiles[state.map]
        current.actor = placed_actor(state.world, state.map, profile.class or countdown_class,
            profile.actor or countdown_name, true)
        if not profile.class and current.actor.Time_Mod ~= profile.multiplier then
            current.actor = nil
            error("The native dream countdown multiplier has changed", 0)
        end
    end
    return SilverfishInspectNativeExchangeCountdown(current.actor:GetAddress())
end

local function update_slate_descent(state)
    local host = state.local_pawn and state.local_pawn.pawn
    if not valid(host) or not host:HasAuthority() then return false end
    local lead = host.CharacterMovement
    if not valid(lead) then return false end
    local point, speed = host:K2_GetActorLocation(), lead.Velocity
    local host_falling = current.last_z and point.Z < current.last_z - 25
    current.last_z = point.Z
    current.slate_state = host_falling and "host_descending" or "host_stationary"
    if not finite(point.Z) or point.Z > -1500 then return false end
    local moved = false
    for _, detail in ipairs(state.guests) do
        local guest = detail.pawn
        local record = current.slate_guests[detail.name]
        if record and record.address == guest:GetAddress() and guest:HasAuthority()
            and not detail.controller_object:IsLocalController() and same(detail.controller_object.Pawn, guest) then
            local follow, location = guest.CharacterMovement, guest:K2_GetActorLocation()
            local guest_falling = record.last_z and location.Z < record.last_z - 25
            record.last_z = location.Z
            current.slate_state = string.format("%s %s dx=%.1f dy=%.1f dz=%.1f",
                host_falling and "host_descending" or "host_stationary", guest_falling and "guest_descending" or "guest_stationary",
                location.X - point.X, location.Y - point.Y, location.Z - point.Z)
            -- Below the stock return plane there is no remaining descent encounter to play.
            local past_return_plane = point.Z <= slate_return_plane and location.Z <= slate_return_plane
            if valid(follow) and (host_falling and guest_falling or past_return_plane)
                and finite(location.Z) and location.Z <= -1500
                and math.abs(location.Z - point.Z) > 240
                and (location.X - point.X) ^ 2 + (location.Y - point.Y) ^ 2 <= 1450 ^ 2
                and guest:K2_TeleportTo({ X = location.X, Y = location.Y, Z = point.Z }, guest:K2_GetActorRotation()) then
                local velocity = follow.Velocity
                if speed and finite(speed.Z) and speed.Z < 0 then
                    follow.Velocity = { X = velocity.X, Y = velocity.Y, Z = speed.Z }
                end
                guest:ForceNetUpdate()
                record.last_z = point.Z
                current.slate_adjustments = current.slate_adjustments + 1
                moved = true
            end
        end
    end
    return moved
end

function dream.sync(state, phase)
    if not enabled or (phase ~= "hosting" and phase ~= "connected") or not dream.supports(state.map)
        or not exact_world(state.world, state.map) then
        current = nil
        return
    end

    if not current or not same(current.world, state.world) then
        current = { world = state.world, map = state.map, early_requested = false,
            slate_guests = {}, slate_adjustments = 0, slate_state = "waiting", slate_enrolled = 0 }
    end
    if state.map == nothing then
        local ready, detail, actor = terminal.guard(state, phase)
        current.status, current.detail, current.actor_name = ready and "ready" or "blocked", detail, actor
        return
    end
    local ok, ready, detail = pcall(inspect, state)
    current.status = ok and ready == true and "ready" or "blocked"
    current.detail = ok and (type(detail) == "string" and detail or "") or tostring(ready)
    if current.status == "ready" and phase == "hosting" then
        local took, requested = pcall(SilverfishTakeNativeExchangeReturn)
        if took then
            current.early_requested = current.early_requested or requested == true
        else
            current.status, current.detail = "blocked", tostring(requested)
        end
        if state.map == slate and current.status == "ready" then return update_slate_descent(state) end
    end
end

function dream.rows(status_row)
    if not enabled then return {} end
    if not current then return { status_row("native-dream-isolation", "inactive", "", "", "", 0) } end
    local actor = current.actor_name or (valid(current.actor) and (profiles[current.map].actor or countdown_name) or "")
    local rows = { status_row("native-dream-isolation", current.status, current.map, actor, current.detail,
        current.early_requested and 1 or 0) }
    if current.map == slate then
        rows[#rows + 1] = status_row("native-dream-formation", slate, current.slate_adjustments,
            current.slate_enrolled, current.slate_state)
    end
    return rows
end

local function signed_int(value)
    return type(value) == "number" and math.tointeger(value) and value >= -2147483648 and value <= 2147483647
end

local function read_flag(instance, flag)
    local out = {}
    instance:Check_SaveFlag(FName(flag), out, out)
    if type(out.Found) ~= "boolean" or not signed_int(out.Value) then
        error("The host's stock Stardust Tarot flag could not be verified: " .. flag, 0)
    end
    return out.Found, out.Value
end

local function selection(instance, source)
    for _, choice in ipairs(tarot_routes) do
        local found, value = read_flag(instance, choice.flag)
        if found and value >= 1 then
            return choice.destination, choice.flag
        end
    end
    return continuation[source] or exchange, ""
end

function dream.source(state, phase, kind)
    local _, instance = host_context(state, phase, state.map, true)
    local item = stock_items[kind]
    if not item or not routes[state.map] then
        error("Stardust requires a supported source and exact item kind", 0)
    end
    require_guard()
    local destination, tarot = selection(instance, state.map)
    local profile = profiles[destination]
    if not profile or destination == state.map
        or (destination == nothing and type(SilverfishSetNativeNothingScope) ~= "function") then
        error("The stock Stardust continuation is not qualified for party travel", 0)
    end
    return { source = state.map, destination = destination, tarot = tarot, kind = kind,
        arrival = routes[destination].player_start, visit_seconds = profile.fixed_seconds or item.seconds * profile.multiplier,
        startup_seconds = profile.startup, fade_seconds = profile.fade, return_plan = bed.source(state, phase) }
end

local function reputation(instance)
    local out = {}
    instance:Find_PlayerReputation(3, out, out, out)
    if not signed_int(out.Points) then error("The host's dream reputation is unavailable", 0) end
    return out.Points
end

function dream.apply_effects(state, phase, kind, destination, tarot)
    local host, instance = host_context(state, phase, state.map, true)
    local route = dream.source(state, phase, kind)
    if route.destination ~= destination or route.tarot ~= tarot then
        error("The stock Tarot selection changed after the Stardust use was committed", 0)
    end
    local code = StaticFindObject(stock_code_library)
    if not valid(code) or not valid(StaticFindObject(stock_global_ping)) then
        error("The stock Stardust teleport event is unavailable", 0)
    end
    local before = reputation(instance)
    if tarot ~= "" then
        instance:Add_PointTo_SaveFlag(FName(tarot), -1, true, true)
        local found, value = read_flag(instance, tarot)
        if not found or value ~= -1 then error("The stock Tarot reset did not survive readback", 0) end
    end
    instance:AddPlayerRep(3, stock_items[kind].reputation)
    local after = reputation(instance)
    if after <= before then error("The stock Stardust reputation award did not survive readback", 0) end
    -- This is the same live player and canonical world context used by stock TeleportToSquad.
    code:GlobalPing("Player_Teleport", host.pawn, instance)
    local current_host, current_instance = host_context(state, phase, state.map, true)
    if not same(current_host.pawn, host.pawn) or not same(current_instance, instance) or reputation(instance) ~= after then
        error("The stock Stardust teleport event changed its authoritative scope", 0)
    end
    return { source = state.map, kind = kind, destination = destination, tarot = tarot,
        before = before, after = after, event = "Player_Teleport" }
end

function dream.prepare_duration(context, kind)
    local item = stock_items[kind]
    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(context)
    if not enabled or not item or not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ") then
        error("The canonical stock dream duration could not be prepared", 0)
    end
    instance.DreamVisit_Time = item.seconds
    if instance.DreamVisit_Time ~= item.seconds then error("The stock dream duration did not survive readback", 0) end
end

function dream.tarot_fixture(state, phase, flag, points)
    if not fixtures then error("The stock Tarot fixture requires a staged test host", 0) end
    local _, instance = host_context(state, phase, state.map, true)
    local qualified = false
    for _, choice in ipairs(tarot_routes) do
        if flag == choice.flag then qualified = true end
    end
    if not qualified or not signed_int(points) then error("Invalid stock Tarot fixture", 0) end
    instance:Add_PointTo_SaveFlag(FName(flag), points, true, true)
    local found, value = read_flag(instance, flag)
    if not found or value ~= points then error("The stock Tarot fixture did not survive readback", 0) end
    return "stock Tarot fixture verified"
end

function dream.arrival(state, phase)
    local map = state.map
    local host = host_context(state, phase, map, false)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" then error("The stock dream countdown guard is not ready", 0) end

    local name = routes[map].player_start
    local marker = placed_actor(state.world, map, "PlayerStart", name, false)
    local target, rotation = marker:K2_GetActorLocation(), marker:K2_GetActorRotation()
    if not finite(target.X) or not finite(target.Y) or not finite(target.Z)
        or not finite(rotation.Pitch) or not finite(rotation.Yaw) or not finite(rotation.Roll) then
        error("The stock dream PlayerStart transform is invalid", 0)
    end
    local pawn, controller = host.pawn, host.controller_object
    local aim = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    if not pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z }, aim) then
        error("The stock dream PlayerStart has no safe host placement", 0)
    end
    controller:SetControlRotation(aim)
    pawn:ForceNetUpdate()
    local location = pawn:K2_GetActorLocation()
    if not same(pawn:GetWorld(), state.world) or not same(controller.Pawn, pawn)
        or not finite(location.X) or not finite(location.Y) or not finite(location.Z) then
        error("The stock dream arrival position could not be read back", 0)
    end
    return { destination = map, actor = name, x = location.X, y = location.Y, z = location.Z }
end

function dream.position_terminal_fixture(state, phase, name, distance)
    if not fixtures or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or state.map ~= nothing
        or (distance ~= "near" and distance ~= "far") then
        error("The terminal distance fixture requires an isolated Nothing listen host", 0)
    end
    host_context(state, phase, nothing, false)
    local marker = placed_actor(state.world, nothing, "PlayerStart", "PlayerStart_1", false)
    local origin = marker:K2_GetActorLocation()
    if math.abs(origin.X) > 1 or math.abs(origin.Y) > 1 or math.abs(origin.Z - 200) > 1 then
        error("The terminal stock start does not match the qualified plane", 0)
    end
    local guest
    for _, details in ipairs(state.guests) do
        local pawn, controller = details.pawn, details.controller_object
        if details.name == name and valid(pawn) and valid(controller) and pawn:HasAuthority()
            and not controller:IsLocalController() and same(pawn:GetWorld(), state.world)
            and same(controller.Pawn, pawn) then
            if guest then error("The terminal fixture guest is ambiguous", 0) end
            guest = pawn
        end
    end
    if not guest then error("Choose the exact enrolled terminal fixture guest", 0) end
    local x = distance == "near" and 110 or 2100
    if not guest:K2_TeleportTo({ X = origin.X + x, Y = origin.Y, Z = origin.Z }, guest:K2_GetActorRotation()) then
        error("The terminal fixture placement could not be verified", 0)
    end
    guest:ForceNetUpdate()
    return "isolated terminal distance fixture positioned"
end

function dream.place_descending_guest(state, phase, name)
    if state.map ~= slate then error("Descent placement requires the stock Slate map", 0) end
    local host = host_context(state, phase, slate, false)
    if not current or current.status ~= "ready" or not same(current.world, state.world) then
        error("The stock Slate descent scope is unavailable", 0)
    end
    local guest
    for _, candidate in ipairs(state.guests) do
        if candidate.name == name and same(candidate.pawn:GetWorld(), state.world)
            and candidate.pawn:HasAuthority() and same(candidate.controller_object.Pawn, candidate.pawn)
            and not candidate.controller_object:IsLocalController() then
            if guest then error("The Slate guest identity is ambiguous", 0) end
            guest = candidate
        end
    end
    if not guest then error("The enrolled Slate guest is unavailable", 0) end
    local lead, follow = host.pawn.CharacterMovement, guest.pawn.CharacterMovement
    local point, aim = host.pawn:K2_GetActorLocation(), host.pawn:K2_GetActorRotation()
    if not valid(lead) or not valid(follow) then error("The Slate movement components are unavailable", 0) end
    local velocity = lead.Velocity
    if not velocity or not finite(velocity.X) or not finite(velocity.Y) or not finite(velocity.Z) then
        error("The host's Slate descent velocity is unavailable", 0)
    end
    for _, offset in ipairs({ { 110, 0 }, { -110, 0 }, { 0, 110 }, { 0, -110 } }) do
        if guest.pawn:K2_TeleportTo({ X = point.X + offset[1], Y = point.Y + offset[2], Z = point.Z }, aim) then
            if lead.MovementMode == 3 then
                follow:SetMovementMode(3, 0)
                follow.Velocity = { X = velocity.X, Y = velocity.Y, Z = velocity.Z }
                if follow.MovementMode ~= 3 or math.abs(follow.Velocity.Z - velocity.Z) > 1 then
                    error("The enrolled Slate guest did not adopt the party descent velocity", 0)
                end
            end
            guest.controller_object:SetControlRotation(aim)
            guest.pawn:ForceNetUpdate()
            if not current.slate_guests[name] then current.slate_enrolled = current.slate_enrolled + 1 end
            current.slate_guests[name] = { address = guest.pawn:GetAddress(), last_z = point.Z }
            return "stock Slate descent placement verified"
        end
    end
    error("The enrolled Slate guest has no safe descent placement", 0)
end

function dream.power_fixture(state, phase)
    if not fixtures or not profiles[state.map] or not profiles[state.map].early_power then
        error("This dream has no qualified stock power return fixture", 0)
    end
    local host = host_context(state, phase, state.map, true)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" or current.early_requested then
        error("The stock dream power test requires an armed visit without a pending return", 0)
    end
    current.actor:Send_PowerSignal(true, host.pawn)
    dream.sync(state, phase)
    if not current or current.status ~= "ready" or not current.early_requested then
        error("The native guard did not collect the staged stock power signal", 0)
    end
    return "stock dream countdown power signal routed to the co-op host"
end

return dream
