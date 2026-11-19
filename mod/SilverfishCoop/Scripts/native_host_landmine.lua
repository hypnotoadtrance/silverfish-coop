local mine = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local mine_class = "/Game/Classes/Equipment/Eqp_LandMine.Eqp_LandMine_C"
local projectile_class = "/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/LandMine_Projectile_BP.LandMine_Projectile_BP_C"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local level_class = "/Game/Classes/Actors/LevelControler_BP.LevelControler_BP_C"
local level_path = world_path .. ":PersistentLevel.LevelControler_BP_C_0"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local saved_items = "Items_3_42BDD59048DF86C357078E80512340F8"
local saved_transform = "Transform_8_BD0C0F504E8E7DC44D04E59C402D9537"
local actions = {
    ["native-host-landmine-prepare-fixture"] = "prepare",
    ["native-host-landmine-press-fixture"] = "press",
    ["native-host-landmine-release-fixture"] = "release",
    ["native-host-landmine-inspect-fixture"] = "inspect",
    ["native-host-landmine-persist-fixture"] = "persist",
}
local run, observation

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end
local function finite(value) return type(value) == "number" and value == value and math.abs(value) < 100000000 end
local function point(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return { X = value.X, Y = value.Y, Z = value.Z }
    end
end
local function square(a, b) return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2 end
local function text(value)
    if type(value) == "userdata" then return value:ToString() end
    if type(value) == "string" then return value end
end
local function require_value(value, reason, dirty)
    if not value then error((dirty and "mutation_unverified_" or "") .. "stock_host_landmine_" .. reason, 0) end
    return value
end
local function age(context)
    return context.statics:GetRealTimeSeconds(context.world)
end

local function context(state, phase, name)
    local host = state.local_pawn
    local pawn, controller, world = host and host.pawn, state.controller, state.world
    if not enabled or phase ~= "hosting" or state.map ~= map or state.listening ~= true or state.server ~= false
        or state.clients ~= 1 or #state.pawns ~= 2 or not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not host or (name and host.name ~= name)
        or not exact(pawn, "/Game/Classes/Player/Player_FP.Player_FP_C") or pawn:GetFullName() ~= host.name
        or not exact(controller, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world)
        or not controller:HasAuthority() or not controller:IsLocalController() or not pawn:HasAuthority()
        or pawn.Dead ~= false or controller.Loading ~= false then return end
    local driver = world.NetDriver
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, world)
        or not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
        or not same(save, pawn.Save) or not same(instance["As Player FP"], pawn) then return end
    for _, info in ipairs(state.pawns) do
        if info.kind == "guest" then
            local ok, guest, detached = pcall(guest_save.exact_item_context, state, phase, info.name)
            if not ok or not valid(guest) or not guest:HasAuthority() or not same(guest:GetWorld(), world)
                or not valid(detached) or same(detached, save) or not same(guest.Save, detached)
                or guest.Dead ~= false or not finite(detached.PlayerHealth) or detached.PlayerHealth <= 1 then return end
            return { pawn = pawn, controller = controller, world = world, statics = statics, instance = instance,
                save = save, name = host.name, guest = guest, detached = detached }
        end
    end
end

local function guid(row)
    local words, id = {}, row and row[item_guid]
    if not id then return end
    for _, key in ipairs({ "A", "B", "C", "D" }) do
        local part = math.tointeger(id[key])
        if not part or part < -0x80000000 or part > 0xffffffff then return end
        words[#words + 1] = part & 0xffffffff
    end
    local value = string.format("%08X%08X%08X%08X", table.unpack(words))
    return value:find("[^0]") and value or nil
end

local function inventory(save, class)
    local total, found, equipped = 0
    for _, source in ipairs({ "PlayerBackpack", "PlayerEquipment" }) do
        local entries = save[source]
        local count = entries and math.tointeger(entries:GetArrayNum())
        if not count or count < 0 or count > 4096 then return end
        for index = 1, count do
            local row = entries[index]
            if row and same(row[item_class], class) then
                local identity, amount = guid(row), math.tointeger(row[item_ammo])
                if not identity or not amount or amount < 1 or found then return end
                total, found = amount, identity
                if source == "PlayerEquipment" then equipped = index - 1 end
            end
        end
    end
    return total, found, equipped
end

local function held(ctx, class, identity)
    local amount, real, index = inventory(ctx.save, class)
    local actor = ctx.pawn.EquipedItem
    if amount ~= 1 or real ~= identity or index ~= ctx.save.PlayerEquipIndex
        or not exact(actor, mine_class) or not actor:HasAuthority() or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), ctx.world) or not same(actor:GetOwner(), ctx.pawn)
        or not same(actor.Instigator, ctx.pawn) or not same(actor.User, ctx.pawn)
        or actor.HeldByPlayer ~= true or actor.Armed ~= false or actor.Ammo ~= 1 then return end
    return actor
end

local function tagged(ctx)
    local tag = FName("PlayerExplosive", EFindName.FNAME_Find)
    require_value(tag:GetComparisonIndex() ~= 0 and tag:ToString() == "PlayerExplosive", "tag_unavailable")
    local found, count = nil, 0
    for _, actor in ipairs(FindAllOf("Eqp_LandMine_C") or {}) do
        if exact(actor, mine_class) and same(actor:GetWorld(), ctx.world) and actor:ActorHasTag(tag) then
            count = count + 1
            if actor:HasAuthority() and not actor:IsActorBeingDestroyed() and actor.Armed == true
                and same(actor.Instigator, ctx.pawn) and actor:GetFullName():find(world_path .. ":PersistentLevel.", 1, true)
                and point(actor:K2_GetActorLocation()) then found = actor end
        end
    end
    return count, found
end

local function launched(ctx)
    local count, found = 0, nil
    for _, actor in ipairs(FindAllOf("LandMine_Projectile_BP_C") or {}) do
        if exact(actor, projectile_class) and same(actor:GetWorld(), ctx.world) and not actor:IsActorBeingDestroyed() then
            count = count + 1
            if actor:HasAuthority() and same(actor.Instigator, ctx.pawn) then found = actor end
        end
    end
    return count, found
end

local function flights(ctx, projectile)
    if type(SilverfishObserveNativeHostLandmineProjectile) ~= "function" then return -1, "observer_unavailable" end
    if projectile then
        return SilverfishObserveNativeHostLandmineProjectile(ctx.world:GetAddress(), ctx.pawn:GetAddress(), projectile:GetAddress())
    end
    return SilverfishObserveNativeHostLandmineProjectile(ctx.world:GetAddress(), ctx.pawn:GetAddress())
end

local function retention(ctx, actor)
    local here = valid(actor) and point(actor:K2_GetActorLocation())
    if not here then return end
    local now = age(ctx)
    local identity = actor:GetFullName()
    if run.retained_actor and identity ~= run.retained_actor then return end
    if not run.retained_at or square(here, run.retained_at) > 4 then
        run.retained_actor, run.retained_at, run.retained_since = identity, here, now
    end
    return now - run.retained_since
end

local function stored(ctx)
    local count, location = 0, nil
    local entries = ctx.save.PlayerExplosives
    if not entries then return end
    entries:ForEach(function(key, value)
        if text(key:get()) ~= "Observitory_P" then return end
        local record = value:get()
        local items = record and record[saved_items]
        count = items and math.tointeger(items:GetArrayNum())
        if count == 1 then
            local row = items[1]
            local transform = row and row[saved_transform]
            location = point(transform and transform.Translation)
        end
        return true
    end)
    return count, location
end

local function prepare(ctx)
    require_value(not run, "fixture_already_started")
    local class = StaticFindObject(mine_class)
    if not valid(class) then LoadAsset(mine_class); class = StaticFindObject(mine_class) end
    local factory = StaticFindObject(factory_path)
    local equipment = ctx.save.PlayerEquipment
    local open_slot = false
    for index = 1, math.min(equipment and equipment:GetArrayNum() or 0, 20) do
        local row = equipment[index]
        if row and not valid(row[item_class]) then open_slot = true; break end
    end
    require_value(valid(class) and class:GetFullName() == "BlueprintGeneratedClass " .. mine_class and valid(factory)
        and factory:GetFullName() == "FL_Inventory_C " .. factory_path and open_slot
        and ctx.pawn:Check_CanUseEquipment() == true and inventory(ctx.save, class) == 0
        and inventory(ctx.detached, class) == 0 and tagged(ctx) == 0 and stored(ctx) == 0, "seed_ineligible")
    local out = {}
    factory:MakeDefaultItem(class, ctx.pawn, out)
    local row = out.Item or out
    local identity = guid(row)
    local defaults = class:GetCDO()
    require_value(identity and same(row[item_class], class) and valid(defaults) and defaults.Stacks == true
        and defaults.Armed == false and defaults.Ammo == 1 and row[item_ammo] == 1, "typed_template_unqualified")
    run = { pawn = ctx.pawn, save = ctx.save, guest = ctx.guest, detached = ctx.detached, class = class,
        guid = identity, state = "equipping", presses = 0, releases = 0 }
    local accepted = {}
    ctx.instance:EquipItemToLoadout(row, accepted)
    require_value((accepted.EquippedSuccessfully == true or accepted[1] == true)
        and inventory(ctx.detached, class) == 0 and held(ctx, class, identity), "equipment_readback", true)
    run.state = "equipped"
end

local function owns_run(ctx)
    return run and same(ctx.pawn, run.pawn) and same(ctx.save, run.save) and same(ctx.guest, run.guest)
        and same(ctx.detached, run.detached)
end

local function clear_lane(ctx, tool)
    local origin, guest = point(ctx.pawn:K2_GetActorLocation()), point(ctx.guest:K2_GetActorLocation())
    local arrow = ctx.pawn.Arrow_NadeThrow
    local camera = ctx.pawn.Camera
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    require_value(origin and guest and valid(arrow) and same(arrow:GetOwner(), ctx.pawn)
        and valid(camera) and same(camera:GetOwner(), ctx.pawn) and valid(trace), "stock_aim_unavailable")
    local lens = require_value(point(camera:K2_GetComponentLocation()), "stock_camera_unavailable")
    local previous = ctx.controller:GetControlRotation()
    require_value(previous and finite(previous.Pitch) and finite(previous.Yaw) and finite(previous.Roll), "control_rotation_unavailable")
    local color, ignores = { R = 0, G = 0, B = 0, A = 0 }, {ctx.pawn, ctx.guest, tool}
    local function cast(from, to, out)
        return trace:LineTraceSingle(ctx.pawn, from, to, 0, false, ignores, 0, out, true, color, color, 0)
    end
    local selected
    for _, turn in ipairs({ 0, 45, -45, 90, -90, 135, -135, 180 }) do
        local yaw = previous.Yaw + turn
        local x, y = origin.X + 430 * math.cos(math.rad(yaw)), origin.Y + 430 * math.sin(math.rad(yaw))
        local hit = {}
        local found = cast({X = x, Y = y, Z = lens.Z + 25}, {X = x, Y = y, Z = origin.Z - 210}, hit)
        hit = hit.OutHit or hit
        local floor, normal = point(hit.ImpactPoint), point(hit.ImpactNormal)
        local surface = hit.Component and hit.Component:Get()
        if found == true and floor and normal and normal.Z > .9 and valid(surface) and surface:GetCollisionObjectType() == 0
            and floor.Z < lens.Z - 65 and floor.Z > lens.Z - 245 and square(floor, guest) > 430^2 then
            local target = { X = x, Y = y, Z = floor.Z + 20 }
            if cast(lens, target, {}) == false then
                selected = { floor = floor, surface = surface, trace = trace, color = color }
                break
            end
        end
    end
    require_value(selected, "stock_floor_throw_lane_unavailable")
    run.lane, run.origin, run.arrow, run.camera = selected, origin, arrow, camera
end

local function at_stock_aim(ctx, tool, invoke)
    local lane, arrow, camera = run.lane, run.arrow, run.camera
    require_value(lane and valid(lane.surface) and same(ctx.pawn.Arrow_NadeThrow, arrow) and same(ctx.pawn.Camera, camera)
        and same(arrow:GetOwner(), ctx.pawn) and same(camera:GetOwner(), ctx.pawn), "stock_throw_component_changed", true)
    local function rotation(value)
        if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
            return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
        end
    end
    local before_control = rotation(ctx.controller:GetControlRotation())
    local before_camera = rotation(camera:K2_GetComponentRotation())
    local before_arrow = rotation(arrow:K2_GetComponentRotation())
    require_value(before_control and before_camera and before_arrow, "stock_throw_rotation_unavailable", true)
    local function aim(from)
        local dx, dy, dz = lane.floor.X - from.X, lane.floor.Y - from.Y, lane.floor.Z - from.Z
        return { Pitch = math.deg(math.atan(dz, math.sqrt(dx^2 + dy^2))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0 }
    end
    local ok, result = pcall(function()
        local eye = require_value(point(camera:K2_GetComponentLocation()), "stock_throw_eye_unavailable", true)
        local view = aim(eye)
        ctx.controller:SetControlRotation(view)
        camera:K2_SetWorldRotation(view, false, {}, true)
        local from = require_value(point(arrow:K2_GetComponentLocation()), "stock_throw_origin_unavailable", true)
        arrow:K2_SetWorldRotation(aim(from), false, {}, true)
        local forward = require_value(point(arrow:GetForwardVector()), "stock_throw_direction_unavailable", true)
        local extent = math.sqrt(square(from, lane.floor))
        local dot = ((lane.floor.X - from.X) * forward.X + (lane.floor.Y - from.Y) * forward.Y
            + (lane.floor.Z - from.Z) * forward.Z) / extent
        require_value(extent >= 225 and extent <= 680 and dot > .998 and forward.Z < -.06,
            "stock_throw_arrow_unaligned", true)
        local reach = extent + 30
        local target = { X = from.X + reach * forward.X, Y = from.Y + reach * forward.Y, Z = from.Z + reach * forward.Z }
        local hit = {}
        local found = lane.trace:LineTraceSingle(ctx.pawn, from, target, 0, false, {ctx.pawn, ctx.guest, tool}, 0,
            hit, true, lane.color, lane.color, 0)
        hit = hit.OutHit or hit
        local surface = hit.Component and hit.Component:Get()
        local impact = point(hit.ImpactPoint)
        require_value(found == true and same(surface, lane.surface) and impact and square(impact, lane.floor) <= 36^2,
            "stock_throw_ray_did_not_reach_floor", true)
        run.aim_dot = dot
        return invoke(forward)
    end)
    ctx.controller:SetControlRotation(before_control)
    camera:K2_SetWorldRotation(before_camera, false, {}, true)
    arrow:K2_SetWorldRotation(before_arrow, false, {}, true)
    if not ok then error(result, 0) end
    return result
end

local function press(ctx)
    local active, flight_state = flights(ctx)
    require_value(active == 0 and flight_state == "unwatched", "projectile_preflight_" .. flight_state)
    require_value(owns_run(ctx) and run.state == "equipped" and tagged(ctx) == 0
        and finite(age(ctx)) and finite(ctx.save.PlayerHealth) and ctx.save.PlayerHealth > 1, "press_scope_unavailable")
    local tool = require_value(held(ctx, run.class, run.guid), "exact_hand_item_required")
    require_value(tool.Cooking == false and tool.Held_Primary == false and tool.Held_Secondary == false
        and tool.ThrowDelay == .5 and tool.FuseTime_Player == 3, "unmodified_primary_required")
    clear_lane(ctx, tool)
    run.started, run.guest_health, run.host_health = age(ctx), ctx.detached.PlayerHealth, ctx.save.PlayerHealth
    run.state, run.presses = "pressed", 1
    tool:Use_Item_Primary(true, 0.0)
    require_value(tool.Held_Primary == true, "stock_press_readback", true)
end

local function release(ctx)
    require_value(owns_run(ctx) and run.state == "pressed", "release_scope_unavailable", true)
    local tool = require_value(held(ctx, run.class, run.guid), "release_item_unavailable", true)
    require_value(tool.Cooking == true and tool.Held_Primary == true and finite(tool.CookTime)
        and tool.CookTime >= 0 and tool.CookTime < 1.4 and age(ctx) - run.started <= 2.25, "stock_cook_window_elapsed", true)
    at_stock_aim(ctx, tool, function(forward)
        run.state, run.releases = "released", 1
        tool:Use_Item_Primary(false, 0.0)
        local count, projectile = launched(ctx)
        require_value(count == 1 and valid(projectile), "stock_projectile_readback", true)
        run.projectile_name = projectile:GetFullName()
        local active, flight_state = flights(ctx, projectile)
        require_value(active == 1 and flight_state == "active", "projectile_bind_" .. flight_state, true)
        local movement = projectile.ProjectileMovement
        local velocity = valid(movement) and point(movement.Velocity)
        local speed = velocity and math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2)
        require_value(speed and speed > 100 and (velocity.X * forward.X + velocity.Y * forward.Y
            + velocity.Z * forward.Z) / speed > .998, "stock_projectile_velocity_readback", true)
    end)
end

local function inspect(ctx, state, flight, flight_state)
    local count, actor = tagged(ctx)
    if flight == nil then flight, flight_state = flights(ctx) end
    local flight_position
    if flight_state == "active" and run and run.projectile_name then
        local _, projectile = launched(ctx)
        if valid(projectile) and projectile:GetFullName() == run.projectile_name then
            flight_position = point(projectile:K2_GetActorLocation())
        end
    end
    local saved, location = stored(ctx)
    local position = actor and point(actor:K2_GetActorLocation())
    local class = StaticFindObject(mine_class)
    local owned, guest_owned = valid(class) and inventory(ctx.save, class), valid(class) and inventory(ctx.detached, class)
    local result = { state = state, pawn = ctx.name, guid = run and run.guid or "-", count = count,
        actor = actor and actor:GetFullName() or "-", position = position, saved = saved, remaining = owned,
        guest_remaining = guest_owned, same_transform = position and location and square(position, location) <= 4,
        presses = run and run.presses or 0, releases = run and run.releases or 0,
        projectile = run and run.projectile_name or "-", flights = flight, flight_state = flight_state,
        flight_position = flight_position,
        aim_dot = run and run.aim_dot }
    return result, actor
end

local function persist(ctx)
    local snapshot, actor = inspect(ctx, "persisted")
    local guest_position = point(ctx.guest:K2_GetActorLocation())
    local retained = owns_run(ctx) and retention(ctx, actor)
    require_value(retained and retained >= .30 and run.state == "released" and snapshot.count == 1
        and snapshot.remaining == 0 and snapshot.guest_remaining == 0 and snapshot.flights == 0
        and snapshot.flight_state == "retired"
        and snapshot.position and square(snapshot.position, run.origin) < 1800^2
        and guest_position and square(snapshot.position, guest_position) > 225^2
        and ctx.detached.PlayerHealth == run.guest_health and ctx.save.PlayerHealth == run.host_health,
        "stock_deployment_readback", true)
    local level = StaticFindObject(level_path)
    require_value(exact(level, level_class) and level:HasAuthority() and same(level:GetWorld(), ctx.world)
        and same(level["As Game Instance FP"], ctx.instance) and same(level["As Player FP"], ctx.pawn),
        "stock_level_canonical_owner", true)
    level:SavePlayerExplosives()
    local saved, location = stored(ctx)
    require_value(saved == 1 and location and square(snapshot.position, location) <= 4
        and same(ctx.instance.SaveSlot_Ref, ctx.save), "typed_map_record_readback", true)
    run.state = "persisted"
    snapshot.saved, snapshot.same_transform = saved, true
    observation = snapshot
end

function mine.command(fields, state, phase)
    local action = actions[fields[2]]
    if not action then return end
    local ctx = require_value(#fields == 3 and context(state, phase, fields[3]), "exact_headless_host_required")
    observation = nil
    if action == "inspect" then observation = inspect(ctx, "inspected")
    elseif action == "prepare" then prepare(ctx)
    elseif action == "press" then press(ctx)
    elseif action == "release" then release(ctx)
    else persist(ctx) end
    return "stock host LandMine " .. action .. " observed"
end

function mine.rows(state, phase, status_row)
    if not (run or observation) then return {} end
    local ctx = context(state, phase)
    if not ctx then
        if state.map ~= map then run, observation = nil, nil end
        return {}
    end
    local found = observation
    if not found and owns_run(ctx) then
        local step = run.state
        local flight, flight_state = flights(ctx)
        if step == "pressed" then
            local tool = held(ctx, run.class, run.guid)
            if valid(tool) and tool.Cooking == true and tool.Held_Primary == true then step = "primed" end
        elseif step == "released" then
            if flight == 0 and flight_state == "retired" then
                local count, actor = tagged(ctx)
                local retained = count == 1 and retention(ctx, actor)
                if retained and retained >= .30 then
                    step = "placed"
                elseif (run.retained_actor and not retained) or count > 1 then
                    step = "retention_failed"
                end
            elseif flight_state ~= "active" and flight_state ~= "retirement_pending" then
                step = "retention_failed"
            end
        end
        found = inspect(ctx, step, flight, flight_state)
    end
    if not found or found.pawn ~= ctx.name then return {} end
    local at = found.position or {}
    local flight = found.flight_position or {}
    return { status_row("native-host-landmine", map, found.pawn, found.state, found.guid, found.actor, found.projectile,
        found.presses, found.releases, found.count, found.saved or "-", found.remaining or "-", found.guest_remaining or "-",
        found.same_transform and 1 or 0, at.X or "-", at.Y or "-", at.Z or "-", found.flights,
        flight.X or "-", flight.Y or "-", flight.Z or "-", found.aim_dot or "-", found.flight_state) }
end

return mine
