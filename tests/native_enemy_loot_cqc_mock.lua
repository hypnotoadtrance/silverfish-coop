-- Run with Lua 5.4 from the repository root.
local root = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local headless_fixture = false
local function flag(name)
    local enabled = { NATIVE_ZONE = true, NATIVE_GUEST_SAVE = true, NATIVE_LOOT = true, NATIVE_CQC_LOOT = true }
    if headless_fixture then
        enabled.HEADLESS, enabled.NATIVE_FIXTURES, enabled.NATIVE_NPC_TEST, enabled.NATIVE_ENEMY_LOOT = true, true, true, true
    end
    return enabled[name:match("^SILVERFISH_COOP_(.*)$")] and "1" or nil
end
os.getenv = flag

local function object(address, name)
    return {GetAddress = function() return address end, GetFullName = function() return name end,
        IsValid = function() return true end}
end
local world = object(1, "World " .. root .. ".DeadBridge_P")
local host = object(2, "host")
host.GetWorld = function() return world end
local scout, shooter = object(3, "scout"), object(4, "shooter")
local scout_save, shooter_save = object(5, "scout save"), object(6, "shooter save")
scout.Dead, scout.IsWeaponBlocked, scout_save.PlayerHealth = false, false, 250
local scout_id = "aaaaaaaa-0000-0000-0000-000000000001"
local shooter_id = "aaaaaaaa-0000-0000-0000-000000000002"
package.loaded.guest_save = {
    exact_item_context = function(state, _, name)
        for _, detail in ipairs(state.guests) do
            if detail.name == name and name == "scout" then return scout, scout_save, scout_id end
            if detail.name == name and name == "shooter" then return shooter, shooter_save, shooter_id end
        end
    end,
}
local fresh = true
package.loaded.native_transport = {scoped_host = function() return {pawn = host}, fresh end}
package.loaded.native_enemy_drop_motion = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_drop_motion.lua"))()
package.loaded.native_enemy_loot_registry = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_loot_registry.lua"))()
package.loaded.native_enemy_shell_view = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_shell_view.lua"))()
local npc_class_path = "/Game/Classes/NPCs/NPC_State_Infantry_CQC.NPC_State_Infantry_CQC_C"
local npc_path = root .. ".DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"
local npc_class, npc = object(7, npc_class_path), object(8, "NPC_State_Infantry_CQC_C " .. npc_path)
npc.GetWorld, npc.GetClass, npc.IsActorBeingDestroyed = host.GetWorld, function() return npc_class end, function() return false end
npc.HasAuthority = function() return true end
npc.Alive, npc.Initialized = true, true
local player_class_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local ai_class_path = "/Game/Classes/NPCs/NPC_Assets/MasterNPC_AI_Controler_BP.MasterNPC_AI_Controler_BP_C"
local player_class, ai_class = object(16, player_class_path), object(17, ai_class_path)
local ai = object(18, "stock CQC AI")
ai.GetWorld, ai.HasAuthority = npc.GetWorld, npc.HasAuthority
ai.Pawn, ai.GetClass = npc, function() return ai_class end
shooter.GetWorld, shooter.HasAuthority, shooter.GetClass = npc.GetWorld, npc.HasAuthority, function() return player_class end
npc.Controller, npc.CombatTarget = ai, shooter
local second_path = npc_path:gsub("_0$", "_1")
local second = object(15, "NPC_State_Infantry_CQC_C " .. second_path)
second.GetWorld, second.GetClass, second.IsActorBeingDestroyed = npc.GetWorld, npc.GetClass, npc.IsActorBeingDestroyed
second.HasAuthority, second.Alive, second.Initialized = npc.HasAuthority, true, true
local ammo_path = root .. ".DeadBridge_P:PersistentLevel.Ammo_ShotgunShells_BP_C_123"
local shell_path = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"
local shell_class, ammo = object(9, shell_path), object(10, "Ammo_ShotgunShells_BP_C " .. ammo_path)
local destroyed, collision = false, true
local retire_pending = true
ammo.Ammo, ammo.bHidden = 8, false
ammo.GetWorld, ammo.GetClass = host.GetWorld, function() return shell_class end
ammo.HasAuthority, ammo.IsActorBeingDestroyed = function() return not destroyed end, function() return destroyed end
ammo.K2_GetActorLocation = function() return {X = 1, Y = 2, Z = 3} end
ammo.K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end
ammo.SetActorEnableCollision, ammo.GetActorEnableCollision = function(_, value) collision = value end, function() return collision end
ammo.SetActorHiddenInGame = function(self, value) self.bHidden = value end
ammo.K2_DestroyActor = function() destroyed = true end
local slot, guid = "SFCoop_" .. ("b"):rep(32), ("B"):rep(32)
local save_class_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local save_class, pool, reopened = object(11, save_class_path), object(12, "pool"), object(13, "reopened")
local api = object(14, "gameplay statics")
local now = 1
api.GetTimeSeconds = function() return now end
api.DoesSaveGameExist = function(_, name) assert(name == slot); return false end
api.CreateSaveGameObject = function(_, class) assert(class == save_class); return pool end
api.SaveGameToSlot = function(_, target, name) assert(target == pool and name == slot); return true end
api.LoadGameFromSlot = function(_, name) assert(name == slot); return reopened end
local objects = {[npc_class_path] = npc_class, [npc_path] = npc, [second_path] = second, [ammo_path] = ammo,
    [shell_path] = shell_class, [save_class_path] = save_class, ["/Script/Engine.Default__GameplayStatics"] = api,
    [player_class_path] = player_class, [ai_class_path] = ai_class}
StaticFindObject = function(path) return objects[path] end
SilverfishIsNativeCqcShellRetired = function(address, path)
    return address == world:GetAddress() and path == ammo_path and destroyed and not retire_pending
end

local ready, reserved, captured = false, false, false
local latest_roster = {}
SilverfishSetNativeCqcEnemyLootScope = function(home, enemy, ...)
    assert(home == host:GetAddress() and (enemy == npc:GetAddress() or enemy == second:GetAddress()))
    latest_roster[enemy] = {...}
    return true
end
SilverfishReadNativeEnemyLoot = function(owner, enemy)
    assert(owner == host:GetAddress())
    if enemy == second:GetAddress() then
        return "watching", 0, "-", "-", "-", 0, 0, 0, 0, 0, false, 0, "-", "-", 0,
            "pending", 0, false, 0, 0, 0, 0, false, "-", false, false
    end
    assert(enemy == npc:GetAddress())
    return ready and "stock_loot_observed" or "watching", 3, "guest", "shooter", "stock projectile",
        20, 10, -10, 1, 2, true, 1, ammo:GetFullName(), shell_path, 8,
        "guest_detached", 2, false, 0, 1, 0, 0, true, shooter_id, reserved, captured
end
SilverfishNativeCqcEnemyLootAvailable = function() return 2 end
SilverfishNativeCqcEnemyLootPending = function() return reserved end
SilverfishCaptureNativeEnemyLootEscrow = function(enemy, target, raw, extra)
    assert(enemy == npc:GetAddress() and target == pool:GetAddress() and raw == ammo:GetAddress() and extra == 0)
    assert(reserved and not captured)
    captured = true
    return true, "captured", 1, guid
end
SilverfishClaimNativeEnemyLootEscrow = function() error("claim was not requested") end
SilverfishVerifyNativeLootEscrow = function(initial, persisted)
    return initial == pool:GetAddress() and persisted == reopened:GetAddress()
end
SilverfishFinishNativeCqcEnemyLoot = function(home, raw)
    assert(home == host:GetAddress() and raw == ammo:GetAddress() and reserved and captured and destroyed)
    if retire_pending then
        retire_pending = false
        return false, "native_enemy_loot_raw_retirement_pending"
    end
    reserved = false
    return true, "retired"
end

local enemy_loot = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_loot.lua"))()
local state = {map = root, world = world, server = false, listening = true,
    guests = {{name = "scout", pawn = scout}, {name = "shooter", pawn = shooter}}}
local row = function(...) return table.concat({...}, "\t") end
enemy_loot.sync(state, "hosting")
assert(table.concat(latest_roster[npc:GetAddress()], ",") == table.concat({3, 5, scout_id, 4, 6, shooter_id}, ","))
assert(table.concat(latest_roster[second:GetAddress()], ",") == table.concat({3, 5, scout_id, 4, 6, shooter_id}, ","))
local current_target = row("native-enemy-loot-cqc-target", root, npc:GetFullName(), shooter:GetFullName(), shooter_id, ai:GetFullName())
assert(table.concat(enemy_loot.rows(state, "hosting", row), "\n"):find(current_target, 1, true))
assert(not enemy_loot.departure_pending(state, "hosting"))
state.guests[2], ready, reserved = nil, true, true
enemy_loot.sync(state, "hosting")
assert(table.concat(latest_roster[npc:GetAddress()], ",") == table.concat({3, 5, scout_id}, ","))
assert(table.concat(latest_roster[second:GetAddress()], ",") == table.concat({3, 5, scout_id}, ","))
assert(not table.concat(enemy_loot.rows(state, "hosting", row), "\n"):find(current_target, 1, true))
enemy_loot.clear_fixture()
assert(enemy_loot.departure_pending(state, "hosting"))
fresh = false
local request = {"request", "native-enemy-loot-bank", npc:GetFullName(), slot, ammo:GetFullName()}
local banked, reason = pcall(enemy_loot.command, request, state, "hosting")
assert(not banked and reason == "native_enemy_loot_drop_settling" and not captured and not destroyed)
now = 1.25
assert(enemy_loot.command(request, state, "hosting"))
assert(not ammo.bHidden and collision and not destroyed and enemy_loot.departure_pending(state, "hosting"))
assert(not pcall(enemy_loot.command, {"request", "native-enemy-loot-retire", ammo:GetFullName(), slot, ("C"):rep(32)}, state, "hosting"))
assert(not destroyed)
local ready_to_commit, wait_reason = pcall(enemy_loot.command,
    {"request", "native-enemy-loot-retire", ammo:GetFullName(), slot, guid}, state, "hosting")
assert(not ready_to_commit and wait_reason == "native_enemy_loot_raw_retirement_pending" and destroyed and reserved)
assert(enemy_loot.command({"request", "native-enemy-loot-retire", ammo:GetFullName(), slot, guid}, state, "hosting"))
assert(ammo.bHidden and not collision and destroyed and not enemy_loot.departure_pending(state, "hosting"))
local rows = table.concat(enemy_loot.rows(state, "hosting", row), "\n")
assert(rows:find(row("native-enemy-loot-cqc", root, npc:GetFullName(), shooter_id, 0, 1), 1, true))
assert(rows:find(row("native-enemy-loot-cqc", root, second:GetFullName(), "-", 0, 0), 1, true))
assert(rows:find(row("native-enemy-loot-escrow", root, "retired", "shooter", ammo:GetFullName(), slot, guid, 8, 1, 2, 3), 1, true))
assert(rows:find(row("native-enemy-loot-retire-proof", 1, root, ammo:GetFullName(), slot, guid), 1, true))
print("Ordinary Dead Bridge loot: detached roster, reserved escrow, exact retirement and source readback passed")

package.loaded.native_enemy_loot = enemy_loot
package.loaded.native_guest_weapon = {}
package.loaded.guest_weapon_registry = assert(loadfile("mod/SilverfishCoop/Scripts/guest_weapon_registry.lua"))()
local npc_script = "mod/SilverfishCoop/Scripts/native_guest_npc.lua"
local shot = {"request", "native-bandit-shot-fixture", npc:GetFullName(), scout:GetFullName(), guid}
local function refused(adapter, fields, expected)
    local ok, error = pcall(adapter.command, fields, state, "hosting")
    assert(not ok and error == expected, tostring(error))
end
refused(assert(loadfile(npc_script))(), shot, "native_bandit_shot_watch_required")

headless_fixture, fresh, ready, captured = true, true, false, false
local automatic = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_loot.lua"))()
package.loaded.native_enemy_loot = automatic
local fixture = assert(loadfile(npc_script))()
automatic.sync(state, "hosting")
assert(automatic.rows(state, "hosting", table.pack)[1][1] == "native-enemy-loot-cqc-capability")

local inspect_count = 0
SilverfishInspectNativeGuestWeapon = function(pawn, saved, selected)
    assert(pawn == scout:GetAddress() and saved == scout_save:GetAddress() and selected == guid)
    inspect_count = inspect_count + 1
    return false, "not equipped"
end
SilverfishFireNativeGuestWeapon = function() error("Unqualified fixture reached stock fire") end
refused(fixture, shot, "native_bandit_shot_equipped_guid_required")
assert(inspect_count == 1)
local other_stock = {"request", "native-bandit-shot-fixture", second:GetFullName(), scout:GetFullName(), guid}
refused(fixture, other_stock, "native_bandit_shot_equipped_guid_required")
local sibling = {"request", "native-bandit-shot-fixture", npc:GetFullName():gsub("_0$", "_2"), scout:GetFullName(), guid}
refused(fixture, sibling, "native_bandit_shot_watch_required")
local other_guest = {"request", "native-bandit-shot-fixture", npc:GetFullName(), shooter:GetFullName(), guid}
refused(fixture, other_guest, "native_bandit_shot_exact_guest_and_weapon_required")
ready = true
refused(fixture, shot, "native_bandit_shot_watch_required")
assert(inspect_count == 2)
print("Dead Bridge stock shot fixture reads the automatic watcher and requires the exact live actor and guest")

ready = false
local path = "BlueprintGeneratedClass " .. package.loaded.guest_weapon_registry.paths["sg-71-a"]
local held, kind = object(22, "scout holds shotgun"), object(23, path)
held.GetClass, held.HasAuthority = function() return kind end, function() return false end
scout.EquipedItem = held
SilverfishInspectNativeGuestWeapon = function() return true, "equipped", 1, 1 end
refused(fixture, shot, "The verified standing guest, stock equipped weapon or living Bandit is unavailable")
scout.EquipedItem = nil
refused(fixture, shot, "guest_weapon_preloaded_magazine_required")
print("The exact CQC fixture admits one stock SG-71 A round and retains the older light pistol requirement")

local recover = {"request", "native-cqc-revive-approach", second:GetFullName(), scout:GetFullName()}
state.local_pawn = {pawn = host, name = host:GetFullName()}
scout.GetWorld = host.GetWorld
refused(fixture, recover, "native_cqc_revive_exact_downed_guest_required")

scout.IsWeaponBlocked, scout_save.PlayerHealth = true, 1
host.Dead, host.IsWeaponBlocked = false, false
local capsule, capsule_type = object(19, "host capsule"), object(20, "Class /Script/Engine.CapsuleComponent")
capsule.GetClass, capsule.GetOwner = function() return capsule_type end, function() return host end
host.CapsuleComponent = capsule
host.K2_GetActorLocation = function() return {X = 0, Y = 0, Z = 90} end
host.K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end
scout.K2_GetActorLocation = function() return {X = 130, Y = 0, Z = 90} end
objects[second_path] = nil
objects["/Script/Engine.Default__KismetSystemLibrary"] = object(21, "stock trace")
SilverfishReadNativeEnemyLoot = function() error("Revive placement must not depend on combat watch") end
assert(fixture.command(recover, state, "hosting") == "stock Bandit revive approach verified")
recover[4] = shooter:GetFullName()
refused(fixture, recover, "native_cqc_revive_exact_downed_guest_required")
assert(scout_save.PlayerHealth == 1 and scout.IsWeaponBlocked)
print("Exact downed guest's stock revive approach is independent of either CQC watcher")

local refinery = "/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P"
local profile = package.loaded.native_enemy_loot_registry[refinery]
local refinery_world = object(30, "World " .. profile.world)
local refinery_actors, sent = {}, {}
for index, name in ipairs(profile.names) do
    local actor = object(30 + index, name)
    actor.GetWorld, actor.GetClass = function() return refinery_world end, npc.GetClass
    actor.IsActorBeingDestroyed, actor.HasAuthority = npc.IsActorBeingDestroyed, npc.HasAuthority
    actor.Alive, actor.Initialized = true, true
    objects[profile.paths[name]], refinery_actors[actor:GetAddress()] = actor, name
end
state.world, state.map = refinery_world, refinery
host.GetWorld = function() return refinery_world end
scout.GetWorld = host.GetWorld
SilverfishSetNativeCqcEnemyLootScope = function(local_address, actor, ...)
    assert(local_address == host:GetAddress() and refinery_actors[actor])
    sent[actor] = {...}
    return true
end
SilverfishReadNativeEnemyLoot = function(owner, actor)
    assert(owner == host:GetAddress() and refinery_actors[actor])
    return "watching", 0, "-", "-", "-", 0, 0, 0, 0, 0, false, 0, "-", "-", 0,
        "pending", 0, false, 0, 0, 0, 0, false, "-", false, false
end
automatic.sync(state, "hosting")
assert(not next(sent))
SilverfishNativeCqcEnemyLootAvailable = function() return 3 end
automatic.sync(state, "hosting")
assert(sent[31][1] == scout:GetAddress() and sent[32][1] == scout:GetAddress())
local qualified = table.concat(automatic.rows(state, "hosting", row), "\n")
assert(qualified:find(row("native-enemy-loot", refinery, profile.names[1], "watching"), 1, true))
assert(qualified:find(row("native-enemy-loot", refinery, profile.names[2], "watching"), 1, true))
assert(not qualified:find(npc:GetFullName(), 1, true))
print("Refinery uses native profile capability 3 and watches only its two authenticated placed encounters")

local start, target = {X = 1000, Y = 0, Z = 90}, {X = 0, Y = 0, Z = 90}
local location, angle = start, {Pitch = 0, Yaw = 0, Roll = 0}
local moves, shots, solid_entrance = 0, 0, false
scout.IsWeaponBlocked, scout_save.PlayerHealth = false, 250
scout.K2_GetActorLocation = function() return location end
scout.K2_GetActorRotation = function() return angle end
scout.K2_TeleportTo = function(_, point) location = point; moves = moves + 1; return true end
scout.ForceNetUpdate = function() end
local controller = object(33, "scout controller")
controller.GetControlRotation = function() return angle end
controller.SetControlRotation = function() end
state.guests[1].controller_object = controller

local function component(id, class, owner, at)
    local result, type_object = object(id, class), object(id + 100, "Class /Script/Engine." .. class)
    result.GetOwner, result.GetClass = function() return owner end, function() return type_object end
    result.K2_GetComponentLocation = at
    result.RelativeRotation = angle
    result.K2_SetRelativeRotation = function(self, orientation) self.RelativeRotation = orientation end
    result.K2_SetWorldRotation = result.K2_SetRelativeRotation
    result.K2_GetComponentRotation = function(self) return self.RelativeRotation end
    result.GetForwardVector = function(self)
        local pitch, yaw = math.rad(self.RelativeRotation.Pitch), math.rad(self.RelativeRotation.Yaw)
        return {X = math.cos(yaw)*math.cos(pitch), Y = math.sin(yaw)*math.cos(pitch), Z = math.sin(pitch)}
    end
    return result
end

local weapon = object(34, "stock SG-71 A")
weapon.HasAuthority, weapon.GetWorld = npc.HasAuthority, host.GetWorld
weapon.GetClass = function() return kind end
weapon.GetOwner = function() return scout end
weapon.GetInstigator = weapon.GetOwner
weapon.User, weapon.Player_Ref, weapon.HeldByPlayer = scout, scout, true
weapon.Arrow_Muzzle = component(35, "ArrowComponent", weapon, scout.K2_GetActorLocation)
scout.EquipedItem = weapon
scout.Camera = component(36, "CameraComponent", scout, scout.K2_GetActorLocation)
scout.CapsuleComponent = component(37, "CapsuleComponent", scout, scout.K2_GetActorLocation)
scout.CapsuleComponent.GetScaledCapsuleHalfHeight = function() return 88 end
local target_name = profile.names[1]
local enemy = objects[profile.paths[target_name]]
enemy.CapsuleComponent = component(38, "CapsuleComponent", enemy, function() return target end)
local contact = component(39, "SkeletalMeshComponent", enemy, function() return target end)
objects["/Script/Engine.Default__KismetSystemLibrary"].LineTraceSingle = function(_, _, from, _, channel, _, _, _, hit)
    if channel == 0 then
        if from.X == start.X and not solid_entrance then return false end
        hit.ImpactPoint, hit.ImpactNormal = {X = from.X, Y = from.Y, Z = 0}, {X = 0, Y = 0, Z = 1}
    else
        assert(channel == 2 and location.X ~= start.X)
        hit.Component = {Get = function() return contact end}
    end
    return true
end
local guest_weapon = package.loaded.native_guest_weapon
guest_weapon.command = function(fields)
    assert(fields[2] == "native-guest-weapon-fire" and fields[3] == "scout" and fields[4] == guid)
    assert(location.X ~= start.X and location.X <= 360)
    shots = shots + 1
end
guest_weapon.rows = function()
    return {{"native-guest-weapon", refinery, "fire", "scout", guid, 1, "equipped", 7, 6, 6, 6, 9}}
end
local refinery_shot = {"request", "native-bandit-shot-fixture", target_name, "scout", guid}
refused(fixture, refinery_shot, "native_bandit_shot_refinery_return_floor_unavailable")
assert(moves == 0 and shots == 0)
solid_entrance = true
assert(fixture.command(refinery_shot, state, "hosting") == "stock Bandit aim and fire verified")
assert(moves == 2 and shots == 1 and location == start and scout_save.PlayerHealth == 250)
local aim = fixture.rows(state, "hosting", table.pack)[1]
assert(aim[2] == refinery and aim[3] == target_name and aim[9] >= 640 and aim[10] == 0)
print("Refinery headless shot fires one stock pulse from a clear lane, then restores the exact floor-checked starting pose")

local positions, rays, blocked = {}, 0, false
scout.InteractionRange = 250
objects["/Script/Engine.CapsuleComponent"] = scout.CapsuleComponent:GetClass()
objects["/Script/Engine.CameraComponent"] = scout.Camera:GetClass()
objects["/Script/Engine.Default__KismetSystemLibrary"].LineTraceSingle = function(_, _, from, to, channel, _, _, _, hit)
    assert(channel == 0)
    if from.X == to.X and from.Y == to.Y then
        hit.ImpactPoint, hit.ImpactNormal = {X = from.X, Y = from.Y, Z = 0}, {X = 0, Y = 0, Z = 1}
        return true
    end
    assert(to.X == 0 and to.Y == 0 and to.Z == 48)
    positions[#positions + 1], rays = location, rays + 1
    return blocked or rays == 1
end
local shell_approach = {"request", "native-enemy-loot-approach", "scout", guid, "0", "0", "8"}
assert(automatic.command(shell_approach, state, "hosting") == "stock enemy loot approach verified")
assert(rays == 2 and positions[1].X == 135 and positions[1].Y == 0 and location == positions[2])
assert(location.X < positions[1].X and location.Y > 0 and location.Z == 90)
location, blocked = start, true
refused(automatic, shell_approach, "native_enemy_loot_fixture_no_floor_with_shell_line")
assert(location == start and rays > 2)
print("Refinery headless shell approach selects a clear alternate floor and restores the original if every ray is blocked")
