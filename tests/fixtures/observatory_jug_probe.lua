local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local jug_case = mailbox and mailbox:match("[/\\]observatory%-jug%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
local health_case = mailbox and mailbox:match("[/\\]guest%-regen%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not (jug_case or health_case) then return end

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local npc_path = observatory .. ":PersistentLevel.NPC_LoneStalker_C_0"
local prop_assets = "/Game/Assets/StaticMeshes/Mesh_Props/"
local jug_class = prop_assets .. "Prop_PlasticJug.Prop_PlasticJug_C"
local toolbox_class = prop_assets .. "Prop_ToolBox01.Prop_ToolBox01_C"
local pistol_class = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local jug_suffixes = {["0"] = true, ["1"] = true, ["18"] = true, ["19"] = true}
local exact_jug = "Prop_PlasticJug_C " .. observatory
    .. ":PersistentLevel.Prop_PlasticJug_GEN_VARIABLE_Prop_PlasticJug_C_CAT_0"
local hit_event = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C:"
    .. "BndEvt__PhysicsProp_Master_BP_PropMesh_K2Node_ComponentBoundEvent_0_ComponentHitSignature__DelegateSignature"
local damage_event = "/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage"
local npc_damage_event = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage"
local remembered_target = "CombatTarget_7_5EB8D2544EEE9E5E6FB88485135FB2B4"
local command, reply = mailbox .. "/npc-jug-command.tsv", mailbox .. "/npc-jug-reply.tsv"
local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
local color = {R = 0, G = 0, B = 0, A = 0}
local jug, npc, collision_target, thrower
local impact, host_hits, guest_hits = 0, 0, 0
local largest_impulse, largest_thrower_impulse
local impulses_decoded, impact_instigator, npc_damage_calls, npc_damage_instigator = 0, "-", 0, "-"
local armed, thrown, hook, guest_fired, previous = false, false, false, false, nil

local function valid(value)
    return value and value:IsValid()
end

local function unwrap(value)
    if type(value) == "userdata" and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then
        return value:get()
    end
    return value
end

local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function name(value)
    return valid(value) and value:GetFullName() or "-"
end

local function health(pawn)
    local save = valid(pawn) and pawn.Save
    return valid(save) and tostring(save.PlayerHealth) or "-"
end

local function context()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. observatory, "awaiting stock Observatory")
    local driver, pawn = world.NetDriver, statics:GetPlayerPawn(world, 0)
    assert(valid(driver) and valid(pawn) and pawn:IsLocallyControlled(), "awaiting the Observatory co-op seat")
    local hosting = pawn:HasAuthority() and not valid(driver.ServerConnection)
    local guest
    if hosting and #driver.ClientConnections == 1 then
        local peer = unwrap(driver.ClientConnections[1]).PlayerController
        guest = valid(peer) and peer.Pawn
    end
    assert(hosting or valid(driver.ServerConnection), "not a co-op gameplay world")
    return world, pawn, guest, hosting
end

local function npc_in(world)
    local actor = StaticFindObject(npc_path)
    assert(valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed(),
        "the original Observatory stalker is unavailable")
    return actor
end

local function location(x, y, z)
    return {X = x, Y = y, Z = z}
end

local function angle(origin, target)
    local x, y, z = target.X - origin.X, target.Y - origin.Y, target.Z - origin.Z
    return {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
end

local function orient(pawn, rotation)
    pawn.Controller:SetControlRotation(rotation)
    pawn:K2_SetActorRotation({Pitch = 0, Yaw = rotation.Yaw, Roll = 0}, false)
    pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
end

local function stage(pawn, center, distance, heading, ignore)
    local x, y = center.X + distance * math.cos(heading), center.Y + distance * math.sin(heading)
    local out = {}
    local on_floor = trace:LineTraceSingle(pawn, location(x, y, center.Z + 90), location(x, y, center.Z - 190),
        0, false, ignore, 0, out, true, color, color, 0)
    if not on_floor then return false end
    local floor = (out.OutHit or out).ImpactPoint
    local at = location(x, y, floor.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() + 2)
    local rotation = angle(at, center)
    rotation.Pitch = 0
    return pawn:K2_TeleportTo(at, rotation) == true
end

local function select_prop(world, pawn, center, toolbox)
    local class = StaticFindObject(toolbox and toolbox_class or jug_class)
    assert(valid(class), "the stock Observatory prop class is unavailable")
    local found = {}
    statics:GetAllActorsOfClass(world, class, found)
    local nearest, best
    for _, candidate in ipairs(found.OutActors or found) do
        local actor = unwrap(candidate)
        local path = name(actor)
        local authored = toolbox and path:match("%.Observitory_P:PersistentLevel%.Prop_ToolBox01_C_7$")
            or not toolbox and jug_suffixes[path:match("Prop_PlasticJug_C_CAT_(%d+)$")]
        if guest_fired and not toolbox then authored = path == exact_jug end
        if authored and same(actor:GetClass(), class) and actor:HasAuthority()
            and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() and actor.Broken == false
            and same(actor.PropMesh, actor.RootComponent) and same(actor.PropMesh:GetOwner(), actor)
            and actor.PropMesh:IsSimulatingPhysics(FName("None")) and actor.PropMesh:GetMass() <= pawn.MaxHoldWeight then
            local at = actor:K2_GetActorLocation()
            local distance = (at.X - center.X)^2 + (at.Y - center.Y)^2 + (at.Z - center.Z)^2
            if not best or distance < best then nearest, best = actor, distance end
        end
    end
    assert(nearest, "no loaded, simulating and holdable authored Observatory prop")
    return nearest
end

local function place_players(pawn, guest, target, prop)
    assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled()
        and valid(pawn.Save) and valid(guest.Save) and not same(pawn.Save, guest.Save),
        "the original Observatory NPC needs one enrolled guest with a detached save")
    assert(target:HasAuthority() and target.Alive == true and valid(target.Controller)
        and not valid(target.CombatTarget) and not valid(pawn.HeldObject), "the Observatory stalker is not neutral and ready")
    local center = target:K2_GetActorLocation()
    local ignore = {pawn, guest, target, prop}
    local clear_heading
    for _, distance in ipairs({270, 340, 220}) do
        for degrees = 0, 330, 30 do
            local heading = math.rad(degrees)
            if stage(pawn, center, distance, heading, ignore) then
                local eye = pawn.Camera:K2_GetComponentLocation()
                if not trace:LineTraceSingle(pawn, eye, center, 0, false, ignore, 0, {}, true, color, color, 0) then
                    clear_heading = heading
                    break
                end
            end
        end
        if clear_heading then break end
    end
    assert(clear_heading, "no unobstructed stock throw lane to the exact Observatory stalker")
    local guest_placed = stage(guest, center, 360, clear_heading + math.rad(60), ignore)
        or stage(guest, center, 360, clear_heading - math.rad(60), ignore)
    assert(guest_placed, "no standing position for the enrolled guest near the stalker")
    guest:ForceNetUpdate()
    return center
end

local function prepare(world, pawn, guest, target, toolbox)
    local prop = select_prop(world, pawn, target:K2_GetActorLocation(), toolbox)
    if guest_fired then
        assert(same(target, npc) and target.Alive == true and same(target.CombatTarget, guest)
            and npc_damage_calls == 1 and npc_damage_instigator == name(guest) and not valid(pawn.HeldObject),
            "the exact stalker must already target the genuine guest attacker")
    end
    local center = guest_fired and target:K2_GetActorLocation() or place_players(pawn, guest, target, prop)
    local eye = pawn.Camera:K2_GetComponentLocation()
    orient(pawn, angle(eye, center))
    local direction = pawn.Camera:GetForwardVector()
    local at = location(eye.X + 95 * direction.X, eye.Y + 95 * direction.Y, eye.Z + 95 * direction.Z)
    assert(prop:K2_SetActorLocation(at, false, {}, true), "could not stage the authored prop on the stock aim ray")
    prop.PropMesh:SetPhysicsLinearVelocity(location(0, 0, 0), false, FName("None"))
    pawn:LookTrace()
    assert(same(pawn.ActorBeingLookedAt, prop), "stock LookTrace did not focus the authored prop: " .. name(pawn.ActorBeingLookedAt))
    pawn:Grab_Release_Object(false, false, true)
    assert(same(pawn.HeldObject, prop) and same(pawn.PhysicsHandle:GetGrabbedComponent(), prop.PropMesh),
        "the stock pickup function did not grab the authored prop")
    jug, npc = prop, target
    return {name(prop), name(target), name(pawn), name(guest), prop.PropMesh:GetMass(), pawn.MaxHoldWeight}
end

local function equip_host_pistol(world, pawn, class)
    local factory = StaticFindObject(inventory)
    assert(valid(factory), "stock item factory is unavailable")
    local out = {}
    factory:MakeDefaultItem(class, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], class), "stock light pistol was not created")
    row[item_ammo] = 1
    local accepted = {}
    world.OwningGameInstance:EquipItemToLoadout(row, accepted)
    assert(accepted.EquippedSuccessfully or accepted[1], "stock light pistol loadout was refused")
end

local function fire_pistol(world, pawn, guest, target, remote)
    local center = not armed and place_players(pawn, guest, target) or target:K2_GetActorLocation()
    local shooter = remote and guest or pawn
    local class = StaticFindObject(pistol_class)
    if not valid(class) then
        LoadAsset(pistol_class)
        class = StaticFindObject(pistol_class)
    end
    assert(valid(class) and valid(shooter) and shooter:HasAuthority(), "stock light pistol or exact shooter is unavailable")
    if not remote then equip_host_pistol(world, pawn, class) end
    local weapon = shooter.EquipedItem
    assert(valid(weapon) and weapon:HasAuthority() and same(weapon:GetClass(), class) and same(weapon.User, shooter)
        and same(weapon:GetOwner(), shooter) and weapon.Ammo > 0, "the exact shooter has no loaded stock light pistol")
    local muzzle = weapon.Arrow_Muzzle
    assert(valid(muzzle) and same(muzzle:GetOwner(), weapon), "stock light pistol muzzle is unavailable")
    local origin = muzzle:K2_GetComponentLocation()
    local direction, component_name
    for _, offset in ipairs({25, 10, -8, 42}) do
        local point = location(center.X, center.Y, center.Z + offset)
        local hit = {}
        if trace:LineTraceSingle(shooter, origin, point, 0, false, {shooter, weapon}, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            local component = hit.Component and hit.Component:Get()
            if valid(component) and same(component:GetOwner(), target) then
                direction, component_name = angle(origin, point), name(component)
                break
            end
        end
    end
    assert(direction, "the stock light pistol has no clear shot at the exact stalker")
    npc, armed = target, true
    local ammo = weapon.Ammo
    local original = muzzle:K2_GetComponentRotation()
    local fired, failure = pcall(function()
        muzzle:K2_SetWorldRotation(direction, false, {}, true)
        weapon:FireWeapon()
    end)
    muzzle:K2_SetWorldRotation(original, false, {}, true)
    assert(fired and weapon.Ammo == ammo - 1, failure or "the stock light pistol did not expend one round")
    guest_fired = guest_fired or remote
    return {name(weapon), name(target), name(pawn), name(guest), component_name}
end

local function register_hooks()
    if hook then return end
    assert(valid(StaticFindObject(hit_event)) and valid(StaticFindObject(damage_event))
        and valid(StaticFindObject(npc_damage_event)), "stock hit or damage hook unavailable")
    RegisterHook(hit_event, function(context_value, _, other, _other_component, normal_impulse)
        if armed and thrown and same(unwrap(context_value), jug) and same(unwrap(other), npc) then
            impact, collision_target = impact + 1, name(npc)
            local vector = unwrap(normal_impulse)
            local x, y, z = vector and vector.X, vector and vector.Y, vector and vector.Z
            if type(x) == "number" and type(y) == "number" and type(z) == "number" then
                impulses_decoded = impulses_decoded + 1
                local strength = math.sqrt(x*x + y*y + z*z)
                local instigator = jug:GetInstigator()
                if not largest_impulse or strength > largest_impulse then
                    largest_impulse, impact_instigator = strength, name(instigator)
                end
                if same(instigator, thrower) then
                    largest_thrower_impulse = math.max(largest_thrower_impulse or 0, strength)
                end
            end
        end
    end)
    RegisterHook(npc_damage_event, function(context_value, _, _, instigator)
        if armed and same(unwrap(context_value), npc) then
            npc_damage_calls, npc_damage_instigator = npc_damage_calls + 1, name(unwrap(instigator))
        end
    end)
    RegisterHook(damage_event, function(context_value, _, _, instigator)
        if not armed or not same(unwrap(instigator), npc) then return end
        local victim = unwrap(context_value)
        if valid(victim) and victim:HasAuthority() and same(victim:GetWorld(), npc:GetWorld()) then
            if victim:IsLocallyControlled() then
                host_hits = host_hits + 1
            else
                guest_hits = guest_hits + 1
            end
        end
    end)
    hook = true
end

local function sample(actor, pawn, guest)
    local threats = {}
    for index = 1, actor.TargetList:GetArrayNum() do
        local target = actor.TargetList[index][remembered_target]
        if valid(target) then threats[#threats + 1] = name(target) end
    end
    return {name(actor), actor:HasAuthority() and 1 or 0, name(actor.Controller), name(actor.CombatTarget),
        actor.Health, actor.Aggro_Level, actor.HasTargetLOS and 1 or 0, name(pawn), health(pawn), name(guest), health(guest),
        table.concat(threats, "|"), impact, collision_target or "-", host_hits, guest_hits,
        largest_impulse or "-", impact_instigator, impulses_decoded, npc_damage_calls, npc_damage_instigator,
        largest_thrower_impulse or "-"}
end

local function stock_health(world, pawn)
    local controller, instance, hud = pawn.Controller, world.OwningGameInstance, pawn.HUD
    local hud_class = StaticFindObject("/Game/Classes/GUI/HUD_Widget.HUD_Widget_C")
    local inventory_class = StaticFindObject("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")
    local card_class = StaticFindObject("/Game/Classes/GUI/New/Components/PlayerCard.PlayerCard_C")
    assert(valid(hud) and same(hud:GetClass(), hud_class) and same(hud["As Player FP"], pawn)
        and same(hud["As Game Instance FP"], instance) and same(instance.SaveSlot_Ref, pawn.Save),
        "the stock HUD is not bound to the isolated local guest")
    local slot = instance.CurrentSaveSlot
    if type(slot) == "userdata" then slot = slot:ToString() end
    assert(type(slot) == "string" and slot:match("^SFCoopGuest_[a-f0-9]+$") and instance.SaveAfterTransition == false,
        "the stock health readback escaped its co-op save scope")
    for index = math.min(16, controller.MenuStack:GetArrayNum()), 1, -1 do
        local screen = unwrap(controller.MenuStack[index])
        if valid(screen) and same(screen:GetClass(), inventory_class) then
            local card = screen.PlayerCard
            assert(valid(card) and same(card:GetClass(), card_class) and same(card.PlayerFP, pawn),
                "the stock PlayerCard is not bound to the isolated local guest")
            return {name(pawn), slot, health(pawn), pawn.MaxHealth, hud:Get_ProgressBar_Health_Percent(),
                card:Get_ProgressBar_Health_Percent()}
        end
    end
    error("the stock InventoryScreen is not open", 0)
end

local function down_host(actor, pawn, guest)
    assert(armed and same(actor, npc) and same(actor.CombatTarget, pawn) and npc_damage_calls > 0
        and npc_damage_instigator == name(pawn), "the exact stalker is not targeting its stock attacker")
    assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled()
        and pawn.Dead == false and guest.Dead == false and valid(pawn.Save) and valid(guest.Save)
        and not same(pawn.Save, guest.Save), "the scoped host downing needs independent living pawns")
    if guest_fired then
        local own_threat, guest_threat = false, false
        for index = 1, actor.TargetList:GetArrayNum() do
            local target = actor.TargetList[index][remembered_target]
            own_threat = own_threat or same(target, pawn)
            guest_threat = guest_threat or same(target, guest)
        end
        assert(own_threat and guest_threat, "the stock stalker did not retain both genuine player threats")
    end
    local save, remote_save = pawn.Save, guest.Save
    local before, guest_before = save.PlayerHealth, remote_save.PlayerHealth
    assert(before > 0 and before <= 1000000 and guest_before > 0, "the scoped host downing needs valid player health")
    pawn:Deal_DirectDamage(before + 1, guest, FText("Observatory stock retarget fixture"), false)
    assert(same(pawn.Save, save) and same(guest.Save, remote_save) and pawn.Dead == false and guest.Dead == false
        and save.PlayerHealth <= 0 and remote_save.PlayerHealth == guest_before,
        "the stock lethal hit did not preserve both scoped pawns and independent guest health")
    return {name(pawn), before, save.PlayerHealth, name(guest), guest_before, remote_save.PlayerHealth}
end

local function observe(operation)
    local world, pawn, guest, hosting = context()
    if health_case then
        assert(operation == "hudhealth" and not hosting, "the regeneration fixture only reads the guest's stock health widgets")
        return stock_health(world, pawn)
    end
    local actor = npc_in(world)
    if operation == "sample" then return sample(actor, pawn, guest) end
    if operation == "hudhealth" and not hosting then return stock_health(world, pawn) end
    assert(hosting and pawn:HasAuthority() and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1",
        "only the private fixture host may stage a stock attack")
    if operation == "downhost" then return down_host(actor, pawn, guest) end
    if operation == "pistol" or operation == "pistolguest" then
        local remote = operation == "pistolguest"
        assert(not valid(jug) and (not armed or (not remote and guest_fired and npc_damage_calls == 1
            and npc_damage_instigator == name(guest))), "a stock Observatory attack is already staged")
        register_hooks()
        return fire_pistol(world, pawn, guest, actor, remote)
    end
    if operation == "grab" or operation == "grabtoolbox" then
        assert(not valid(jug) and not thrown, "the Observatory prop is already staged")
        register_hooks()
        return prepare(world, pawn, guest, actor, operation == "grabtoolbox")
    end
    if operation == "throw" then
        assert(same(actor, npc) and same(pawn.HeldObject, jug) and not thrown, "no exact Observatory jug is held")
        local eye, center = pawn.Camera:K2_GetComponentLocation(), actor:K2_GetActorLocation()
        local holder = name(jug["Holding Actor"])
        local before_instigator = name(jug:GetInstigator())
        orient(pawn, angle(eye, center))
        armed, thrown, thrower = true, true, pawn
        pawn:Grab_Release_Object(true, false, false)
        assert(not same(pawn.HeldObject, jug) and not same(pawn.PhysicsHandle:GetGrabbedComponent(), jug.PropMesh),
            "stock throw did not release the jug")
        local velocity = jug.PropMesh:GetPhysicsLinearVelocity(FName("None"))
        local speed = math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2)
        return {name(jug), name(actor), holder, before_instigator, name(jug:GetInstigator()), speed}
    end
    error("unknown Observatory jug probe operation", 0)
end

LoopInGameThreadWithDelay(25, function()
    local input = io.open(command, "rb")
    if not input then return end
    local content = input:read("*l") or ""
    input:close()
    local id, operation = content:match("^(%x+)\t([a-z]+)$")
    if not id or #id ~= 32 or id == previous then return end
    previous = id
    local ok, payload = pcall(observe, operation)
    local output = io.open(reply, "wb")
    if output then
        output:write(id, "\t", ok and "ok\t" .. table.concat(payload, "\t") or "error\t" .. tostring(payload), "\n")
        output:close()
    end
end)
