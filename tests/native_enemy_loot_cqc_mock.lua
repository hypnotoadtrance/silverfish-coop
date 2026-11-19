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
ammo.Ammo, ammo.bHidden = 8, false
ammo.GetWorld, ammo.GetClass = host.GetWorld, function() return shell_class end
ammo.HasAuthority, ammo.IsActorBeingDestroyed = function() return not destroyed end, function() return destroyed end
ammo.K2_GetActorLocation = function() return {X = 1, Y = 2, Z = 3} end
ammo.SetActorEnableCollision, ammo.GetActorEnableCollision = function(_, value) collision = value end, function() return collision end
ammo.SetActorHiddenInGame = function(self, value) self.bHidden = value end
ammo.K2_DestroyActor = function() destroyed = true end
local slot, guid = "SFCoop_" .. ("b"):rep(32), ("B"):rep(32)
local save_class_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local save_class, pool, reopened = object(11, save_class_path), object(12, "pool"), object(13, "reopened")
local api = object(14, "gameplay statics")
api.DoesSaveGameExist = function(_, name) assert(name == slot); return false end
api.CreateSaveGameObject = function(_, class) assert(class == save_class); return pool end
api.SaveGameToSlot = function(_, target, name) assert(target == pool and name == slot); return true end
api.LoadGameFromSlot = function(_, name) assert(name == slot); return reopened end
local objects = {[npc_class_path] = npc_class, [npc_path] = npc, [second_path] = second, [ammo_path] = ammo,
    [shell_path] = shell_class, [save_class_path] = save_class, ["/Script/Engine.Default__GameplayStatics"] = api,
    [player_class_path] = player_class, [ai_class_path] = ai_class}
StaticFindObject = function(path) return objects[path] end

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
assert(enemy_loot.command({"request", "native-enemy-loot-bank", npc:GetFullName(), slot, ammo:GetFullName()}, state, "hosting"))
assert(not ammo.bHidden and collision and not destroyed and enemy_loot.departure_pending(state, "hosting"))
assert(not pcall(enemy_loot.command, {"request", "native-enemy-loot-retire", ammo:GetFullName(), slot, ("C"):rep(32)}, state, "hosting"))
assert(not destroyed)
assert(enemy_loot.command({"request", "native-enemy-loot-retire", ammo:GetFullName(), slot, guid}, state, "hosting"))
assert(ammo.bHidden and not collision and destroyed and not enemy_loot.departure_pending(state, "hosting"))
local rows = table.concat(enemy_loot.rows(state, "hosting", row), "\n")
assert(rows:find(row("native-enemy-loot-cqc", root, npc:GetFullName(), shooter_id, 0, 1), 1, true))
assert(rows:find(row("native-enemy-loot-cqc", root, second:GetFullName(), "-", 0, 0), 1, true))
assert(rows:find(row("native-enemy-loot-escrow", root, "retired", "shooter", ammo:GetFullName(), slot, guid, 8, 1, 2, 3), 1, true))
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
