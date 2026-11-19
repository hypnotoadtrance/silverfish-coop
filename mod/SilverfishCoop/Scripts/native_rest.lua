local rest = {}
local native_bed = require("native_bed")

local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local enabled = bridge and bridge ~= "" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local headless_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local observatory = native_bed.profile(map)
local actor_name = observatory.actor_full_name
local max_hours = 10
local statics_path = "/Script/Engine.Default__GameplayStatics"
local math_path = "/Script/Engine.Default__KismetMathLibrary"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local bed_path = "/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C"
local day_class = "DayCycle_SunControler_BP_C"
local day_path = "/Game/Classes/Actors/DayCycle_SunControler_BP." .. day_class
local antfarm_class = "AntfarmController_C"
local antfarm_path = "/Game/Classes/AntFarm/AntfarmController." .. antfarm_class
local code_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"

rest.map, rest.actor = map, actor_name
local stock_scope
local stock_guard
local request_sequence = 0

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), class) and not object:GetFullName():find("Default__", 1, true)
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function int32(number)
    return finite(number) and math.tointeger(number) and number >= -2147483648 and number <= 2147483647
end

local function text(value)
    if type(value) == "userdata" then value = value:ToString() end
    if type(value) ~= "string" or #value > 256 or value:find("[%c]") then
        error("native_rest_text_unavailable", 0)
    end
    return value
end

local function hours_selected(value)
    if not int32(value) or value < 0 or value > max_hours then error("native_rest_hours_out_of_range", 0) end
    return math.tointeger(value)
end

local function world_actor(class, path, world, full_name, allow_proxy)
    local stock_class = StaticFindObject(path)
    local found
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if exact(actor, stock_class) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
            and (not full_name or actor:GetFullName() == full_name) then
            if found then return nil, "ambiguous" end
            found = actor
        end
    end
    if not found then return nil, "missing" end
    if not allow_proxy and not found:HasAuthority() then return nil, "authority_unavailable" end
    return found
end

local function host_context(state, phase, pawn_name, profile)
    local host = state.local_pawn
    local world, controller = state.world, state.controller
    if not enabled or phase ~= "hosting" or state.map ~= profile.map or not state.listening or state.server ~= false then
        error("native_rest_host_unavailable", 0)
    end
    if not valid(world) or world:GetFullName() ~= "World " .. profile.map .. "." .. profile.level then
        error("native_rest_host_world_unqualified", 0)
    end
    if not host or not same(host.controller_object, controller)
        or not exact(controller, StaticFindObject(controller_path)) or not controller:HasAuthority()
        or not controller:IsLocalController() or controller:IsActorBeingDestroyed()
        or not same(controller:GetWorld(), world) then
        error("native_rest_host_controller_unqualified", 0)
    end
    if controller.Loading ~= false then error("native_rest_host_still_loading", 0) end
    local pawn, driver = host.pawn, world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection)
        or not exact(pawn, StaticFindObject(pawn_path)) or pawn:GetFullName() ~= pawn_name
        or not pawn:HasAuthority() or pawn:IsActorBeingDestroyed() or pawn.Dead ~= false
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), world) then
        error("native_rest_host_pawn_unavailable", 0)
    end
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, StaticFindObject(instance_path)) or not exact(save, StaticFindObject(save_path))
        or not same(pawn.Save, save) or not same(statics:GetGameInstance(controller), instance) then
        error("native_rest_canonical_save_unavailable", 0)
    end
    return {world = world, controller = controller, pawn = pawn, driver = driver,
        statics = statics, instance = instance, save = save}
end

local function close_to_bed(pawn, box)
    local first, second = pawn:K2_GetActorLocation(), box:K2_GetComponentLocation()
    local range = pawn.InteractionRange
    if not first or not second or not finite(first.X) or not finite(first.Y) or not finite(first.Z)
        or not finite(second.X) or not finite(second.Y) or not finite(second.Z) or not finite(range) or range <= 0 then
        error("native_rest_stock_interaction_range_unavailable", 0)
    end
    local x, y, z = first.X - second.X, first.Y - second.Y, first.Z - second.Z
    return math.sqrt(x * x + y * y + z * z), range, second
end

local function inspect_bed(state, phase, pawn_name, requested_actor)
    local profile = native_bed.profile(state.map)
    if not profile or requested_actor ~= profile.actor_full_name then error("native_rest_actor_unqualified", 0) end
    local context = host_context(state, phase, pawn_name, profile)
    local actor = world_actor("SavePoint_BP_C", bed_path, context.world, profile.actor_full_name)
    if not actor or actor:GetFullName() ~= profile.actor_full_name or not same(actor["As Game Instance FP"], context.instance)
        or not same(context.statics:GetGameInstance(actor), context.instance)
        or actor.Faction ~= profile.faction or actor.MinSafeRep ~= profile.min_safe_rep
        or not valid(actor.Box) or not same(actor.Box:GetOwner(), actor) then
        error("native_rest_stock_bed_changed", 0)
    end
    local library = StaticFindObject(code_library)
    if not valid(library) then error("native_rest_stock_bed_id_unavailable", 0) end
    local id = {}
    library:Calc_UniqueID(actor, actor, id)
    if text(id.ID) ~= profile.bed_id then error("native_rest_stock_bed_id_changed", 0) end

    local distance, range, anchor = close_to_bed(context.pawn, actor.Box)
    local reputation = { Points = 0 }
    if profile.faction ~= 0 then
        context.instance:Find_PlayerReputation(actor.Faction, reputation, reputation, reputation)
        if not int32(reputation.Points) then error("native_rest_reputation_unavailable", 0) end
    end

    local insomnia_name = FName("Insomnia", EFindName.FNAME_Find)
    if insomnia_name:GetComparisonIndex() == 0 or insomnia_name:ToString() ~= "Insomnia" then
        error("native_rest_insomnia_flag_unavailable", 0)
    end
    local passive = {}
    context.instance:FindPassiveEffect(insomnia_name, false, passive, passive)
    if type(passive.EffectActive) ~= "boolean" then error("native_rest_insomnia_unavailable", 0) end

    -- Neutral beds use a random sleep duration when the stock Insomnia effect is active.
    local denied = distance >= range and "out_of_range"
        or profile.faction ~= 0 and reputation.Points < actor.MinSafeRep and "reputation_too_low"
        or profile.faction == 0 and passive.EffectActive and "insomnia_requires_stock_sleep_selection"
        or nil
    local report = {map = profile.map, pawn = pawn_name, actor = profile.actor_full_name,
        bed = profile.bed_id, level = profile.level,
        eligible = denied == nil, reason = denied, points = reputation.Points, distance = distance, range = range,
        insomnia = passive.EffectActive, x = anchor.X, y = anchor.Y, z = anchor.Z}
    context.actor = actor
    return report, context
end

function rest.inspect(state, phase, pawn_name, requested_actor)
    local report = inspect_bed(state, phase, pawn_name, requested_actor)
    return report
end

function rest.interaction(state, phase, actor, interactor)
    local host = state.local_pawn
    local profile = native_bed.profile(state.map)
    if not profile or not host or not valid(interactor) or not same(interactor, host.pawn)
        or not valid(actor) or actor:GetFullName() ~= profile.actor_full_name then return end
    local report, context = inspect_bed(state, phase, interactor:GetFullName(), profile.actor_full_name)
    if report.eligible and same(actor, context.actor) then return report end
end

-- For a qualified pre-dispatch hook on SavePoint_BP_C:GoToSleep. The
-- stock interaction caches the interactor before the SleepScreen opens.
function rest.selection(state, phase, actor, hours)
    if not valid(actor) or not valid(actor.Player) then return end
    local report = rest.interaction(state, phase, actor, actor.Player)
    if report then
        report.hours = hours_selected(hours)
        return report
    end
end

local function time_seconds(clock, save)
    local seconds = clock:GetTotalSeconds(save.GameTime)
    if not finite(seconds) then error("native_rest_stock_time_unavailable", 0) end
    return seconds
end

local function host_unchanged(context)
    return same(context.world.NetDriver, context.driver) and same(context.controller.Pawn, context.pawn)
        and same(context.pawn.Controller, context.controller) and same(context.pawn:GetWorld(), context.world)
        and same(context.instance.SaveSlot_Ref, context.save) and same(context.pawn.Save, context.save)
end

local function sleep_receiver(context)
    local actor, reason = world_actor(antfarm_class, antfarm_path, context.world)
    if not actor then return nil, reason end
    if not same(actor.GameInstance, context.instance)
        or not same(context.statics:GetGameInstance(actor), context.instance) then return nil, "uninitialized" end
    return actor
end

function rest.apply(state, phase, pawn_name, requested_actor, hours)
    hours = hours_selected(hours)
    local report, context = inspect_bed(state, phase, pawn_name, requested_actor)
    if not report.eligible then error("native_rest_" .. report.reason, 0) end
    local day = world_actor(day_class, day_path, context.world)
    local day_name = day_class .. " " .. report.map .. "." .. report.level .. ":PersistentLevel." .. day_class .. "_0"
    local clock = StaticFindObject(math_path)
    if not day or day:GetFullName() ~= day_name or day.Ready ~= true or not finite(day.StaticTimeStep)
        or not same(day["Game Instance"], context.instance) or not valid(clock) then
        error("native_rest_stock_day_cycle_unavailable", 0)
    end

    local receiver, ping_reason = sleep_receiver(context)
    local save = context.save
    local level, bed_id = report.level, report.bed
    local previous_level, previous_bed, previous_entrance = text(save.RespawnLevel), text(save.Bed), text(save.Entrance)
    local before, previous_step = time_seconds(clock, save), day.StaticTimeStep
    local complete, failure = pcall(function()
        save.RespawnLevel, save.Bed, save.Entrance = level, bed_id, bed_id
        if text(save.RespawnLevel) ~= level or text(save.Bed) ~= bed_id or text(save.Entrance) ~= bed_id
            or not host_unchanged(context) then error("native_rest_checkpoint_readback_failed", 0) end

        -- SavePoint's cooked power path consumes hours*60 through the stock
        -- DayCycle update path (0.25x input, then 240x game seconds).
        day.StaticTimeStep = hours * 60
        if day.StaticTimeStep ~= hours * 60 then error("native_rest_stock_time_step_readback_failed", 0) end
        day:Send_PowerSignal(true, context.actor)
        local after = time_seconds(clock, save)
        if math.abs(after - before - hours * 3600) > 0.25 or not host_unchanged(context) then
            error("native_rest_stock_time_readback_failed", 0)
        end

        -- Keep the qualified stock Antfarm receiver's Player_Sleep response.
        -- Broadcasting would invoke unrelated, data-driven world listeners.
        if receiver then
            receiver:Send_GlobalPing_ToActor("Player_Sleep", context.actor)
            ping_reason = "antfarm"
        end
        if not host_unchanged(context) or text(save.RespawnLevel) ~= level or text(save.Bed) ~= bed_id
            or text(save.Entrance) ~= bed_id or math.abs(time_seconds(clock, save) - after) > 0.25 then
            error("native_rest_final_readback_failed", 0)
        end
        report.hours, report.before, report.after, report.ping = hours, before, after, ping_reason
    end)
    day.StaticTimeStep = previous_step
    if not complete then
        if host_unchanged(context) then save.RespawnLevel, save.Bed, save.Entrance = previous_level, previous_bed, previous_entrance end
        -- The caller must recover its pre-rest generation if the stock time
        -- or Antfarm call ran before failure. Their world effects are not reversible here.
        error(failure, 0)
    end
    return report
end

function rest.clear_hook()
    if not stock_scope and not stock_guard then return end
    stock_scope = nil
    stock_guard = nil
    if type(SilverfishClearNativeStockRestScope) == "function" then SilverfishClearNativeStockRestScope() end
end

local function coordinator_recent()
    local heartbeat = io.open(bridge .. "/native-zone-heartbeat", "rb")
    if not heartbeat then return false end
    local text = heartbeat:read(32)
    heartbeat:close()
    local timestamp = text and text:match("^(%d+)%s*$")
    if not timestamp or #timestamp > 12 then return false end
    local age = os.time() - tonumber(timestamp)
    return age >= 0 and age <= 2
end

function rest.sync_hook(state, phase)
    if not enabled then return end
    local profile = native_bed.profile(state.map)
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not profile or not (host or client) or not state.listening or not valid(pawn) then
        rest.clear_hook()
        return
    end

    if type(SilverfishSetNativeStockRestGuard) == "function" then
        stock_guard = SilverfishSetNativeStockRestGuard(pawn:GetAddress(), host) or stock_guard
    end
    if not host or type(SilverfishSetNativeStockRestScope) ~= "function"
        or type(SilverfishTakeNativeStockRestRequest) ~= "function" then return end
    -- An already scoped, stale UI call is vetoed by the native hook.
    if not coordinator_recent() then return end
    local bed = stock_scope and stock_scope.bed
    if not valid(bed) or not same(state.world, bed:GetWorld()) or bed:GetFullName() ~= profile.actor_full_name then
        bed = world_actor("SavePoint_BP_C", bed_path, state.world, profile.actor_full_name)
    end
    if not valid(bed) or not SilverfishSetNativeStockRestScope(pawn:GetAddress(), bed:GetAddress()) then return end
    stock_scope = { bed = bed }

    local took, owner, receiver, hours = SilverfishTakeNativeStockRestRequest()
    if not took or owner ~= pawn:GetAddress() or receiver ~= bed:GetAddress() then return end
    local checked, selection = pcall(rest.selection, state, phase, bed, hours)
    if not checked or not selection then return end
    local file = io.open(bridge .. "/native-rest-events", "ab")
    if not file then return end
    request_sequence = request_sequence + 1
    file:write(table.concat({ "rest", request_sequence, selection.map, selection.pawn,
        selection.actor, selection.hours }, "\t"), "\n")
    file:close()
end

function rest.hook_rows(status_row)
    local rows = {}
    if type(SilverfishReadNativeStockRestStatus) == "function" then
        local installed, armed, fresh, state = SilverfishReadNativeStockRestStatus()
        rows[#rows + 1] = status_row("native-rest-hook", installed and 1 or 0, armed and 1 or 0, fresh and 1 or 0, state)
    end
    if type(SilverfishReadNativeStockRestGuardStatus) == "function" then
        local armed, decision, guest_denials = SilverfishReadNativeStockRestGuardStatus()
        rows[#rows + 1] = status_row("native-rest-guard", armed and 1 or 0, decision, guest_denials)
    end
    return rows
end

function rest.ui_fixture(state, phase, pawn_name, hours)
    local guard = SilverfishSetNativeStockRestTestMenuGuard
    if not headless_fixture or type(guard) ~= "function" or not stock_scope then
        error("Native rest UI fixture requires the headless stock guard", 0)
    end
    local profile = native_bed.profile(state.map)
    if not profile then error("The current map has no stock sleep UI fixture", 0) end
    local report, context = inspect_bed(state, phase, pawn_name, profile.actor_full_name)
    hours = hours_selected(hours)
    local stack = context.controller.MenuStack
    if not report.eligible or not stack or stack:GetArrayNum() ~= 0 or not guard(true) then
        error("The exact stock sleep menu cannot be opened in the current fixture state", 0)
    end
    local opened, problem = pcall(function()
        context.actor:SendInteractionToActor(context.pawn, context.actor.Box)
        local top = stack:GetArrayNum() == 1 and stack[1]
        if not exact(top, StaticFindObject("/Game/Classes/GUI/New/SleepScreen.SleepScreen_C"))
            or not same(top.Bed, context.actor) or not same(context.actor.Player, context.pawn)
            or not same(top["As Game Instance FP"], context.instance) or top.Max ~= max_hours then
            error("The stock save point did not open its exact SleepScreen", 0)
        end
        top:SetCount(hours)
        if top.Count ~= hours then error("The stock sleep-hours selection was not verified", 0) end
        top:ConfirmSleep()
        local installed, armed, _, status = SilverfishReadNativeStockRestStatus()
        if stack:GetArrayNum() ~= 0 or not installed or not armed or status ~= "selected" then
            error("The stock sleep confirmation did not reach the isolated native handoff (" .. tostring(status) .. ")", 0)
        end
    end)
    guard(false)
    if not opened then error(problem, 0) end
    return "verified stock sleep screen selection"
end

function rest.guest_fixture(state, phase, pawn_name)
    local read_guard = SilverfishReadNativeStockRestGuardStatus
    local menu_guard = SilverfishSetNativeStockRestTestMenuGuard
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local profile = native_bed.profile(state.map)
    if not headless or (host and not headless_fixture) or not (host or client)
        or not profile or not valid(state.world)
        or type(read_guard) ~= "function" or not state.local_pawn or not valid(state.controller) then
        error("The stock guest rest fixture requires a qualified headless co-op world", 0)
    end
    local armed, _, denials = read_guard()
    if not armed then error("The native stock rest guest guard is unavailable", 0) end
    local target
    if host then
        for _, guest in ipairs(state.guests) do
            if guest.name == pawn_name then
                if target then error("The authority guest rest fixture target is ambiguous", 0) end
                target = guest.pawn
            end
        end
    elseif state.local_pawn.name == pawn_name then
        target = state.local_pawn.pawn
    end
    local actor = world_actor("SavePoint_BP_C", bed_path, state.world, profile.actor_full_name, client)
    if not exact(target, StaticFindObject(pawn_path)) or not same(target:GetWorld(), state.world)
        or target:GetFullName() ~= pawn_name or not actor
        or not valid(actor.Box) or not same(actor.Box:GetOwner(), actor) then
        error("The exact stock guest rest interaction is unavailable", 0)
    end
    if actor.Faction ~= profile.faction then
        error("The stock guest rest denial fixture requires the exact authored bed faction", 0)
    end
    -- The host can verify the neutral Insomnia branch is inactive before its
    -- headless menu guard is used. Client fixtures only probe faction beds.
    if actor.Faction == 0 then
        if not host or inspect_bed(state, phase, state.local_pawn.name, profile.actor_full_name).insomnia ~= false then
            error("The neutral bed probe requires a headless host without stock Insomnia", 0)
        end
    end
    local stack = state.controller.MenuStack
    if not stack or (host and (type(menu_guard) ~= "function" or not menu_guard(true))) then
        error("The staged guest rest UI safety guard is unavailable", 0)
    end
    local function player_address()
        return valid(actor.Player) and actor.Player:GetAddress() or 0
    end
    local accepted, failure = pcall(function()
        local depth, owner = stack:GetArrayNum(), player_address()
        actor:SendInteractionToActor(target, actor.Box)
        local still_armed, decision, after = read_guard()
        if not still_armed or decision ~= "guest_interaction_denied" or after ~= denials + 1
            or depth ~= stack:GetArrayNum() or owner ~= player_address() then
            error("The stock guest rest interaction reached native UI or lacked a denial receipt", 0)
        end
    end)
    if host then menu_guard(false) end
    if not accepted then error(failure, 0) end
    return "verified stock guest rest interaction denial"
end

return rest
