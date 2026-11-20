local input = {}
local supply_profiles = require("authored_supply_catalog")
local cash_profiles = require("authored_cash_catalog")
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local docks = "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"
local cistern = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local drill_head = "scientist-tunnel-drill-head"
local lab_blank_tape = "lab-e206-blank-data-tape"
local cistern_keycard = "cistern-old-nfta-keycard"
local cistern_locker = "cistern-old-nfta-locker"
local crossroads_lockpick = "crossroads-lockpick"
local sources = {
    ["city-copper-wire"] = city,
    ["city-paladin-brandy"] = city,
    ["city-canned-peaches"] = city,
    ["scientist-blank-data-tape"] = scientist,
    [lab_blank_tape] = "/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P",
    ["crossroads-pocket-watch"] = crossroads,
    ["crossroads-dead-drop-bag"] = crossroads,
    [crossroads_lockpick] = crossroads,
    ["savlo-estate-evil-eye-ring"] = "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P",
    ["borough-mre"] = "/Game/Classes/Maps/Maps_SafeZones/Borough",
    ["railyard-dust-filter"] = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P",
    ["northeast-rail-molasses"] = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P",
    ["trench-savlo-keycard"] = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P",
    [cistern_keycard] = cistern,
    [cistern_locker] = cistern,
    ["trench-savlo-locker"] = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P",
    ["fort-leto-locker-key-543"] = "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P",
    ["forest-brokers-artifacts-bag"] = "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P",
    ["gorge-insurrectionist-recorder"] = "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P",
    [drill_head] = "/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P",
}
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local drill_chamber_stage = headless and host_fixtures and os.getenv("SILVERFISH_COOP_DRILL_HEAD_CHAMBER_FIXTURE") == "1"
local dust_filter = "railyard-dust-filter"
local molasses = "northeast-rail-molasses"
local savlo_keycard = "trench-savlo-keycard"
local savlo_locker = "trench-savlo-locker"
local fort_leto_key = "fort-leto-locker-key-543"
local stolen_bag = "forest-brokers-artifacts-bag"
local dead_drop = "crossroads-dead-drop-bag"
local black_box = "gorge-insurrectionist-recorder"
local city_shelf_supply = city .. ".City_01_P:PersistentLevel.Ammo_Magnum_BP_C_1"
local precise_distances = {
    [dust_filter] = {125, 160, 195},
    [molasses] = {45, 60, 75, 90, 110, 140, 170, 200},
    [savlo_keycard] = {55, 80, 105, 125, 160, 195},
    [cistern_keycard] = {55, 80, 105, 125, 160, 195},
    [cistern_locker] = {95, 125, 150, 185},
    [crossroads_lockpick] = {70, 95, 120, 155, 190},
    [savlo_locker] = {95, 125, 150, 185},
    [fort_leto_key] = {120, 155, 185, 220},
    [stolen_bag] = {85, 115, 145, 175, 210},
    [dead_drop] = {85, 115, 145, 175, 210},
    [black_box] = {65, 85, 110, 140, 175, 205},
    [drill_head] = {85, 115, 145, 175, 210},
    [lab_blank_tape] = {80, 110, 140, 175, 205},
}
local supplies, shelf_source = {}, nil
for _, profile in ipairs(supply_profiles) do
    supplies[profile.source] = profile
    sources[profile.source] = profile.map
    precise_distances[profile.source] = {95, 125, 155, 190}
    if profile.path == city_shelf_supply then
        shelf_source = profile.source
        precise_distances[profile.source] = {48, 62, 78, 95, 115, 150, 195}
    end
end
local cash_sources = {}
for _, profile in ipairs(cash_profiles) do
    cash_sources[profile.source] = profile
    sources[profile.source] = profile.map
    precise_distances[profile.source] = {95, 125, 155, 190}
end
local approach_front = {[dust_filter] = 0, [molasses] = 180, [stolen_bag] = -90, [cistern_locker] = -90}
-- The exact City stack sits in the low wall shelf at yaw -105 degrees.
if shelf_source then approach_front[shelf_source] = 75 end
local cabinet_labels = {[dust_filter] = "authored_dust_cabinet", [molasses] = "authored_molasses_cabinet",
    [savlo_locker] = "authored_savlo_locker", [cistern_locker] = "stock_cistern_nfta_locker"}
local trace_color = {R = 0, G = 0, B = 0, A = 0}

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function vector(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then return value end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
    end
end

local function owned(component, owner, class)
    return valid(component) and same(component:GetOwner(), owner)
        and same(component:GetClass(), StaticFindObject("/Script/Engine." .. class))
end

local function aim(eye, point)
    local x, y, z = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
    local horizontal = math.sqrt(x ^ 2 + y ^ 2)
    if horizontal < 1 then return end
    return { Pitch = math.deg(math.atan(z, horizontal)), Yaw = math.deg(math.atan(y, x)), Roll = 0 }
end

local function points(box, center, tall_aim)
    local result = { center }
    local extent = vector(box:GetScaledBoxExtent())
    if not extent then return result end
    for _, axis in ipairs({ { box:GetUpVector(), extent.Z }, { box:GetRightVector(), extent.Y } }) do
        local direction, radius = vector(axis[1]), axis[2]
        if direction and radius > 0 and radius <= 100 then
            for _, sign in ipairs({ 1, -1 }) do
                local offset = 0.35 * radius * sign
                result[#result + 1] = { X = center.X + direction.X * offset,
                    Y = center.Y + direction.Y * offset, Z = center.Z + direction.Z * offset }
            end
        end
    end
    if tall_aim and extent.Z > 0 and extent.Z <= 100 then
        local up = vector(box:GetUpVector())
        if up then
            local offset = 0.75 * extent.Z
            result[#result + 1] = {X = center.X + up.X * offset, Y = center.Y + up.Y * offset, Z = center.Z + up.Z * offset}
        end
    end
    return result
end

local function square(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function cast(trace, pawn, from, to, channel, complex, ignored)
    local hit = {}
    if trace:LineTraceSingle(pawn, from, to, channel, complex, ignored, 0, hit, true, trace_color, trace_color, 0) ~= true then return end
    return hit.OutHit or hit
end

local function component_name(component)
    return valid(component) and component:GetFullName() or "-"
end

local function stock_sight(trace, pawn, box, eye, point, limit, ignored)
    local distance = math.sqrt(square(eye, point))
    if distance < 1 or distance >= limit then return false, "range" end
    local function endpoint(length)
        local scale = length / distance
        return {X = eye.X + (point.X - eye.X) * scale, Y = eye.Y + (point.Y - eye.Y) * scale,
            Z = eye.Z + (point.Z - eye.Z) * scale}
    end
    -- LookTrace starts with complex Visibility and trims the ordinary Interact
    -- trace to that first contact when it is within InteractionRange.
    local visibility = cast(trace, pawn, eye, endpoint(limit), 1, true, ignored)
    local obstruction
    if visibility then
        local impact = vector(visibility.ImpactPoint)
        if not impact then return false, "visibility_geometry" end
        limit = math.min(limit, math.sqrt(square(eye, impact)))
        if limit < distance then
            obstruction = "visible:" .. component_name(visibility.Component and visibility.Component:Get())
        end
    end
    if limit < 1 then return false, obstruction or "visibility_range" end
    local hit = cast(trace, pawn, eye, endpoint(limit), 4, false, ignored)
    local component = hit and hit.Component and hit.Component:Get()
    if same(component, box) then return true end
    return false, valid(component) and "interact:" .. component_name(component) or obstruction or "interact:-"
end

function input.authority_sight(state, phase, profile, pawn, actor)
    local cash = cash_sources[profile.source] == profile
    local sight_profile = ((profile.source == stolen_bag or profile.source == dead_drop
        or profile.source == black_box or profile.source == lab_blank_tape or profile.source == cistern_keycard
        or profile.source == crossroads_lockpick)
        and profile.map == sources[profile.source] and profile.authority_sight == true)
        or (profile.source == drill_head and profile == require("native_prototype_drill_pickup").profile)
    if (not cash and not sight_profile and supplies[profile.source] ~= profile) or phase ~= "hosting" or not state.listening or state.server ~= false
        or state.map ~= profile.map or not same(pawn:GetWorld(), state.world) or not pawn:HasAuthority()
        or not valid(actor) or not same(actor:GetWorld(), state.world) or not actor:HasAuthority()
        or actor:GetFullName() ~= profile.name .. " " .. profile.path or pawn.Dead ~= false then return false end
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local camera, box = pawn.Camera, actor.Box
    if not valid(trace) or not owned(camera, pawn, "CameraComponent") or not owned(box, actor, "BoxComponent")
        or not same(box, actor.RootComponent) then return false end
    local origin, eye = vector(pawn:K2_GetActorLocation()), vector(camera:K2_GetComponentLocation())
    local center, limit = vector(box:K2_GetComponentLocation()), pawn.InteractionRange
    if not origin or not eye or not center or not finite(limit) or limit <= 0 or limit > 1000
        or square(eye, origin) > 150^2 or square(origin, center) > 250^2 then return false end
    local ignored, held = {pawn}, pawn.EquipedItem
    if valid(held) and same(held:GetOwner(), pawn) and same(held:GetWorld(), state.world) then
        ignored[#ignored + 1] = held
    end
    for _, point in ipairs(points(box, center, true)) do
        if stock_sight(trace, pawn, box, eye, point, limit, ignored) then return true end
    end
    return false
end

local function restore_position(pawn, origin, facing)
    local returned = pawn:K2_TeleportTo(origin, facing) and vector(pawn:K2_GetActorLocation())
    if not returned or square(returned, origin) > 18^2 then return false end
    pawn:ForceNetUpdate()
    return true
end

local function approach_clear_sight(state, pawn, actor, box, label, distances, front, multilevel, tall_aim, floor_depth, drill_catwalk)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local capsule, camera = pawn.CapsuleComponent, pawn.Camera
    if not valid(trace) or not owned(capsule, pawn, "CapsuleComponent") or not owned(camera, pawn, "CameraComponent")
        or not owned(box, actor, "BoxComponent") then
        error(label .. "_components_unavailable", 0)
    end
    local origin, initial_eye = vector(pawn:K2_GetActorLocation()), vector(camera:K2_GetComponentLocation())
    local facing, center = rotation(pawn:K2_GetActorRotation()), vector(box:K2_GetComponentLocation())
    local half_height, limit = capsule:GetScaledCapsuleHalfHeight(), pawn.InteractionRange
    if not origin or not initial_eye or not facing or not center or not finite(half_height) or half_height < 20 or half_height > 150
        or not finite(limit) or limit <= 0 or limit > 1000 or square(initial_eye, origin) > 150^2 then
        error(label .. "_geometry_unavailable", 0)
    end
    local floor_ignored, sight_ignored = {actor}, {pawn}
    for _, detail in ipairs(state.pawns) do
        if valid(detail.pawn) then floor_ignored[#floor_ignored + 1] = detail.pawn end
    end
    if valid(pawn.EquipedItem) then sight_ignored[#sight_ignored + 1] = pawn.EquipedItem end
    local deck, radius
    if drill_catwalk then
        if not drill_chamber_stage and square(origin, center) > 625^2 then
            error(label .. "_requires_local_catwalk", 0)
        end
        local placement = StaticFindObject(sources[drill_head] .. ".Sci_Tunnel_01_P:PersistentLevel.StaticMeshActor_1001")
        deck = valid(placement) and placement.RootComponent
        radius = capsule:GetScaledCapsuleRadius()
        if not valid(placement) or not same(placement:GetWorld(), state.world) or not owned(deck, placement, "StaticMeshComponent")
            or not finite(radius) or radius <= 0 or radius > half_height then
            error(label .. "_catwalk_geometry_unavailable", 0)
        end
    end
    local function deck_hit(hit, floor)
        return floor and math.abs(floor.Z + 3300) <= 18
            and same(hit.Component and hit.Component:Get(), deck)
    end
    if drill_catwalk and not drill_chamber_stage then
        local below = {X = origin.X, Y = origin.Y, Z = origin.Z - half_height - 18}
        local support = cast(trace, pawn, origin, below, 0, false, floor_ignored)
        local floor, normal = support and vector(support.ImpactPoint), support and vector(support.ImpactNormal)
        if not support or not deck_hit(support, floor) or not normal or normal.Z < 0.7
            or math.abs(origin.Z - floor.Z - half_height) > 18 then
            error(label .. "_requires_local_catwalk", 0)
        end
    end
    local function clear_step(point)
        if not drill_catwalk then return true end
        local above = {X = point.X, Y = point.Y, Z = point.Z + 2}
        local from = drill_chamber_stage and point or {X = origin.X, Y = origin.Y, Z = origin.Z + 4}
        return trace:CapsuleTraceSingle(pawn, from, above, radius, half_height, 0, false,
            sight_ignored, 0, {}, true, trace_color, trace_color, 0) == false
    end
    local rays, blockers = points(box, center, tall_aim ~= false), {}
    local angles = {0, 45, -45, 90, -90, 135, -135, 180}
    if multilevel then
        for _, angle in ipairs({22.5, -22.5, 67.5, -67.5, 112.5, -112.5, 157.5, -157.5}) do
            angles[#angles + 1] = angle
        end
    end
    local moved, candidates, floor_hits, deck_hits = false, 0, 0, 0
    local function clear_sight(eye)
        for _, target in ipairs(rays) do
            local clear, reason = stock_sight(trace, pawn, box, eye, target, limit, sight_ignored)
            if clear then return true end
            blockers[reason] = true
        end
        return false
    end
    local checked, placed = pcall(function()
        for _, distance in ipairs(distances) do
            for _, angle in ipairs(angles) do
                local heading = math.rad((front or 0) + angle)
                local x, y = center.X + distance * math.cos(heading), center.Y + distance * math.sin(heading)
                local top, bottom = center.Z + 160, center.Z - (floor_depth or 240)
                local tested_heights = {}
                for trace_kind = 1, multilevel and 2 or 1 do
                    local from_z = top
                    -- At the rail office a roof can sit directly above a walkable loft floor.
                    for _ = 1, multilevel and 5 or 1 do
                        local hit = cast(trace, pawn, {X = x, Y = y, Z = from_z}, {X = x, Y = y, Z = bottom},
                            0, trace_kind == 2, floor_ignored)
                        local floor, normal = hit and vector(hit.ImpactPoint), hit and vector(hit.ImpactNormal)
                        if not floor or floor.Z < bottom or floor.Z > from_z then break end
                        local height = math.floor(floor.Z + 0.5)
                        local qualified = not drill_catwalk or deck_hit(hit, floor)
                        if drill_catwalk then
                            floor_hits = floor_hits + 1
                            if qualified then deck_hits = deck_hits + 1 end
                        end
                        if qualified and normal and normal.Z >= 0.7 and floor.Z < from_z - 1 and not tested_heights[height] then
                            tested_heights[height] = true
                            local point = {X = x, Y = y, Z = floor.Z + half_height + (drill_catwalk and 4 or 2)}
                            local eye = {X = x + initial_eye.X - origin.X, Y = y + initial_eye.Y - origin.Y,
                                Z = point.Z + initial_eye.Z - origin.Z}
                            if square(point, center) <= 235^2 then
                                candidates = candidates + 1
                                local clear = clear_sight(eye)
                                if clear and not clear_step(point) then
                                    blockers["catwalk_capsule_blocked"] = true
                                    clear = false
                                end
                                if clear and pawn:K2_TeleportTo(point, facing) then
                                    moved = true
                                    local actual, current_eye = vector(pawn:K2_GetActorLocation()),
                                        vector(camera:K2_GetComponentLocation())
                                    if actual and current_eye and square(actual, point) <= 18^2
                                        and square(actual, center) <= 240^2 and clear_sight(current_eye) then
                                        pawn:ForceNetUpdate()
                                        return true
                                    end
                                end
                            end
                        end
                        from_z = floor.Z - 3
                        if from_z <= bottom then break end
                    end
                end
            end
        end
    end)
    if checked and placed then return origin, facing end
    if moved and not restore_position(pawn, origin, facing) then
        error("mutation_unverified_" .. label .. "_rollback", 0)
    end
    if not checked then error(placed, 0) end
    local names = {}
    for reason in pairs(blockers) do names[#names + 1] = reason end
    table.sort(names)
    local floor_proof = drill_catwalk and string.format(":hits=%d:catwalk_1001=%d", floor_hits, deck_hits) or ""
    error(string.format("%s_no_clear_sight:floor=%d%s:%s", label, candidates, floor_proof,
        table.concat(names, "|", 1, math.min(#names, 4))), 0)
end

local function approach_scope(state, phase, pawn, actor, map)
    return headless and host_fixtures and phase == "hosting" and state.listening and state.server == false
        and map == state.map and same(pawn:GetWorld(), state.world) and same(actor:GetWorld(), state.world)
        and pawn.Dead == false and pawn:HasAuthority()
end

function input.precise_approach(state, phase, profile, pawn, actor)
    local distances = precise_distances[profile.source]
    if not distances then return end
    local label = "authored_pickup_fixture_" .. profile.source
    local drill_catwalk = profile.source == drill_head and profile == require("native_prototype_drill_pickup").profile
    if (profile.stock_supply and supplies[profile.source] ~= profile)
        or (profile.stock_cash and cash_sources[profile.source] ~= profile)
        or (profile.source == drill_head and not drill_catwalk)
        or not approach_scope(state, phase, pawn, actor, sources[profile.source])
        or not same(actor.Box, actor.RootComponent) then
        error(label .. "_scope_unavailable", 0)
    end
    local forest_locker_top = profile.source == stolen_bag
    local nested_floor = profile.source == molasses or profile.source == shelf_source or forest_locker_top
        or profile.source == dead_drop or profile.source == black_box or profile.source == lab_blank_tape or drill_catwalk
        or profile.source == "city-large-stack-1" or profile.source == "crossroads-small-stack-1"
    -- The bag sits above the stock locker bank, about 248 units above the room floor.
    -- The Drill Head sits about 264 units above its catwalk, beside the drill assembly.
    local floor_depth = drill_catwalk and 340 or forest_locker_top and 275 or nil
    approach_clear_sight(state, pawn, actor, actor.Box, label, distances, approach_front[profile.source],
        nested_floor, nil, floor_depth, drill_catwalk)
    if drill_catwalk and drill_chamber_stage then return "headless Drill Head catwalk staged with stock sight" end
    return "stock pickup floor and interaction sight verified"
end

function input.approach_exact_container(state, phase, profile, pawn, actor, box)
    local docks_crate = profile.map == docks and profile.guarantee_spawn == true
    local lab_cabinet = profile.small_cabinet == true and profile.map == "/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P"
    local office_desk = profile.office_desk == true and profile.map == "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P"
    local label = office_desk and "native_loot_desk_fixture" or docks_crate and "native_loot_docks_fixture" or "native_loot_cabinet_fixture"
    if (not docks_crate and not lab_cabinet and not office_desk)
        or require("authored_loot_registry")[profile.index] ~= profile
        or not approach_scope(state, phase, pawn, actor, profile.map) or not actor:HasAuthority()
        or not same(StaticFindObject(profile.path), actor) or actor:GetFullName() ~= profile.name
        or not same(actor.Box, box) or not owned(box, actor, "BoxComponent") then
        error(label .. "_scope_unavailable", 0)
    end
    local forward = vector(box:GetForwardVector())
    local bearing = forward and math.deg(math.atan(forward.Y, forward.X)) or 0
    -- Match the exact center and edge rays used by the local crate F fixture.
    local distances = office_desk and {65, 85, 105, 135, 165, 205} or {95, 125, 155, 195, 225}
    approach_clear_sight(state, pawn, actor, box, label, distances, office_desk and bearing + 90 or bearing, true, false)
end

local function restored(camera, original)
    local current = rotation(camera.RelativeRotation)
    if not current then return false end
    for _, axis in ipairs({ "Pitch", "Yaw", "Roll" }) do
        if math.abs((current[axis] - original[axis] + 180) % 360 - 180) > 0.05 then return false end
    end
    return true
end

local function press_stock(state, pawn, box, precise_aim, label, dispatch)
    local camera = pawn.Camera
    local original = rotation(camera.RelativeRotation)
    local eye, center, position = vector(camera:K2_GetComponentLocation()), vector(box:K2_GetComponentLocation()),
        vector(pawn:K2_GetActorLocation())
    if not original or not eye or not center or not position or square(position, center) > 250^2 then
        error(label .. "_outside_verified_reach", 0)
    end

    local ok, reason = pcall(function()
        -- LookTrace reads the Camera transform and fills the ordinary stock interaction focus.
        for _, point in ipairs(points(box, center, precise_aim)) do
            local view = aim(eye, point)
            if view then
                camera:K2_SetWorldRotation(view, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, box) then
                    return dispatch()
                end
            end
        end
        local focused = pawn.InteractionTarget_Component
        local failure = label .. "_focus_unavailable:" .. component_name(focused)
        if precise_aim then
            failure = failure .. string.format(":eye=(%.1f,%.1f,%.1f):box=(%.1f,%.1f,%.1f)",
                eye.X, eye.Y, eye.Z, center.X, center.Y, center.Z)
        end
        error(failure, 0)
    end)
    local recovered, match = pcall(function()
        if not owned(camera, pawn, "CameraComponent") or not same(pawn.Camera, camera)
            or not same(pawn:GetWorld(), state.world) then return false end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        pawn:LookTrace()
        return restored(camera, original)
    end)
    if not recovered or not match then error("mutation_unverified_" .. label .. "_camera_restore", 0) end
    if not ok then error(reason, 0) end
    return reason
end

function input.open_cabinet(state, phase, pawn, actor, box, source, dispatch)
    local press = SilverfishProbeNativeInteractInput
    local label = cabinet_labels[source]
    if not label then error("authored_cabinet_unknown_fixture", 0) end
    if not approach_scope(state, phase, pawn, actor, sources[source])
        or not same(pawn, state.local_pawn.pawn)
        or not owned(pawn.Camera, pawn, "CameraComponent")
        or type(dispatch or press) ~= "function" then
        error(label .. "_fixture_scope_unavailable", 0)
    end
    local origin, facing = approach_clear_sight(state, pawn, actor, box, label .. "_fixture",
        precise_distances[source], approach_front[source], source == molasses)
    local ok, receipt = pcall(press_stock, state, pawn, box, true, label .. "_stock_input", dispatch or function()
        local delivered, why, sequence = press(pawn:GetAddress(), false)
        if delivered ~= true or why ~= "stock_started" or math.type(sequence) ~= "integer" or sequence < 1 then
            error("mutation_unverified_" .. label .. "_stock_input:" .. tostring(why), 0)
        end
        return sequence
    end)
    if not restore_position(pawn, origin, facing) then
        error("mutation_unverified_" .. label .. "_fixture_rollback", 0)
    end
    if not ok then error(receipt, 0) end
    return receipt
end

function input.cistern_nfta_locker(fields, state, phase)
    local world_path = cistern .. ".Cistern_P"
    local path = world_path .. ":PersistentLevel.LootLocker_Small_BP_C_6"
    local class = "/Game/Classes/Actors/Lootables/LootLocker_Small_BP.LootLocker_Small_BP_C"
    local pawn = state.local_pawn and state.local_pawn.pawn
    local actor = state.map == cistern and StaticFindObject(path)
    if #fields ~= 2 or not valid(pawn) or not valid(actor) or not approach_scope(state, phase, pawn, actor, cistern)
        or state.world:GetFullName() ~= "World " .. world_path or not pawn:IsLocallyControlled()
        or actor:GetFullName() ~= "LootLocker_Small_BP_C " .. path or not same(actor:GetClass(), StaticFindObject(class))
        or not same(actor:GetOuter(), state.world.PersistentLevel) or not actor:HasAuthority()
        or actor:IsActorBeingDestroyed() or actor.SpawnSalt ~= -1065462208 or type(actor.Locked) ~= "boolean" then
        error("stock_cistern_nfta_locker_fixture_scope_unavailable", 0)
    end
    local location, facing = vector(actor:K2_GetActorLocation()), rotation(actor:K2_GetActorRotation())
    local door = actor.SM_Prop_Cabinet_Small_Door_01
    if not location or square(location, {X = 5109.10794, Y = 11363.25031, Z = -2250}) > 1
        or not facing or math.abs(math.abs(facing.Yaw) - 180) > .2
        or not owned(door, actor, "StaticMeshComponent") then
        error("stock_cistern_nfta_locker_fixture_geometry_unavailable", 0)
    end
    local box_type = StaticFindObject("/Script/Engine.BoxComponent")
    if not valid(box_type) then error("stock_cistern_nfta_locker_fixture_box_class_unavailable", 0) end
    local function box_count()
        local boxes = actor:K2_GetComponentsByClass(box_type)
        local shape = type(boxes)
        if shape == "userdata" and type(boxes.type) == "function" then shape = boxes:type() end
        return (shape == "table" or shape == "TArray") and #boxes or nil
    end
    local function read_opening()
        local angle = rotation(door.RelativeRotation)
        local offset = vector(door.RelativeLocation)
        if actor.Locked or not angle or not offset then error("mutation_unverified_stock_cistern_nfta_locker_open", 0) end
        -- The stock interaction retires its Box and starts a 0.3s latent door move.
        if math.abs((angle.Yaw - 95 + 180) % 360 - 180) < .25
            and square(offset, {X = -28.5, Y = 35, Z = 115}) < 1 then
            return "stock_cistern_nfta_locker_opened"
        end
        return "stock_cistern_nfta_locker_opening"
    end
    local count = box_count()
    if count == 0 then return read_opening() end
    local box = actor.Box
    if count ~= 1 or not owned(box, actor, "BoxComponent") then error("stock_cistern_nfta_locker_fixture_box_unavailable", 0) end
    input.open_cabinet(state, phase, pawn, actor, box, cistern_locker)
    if actor.Locked then error("stock_cistern_nfta_locker_locked", 0) end
    if not same(actor.Player, pawn) or box_count() ~= 0 then error("mutation_unverified_stock_cistern_nfta_locker_open", 0) end
    return read_opening()
end

function input.approach_cabinet_guest(state, phase, pawn, actor, box, source)
    local label = cabinet_labels[source]
    if not label or not approach_scope(state, phase, pawn, actor, sources[source])
        or same(pawn, state.local_pawn.pawn) or not owned(box, actor, "BoxComponent")
        or not owned(pawn.Camera, pawn, "CameraComponent") then
        error("authored_cabinet_guest_approach_scope_unavailable", 0)
    end
    approach_clear_sight(state, pawn, actor, box, label .. "_guest_approach",
        precise_distances[source], approach_front[source], source == molasses)
end

function input.check_cabinet_guest_press(state, phase, pawn, actor, box, source)
    local label, press = cabinet_labels[source], SilverfishProbeNativeInteractInput
    if not headless or not label or phase ~= "connected" or state.server ~= true or not state.listening
        or sources[source] ~= state.map or not same(pawn, state.local_pawn.pawn)
        or not same(pawn:GetWorld(), state.world) or not same(actor:GetWorld(), state.world) or pawn.Dead ~= false
        or not owned(box, actor, "BoxComponent") or not owned(pawn.Camera, pawn, "CameraComponent")
        or type(press) ~= "function" then
        error("authored_cabinet_guest_stock_scope_unavailable", 0)
    end
    return press_stock(state, pawn, box, true, label .. "_guest_stock_input", function()
        local delivered, why, sequence = press(pawn:GetAddress(), true)
        if delivered ~= true or why ~= "stock_started_consumed" or sequence ~= 0 then
            error("mutation_unverified_" .. label .. "_guest_stock_input:" .. tostring(why), 0)
        end
        return why
    end)
end

function input.press(state, phase, profile)
    local host = phase == "hosting" and state.server == false
    local guest = phase == "connected" and state.server == true
    local inspect, press = SilverfishReadNativeAuthoredPickup, SilverfishProbeNativeInteractInput
    if not headless or (not guest and not (host and host_fixtures)) or not state.listening or profile.map ~= state.map
        or (profile.stock_supply and supplies[profile.source] ~= profile)
        or (profile.stock_cash and cash_sources[profile.source] ~= profile)
        or sources[profile.source] ~= state.map or type(inspect) ~= "function" or type(press) ~= "function" then
        error("authored_pickup_stock_input_fixture_unavailable", 0)
    end
    local detail = state.local_pawn
    local pawn, controller = detail and detail.pawn, state.controller
    local actor = StaticFindObject(profile.path)
    if not valid(pawn) or not valid(controller) or not valid(actor) or type(detail.name) ~= "string"
        or pawn:GetFullName() ~= detail.name or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(actor:GetWorld(), state.world)
        or actor:GetFullName() ~= profile.name .. " " .. profile.path
        or not same(actor:GetClass(), StaticFindObject(profile.class)) or pawn.Dead ~= false then
        error("authored_pickup_stock_input_exact_pawn_or_actor_unavailable", 0)
    end
    local box = actor.Box
    local active, claimed, before = inspect(profile.source)
    if active ~= true or claimed ~= false or not math.tointeger(before)
        or not owned(box, actor, "BoxComponent") or not same(box, actor.RootComponent)
        or not owned(pawn.Camera, pawn, "CameraComponent") then
        error("authored_pickup_stock_input_exact_box_or_camera_unavailable", 0)
    end
    press_stock(state, pawn, box, precise_distances[profile.source] ~= nil, "authored_pickup_stock_input", function()
        local consumed, why = press(pawn:GetAddress(), true)
        local still_active, still_claimed, sequence, fixture = inspect(profile.source)
        if consumed ~= true or why ~= "stock_started_consumed" or still_active ~= true
            or still_claimed ~= false or fixture ~= false or not math.tointeger(sequence) or sequence <= before then
            error("authored_pickup_stock_input_unverified:" .. tostring(why), 0)
        end
    end)
    return "exact stock loose pickup input consumed"
end

return input
