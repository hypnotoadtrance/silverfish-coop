local lockpick = {}
local stock_input = require("native_authored_pickup_input_fixture")

local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local action = "native-cistern-lockpick-fixture"
local tag = "native-cistern-lockpick"
local map = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world_path = map .. ".Cistern_P"
local locker_path = world_path .. ":PersistentLevel.LootLocker_Small_BP_C_6"
local locker_class = "/Game/Classes/Actors/Lootables/LootLocker_Small_BP.LootLocker_Small_BP_C"
local pick_class = "/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local run

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end
local function finite(value) return type(value) == "number" and value == value and math.abs(value) < 100000000 end

local function require_value(value, reason, dirty)
    if not value then
        local prefix = dirty and "mutation_unverified_stock_cistern_lockpick_" or "stock_cistern_lockpick_refused_"
        error(prefix .. reason, 0)
    end
    return value
end

local function qualify(state, phase)
    local world, detail, controller = state.world, state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not enabled or phase ~= "hosting" or state.map ~= map or state.listening ~= true or state.server ~= false
        or not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not exact(pawn, "/Game/Classes/Player/Player_FP.Player_FP_C") or pawn:GetFullName() ~= detail.name
        or not exact(controller, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not pawn:HasAuthority() or not controller:HasAuthority()
        or pawn.Dead ~= false or controller.Loading ~= false then return end
    local driver = world.NetDriver
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, world)
        or not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
        or not same(save, pawn.Save) or not same(instance["As Player FP"], pawn)
        or not finite(save.PlayerHealth) or save.PlayerHealth <= 0 then return end
    local actor = StaticFindObject(locker_path)
    if not exact(actor, locker_class) or actor:GetFullName() ~= "LootLocker_Small_BP_C " .. locker_path
        or not same(actor:GetOuter(), world.PersistentLevel) or not same(actor:GetWorld(), world)
        or not actor:HasAuthority() or actor:IsActorBeingDestroyed() or actor.SpawnSalt ~= -1065462208
        or type(actor.Locked) ~= "boolean" then return end
    local location, rotation = actor:K2_GetActorLocation(), actor:K2_GetActorRotation()
    if not location or not finite(location.X) or not finite(location.Y) or not finite(location.Z)
        or (location.X - 5109.10794)^2 + (location.Y - 11363.25031)^2 + (location.Z + 2250)^2 > 1
        or not rotation or not finite(rotation.Yaw) or math.abs(math.abs(rotation.Yaw) - 180) > .2 then return end
    return {world = world, pawn = pawn, name = detail.name, statics = statics, instance = instance, save = save, actor = actor}
end

local function guid(row)
    local value = row and row[item_guid]
    if not value then return end
    local parts, words = {}, {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local part = math.tointeger(value[name])
        if not part or part < -0x80000000 or part > 0xffffffff then return end
        parts[name] = part & 0xffffffff
        words[#words + 1] = parts[name]
    end
    local id = string.format("%08X%08X%08X%08X", table.unpack(words))
    return id:find("[^0]") and id or nil, parts
end

local function inventory(ctx, id)
    local class = StaticFindObject(pick_class)
    if not valid(class) then return end
    local observation = {others = {}, open_slot = false}
    for _, source in ipairs({"PlayerBackpack", "PlayerEquipment"}) do
        local array = ctx.save[source]
        local count = array and math.tointeger(array:GetArrayNum())
        if not count or count < 0 or count > 4096 then return end
        for index = 1, count do
            local row = array[index]
            local kind = row and row[item_class]
            if valid(kind) then
                local identity, parts = guid(row)
                local amount = math.tointeger(row[item_ammo])
                if not identity or not amount or observation.others[identity] then return end
                local picked = same(kind, class)
                if identity == id or picked then
                    if identity ~= id or not picked or observation.item or amount < 1 or amount > 2 then return end
                    observation.item = {source = source, index = index - 1, amount = amount, parts = parts}
                else
                    observation.others[identity] = {source = source, class = kind:GetFullName(), amount = amount}
                end
            elseif source == "PlayerEquipment" and index >= 5 and index <= 8 then
                observation.open_slot = true
            end
        end
    end
    return observation
end

local function same_other_items(first, second)
    for id, row in pairs(first.others) do
        local other = second.others[id]
        if not other or row.source ~= other.source or row.class ~= other.class or row.amount ~= other.amount then return false end
    end
    for id in pairs(second.others) do
        if not first.others[id] then return false end
    end
    return true
end

local function held(ctx, item)
    local actor = ctx.pawn.EquipedItem
    if item and item.source == "PlayerEquipment" and item.index == ctx.save.PlayerEquipIndex
        and exact(actor, pick_class) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
        and same(actor:GetWorld(), ctx.world) and same(actor:GetOwner(), ctx.pawn)
        and same(actor.Instigator, ctx.pawn) and same(actor.User, ctx.pawn) and same(actor.Player_Ref, ctx.pawn)
        and actor.HeldByPlayer == true and actor.Ammo == item.amount then
        return actor
    end
end

local function equip(ctx, id, before)
    local item = before.item
    local in_hand = held(ctx, item)
    if in_hand then return in_hand end
    require_value(ctx.pawn:Check_CanUseEquipment() == true, "equip_unavailable")
    if item.source == "PlayerBackpack" then
        require_value(before.open_slot, "empty_equipment_slot_required")
        local removed, success = {}, {}
        ctx.instance:RemoveItem(item.parts, 0, removed, success)
        require_value(success.Success == true and guid(removed) == id and removed[item_ammo] == item.amount
            and same(removed[item_class], StaticFindObject(pick_class)), "stock_remove_readback", true)
        local accepted = {}
        ctx.instance:EquipItemToLoadout(removed, accepted)
        if accepted.EquippedSuccessfully ~= true then
            local stopped = inventory(ctx, id)
            require_value(stopped and not stopped.item and same_other_items(before, stopped), "equip_refusal_state", true)
            ctx.instance:MoveItemToInventory(removed, false)
            local restored = inventory(ctx, id)
            require_value(restored and restored.item and restored.item.source == "PlayerBackpack"
                and restored.item.amount == item.amount and same_other_items(before, restored), "equip_rollback", true)
            error("stock_cistern_lockpick_refused_equip", 0)
        end
    else
        ctx.pawn:Equip_NextItem(item.index, false)
    end
    local equipped = inventory(ctx, id)
    require_value(equipped and equipped.item and equipped.item.amount == item.amount
        and same_other_items(before, equipped), "inventory_equip_readback", true)
    return require_value(held(ctx, equipped.item), "equipped_actor_readback", true)
end

function lockpick.command(fields, state, phase)
    if fields[2] ~= action then return end
    local id = fields[3]
    require_value(#fields == 3 and type(id) == "string" and #id == 32 and id:match("^[A-F0-9]+$")
        and id:find("[^0]"), "guid_unqualified")
    require_value(not run or (run.state == "retry" and run.guid == id), "attempt_unavailable")
    local ctx = require_value(qualify(state, phase), "scope_unavailable")
    local box = ctx.actor.Box
    require_value(ctx.actor.Locked and exact(box, "/Script/Engine.BoxComponent") and same(box:GetOwner(), ctx.actor),
        "exact_locked_target_unavailable")
    local before = require_value(inventory(ctx, id), "inventory_unqualified")
    require_value(before.item, "owned_guid_required")

    stock_input.open_cabinet(state, phase, ctx.pawn, ctx.actor, box, "cistern-old-nfta-locker", function()
        require_value(same(ctx.pawn.ActorBeingLookedAt, ctx.actor), "stock_look_target_required")
        local tool = equip(ctx, id, before)
        ctx.pawn:LookTrace()
        local distance, range, delay = ctx.pawn.ActorLookedAt_Distance, tool.UseERange, tool.PickTime
        local now = ctx.statics:GetTimeSeconds(ctx.world)
        require_value(same(ctx.pawn.ActorBeingLookedAt, ctx.actor) and same(ctx.pawn.InteractionTarget_Component, box)
            and finite(distance) and distance > 0 and finite(range) and distance <= range and range <= 1000
            and finite(delay) and delay > 0 and delay <= 20 and finite(now) and ctx.actor.Locked == true,
            "stock_primary_scope_unavailable", true)
        run = {world = ctx.world, pawn = ctx.pawn, name = ctx.name, save = ctx.save, actor = ctx.actor,
            guid = id, inventory = before, before = before.item.amount, started = now, delay = delay, state = "pending"}
        tool:Use_Item_Primary(true, 0.0)
        require_value(same(tool.TargetActor, ctx.actor) and same(tool.LockRef, ctx.actor)
            and finite(tool.Target_PickChance) and tool.Target_PickChance > 0, "stock_target_readback", true)
        tool:Use_Item_Primary(false, 0.0)
        return "stock lockpick primary started"
    end)
    return "exact stock Cistern lockpick attempt pending"
end

function lockpick.sync(state, phase)
    if not run or run.state ~= "pending" then return end
    local ctx = qualify(state, phase)
    if not ctx or not same(run.world, ctx.world) or not same(run.pawn, ctx.pawn)
        or not same(run.save, ctx.save) or not same(run.actor, ctx.actor) then return end
    local now = ctx.statics:GetTimeSeconds(ctx.world)
    if not finite(now) or now < run.started + run.delay then return end
    local observed = inventory(ctx, run.guid)
    local after = observed and (observed.item and observed.item.amount or 0)
    if not after or not same_other_items(run.inventory, observed) or after > run.before or after < run.before - 1
        or (after > 0 and not held(ctx, observed.item)) then
        run.state, run.after = "fault", after
    elseif not ctx.actor.Locked then
        run.state, run.after = "unlocked", after
    elseif after == run.before - 1 then
        run.state, run.after = after > 0 and "retry" or "spent", after
    elseif now >= run.started + run.delay + 2 then
        run.state, run.after = "fault", after
    end
end

function lockpick.rows(state, phase, row)
    if not run or phase ~= "hosting" or state.map ~= map or not same(state.world, run.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, run.pawn) then return {} end
    return {row(tag, map, run.name, run.guid, run.state, run.before, run.after or "-")}
end

function lockpick.clear()
    run = nil
end

return lockpick
