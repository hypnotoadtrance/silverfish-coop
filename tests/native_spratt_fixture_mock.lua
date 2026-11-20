package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local now = 100
os.time = function() return now end
os.getenv = function(name)
    if name == "SILVERFISH_COOP_HEADLESS" or name == "SILVERFISH_COOP_NATIVE_FIXTURES" then return "1" end
end

local function object(name, world, class)
    local result = {}
    function result:IsValid() return true end
    function result:GetAddress() return self end
    function result:GetFullName() return name end
    function result:GetClass() return class end
    function result:GetWorld() return world end
    function result:HasAuthority() return true end
    function result:IsActorBeingDestroyed() return false end
    return result
end

local area = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local level = area .. ".Cistern_P:PersistentLevel."
local stock_name = "NPC_Spratt_C " .. level .. "NPC_Spratt_C_42"
local guest_name = "Player_FP_C detached"
local guid = ("A"):rep(32)
local world = object("World " .. area .. ".Cistern_P")
local stock_class = object("class")
local target = object(stock_name, world, stock_class)
target.Alive = true
target.CapsuleComponent, target.Mesh = object("capsule"), object("mesh")
local hosted = object("host", world)
local guest = object(guest_name, world)
guest.Dead, guest.IsWeaponBlocked = false, false
guest.Save, guest.EquipedItem = object("detached save"), object("weapon")
guest.Controller = {IsValid = function() return true end, GetAddress = function(self) return self end,
    Player = object("net connection"), Pawn = guest}
hosted.Save = object("canonical save")
local active = {Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A = 1}
hosted.Save.ActiveQuests = active

local inst = object("game instance")
inst["As Player FP"], inst.SaveSlot_Ref = hosted, hosted.Save
local spawner_path = level .. "Quest_AssassinTargetSpawn_BP_C_0"
local spawner_class = object("spawner class")
local spawner = object("Quest_AssassinTargetSpawn_BP_C " .. spawner_path, world, spawner_class)
spawner.TargetNPC, spawner["As Game Instance FP"] = target, inst
local trace = object("trace")
local objects = {
    [spawner_path] = spawner,
    ["/Game/Classes/ScriptingActors/Quest_AssassinTargetSpawn_BP.Quest_AssassinTargetSpawn_BP_C"] = spawner_class,
    ["/Game/Classes/NPCs/NPC_Characters/NPC_Spratt.NPC_Spratt_C"] = stock_class,
    ["/Script/Engine.Default__KismetSystemLibrary"] = trace,
}
StaticFindObject = function(path) return objects[path] end
EFindName = {FNAME_Find = 0}
FName = function(value) return value end

local poses, restores, revives = 0, 0, 0
package.loaded.native_flood_jammer_aim_fixture = {
    aim_target = function(state, phase, pawn, item, tag, npc, capsule, mesh, temporary)
        assert(state.world == world and phase == "hosting" and pawn == guest_name and item == guid)
        assert(tag == "native-spratt" and npc == target and npc.CapsuleComponent == capsule and npc.Mesh == mesh and temporary)
        poses = poses + 1
        return function() restores = restores + 1 end
    end,
}
package.loaded.native_wire_quest = {
    stock_row = function(quests, key)
        assert(quests == active and key == "Asn_Spratt")
        return active
    end,
}
package.loaded.guest_save = {
    exact_item_context = function(state, phase, pawn)
        assert(state.world == world and phase == "hosting" and pawn == guest_name)
        return guest, guest.Save
    end,
}
package.loaded.native_guest_npc = {
    approach_revive = function(host, downed, enemy, geometry)
        assert(host == hosted and downed == guest and enemy == target and geometry == trace)
        revives = revives + 1
        return "stock revive approach"
    end,
}

local fixture = require("native_spratt_fixture")
local state = {map = area, world = world, listening = true, server = false, local_pawn = {pawn = hosted}}
local arm = {"nonce", "native-spratt-aim", guest_name, guid, stock_name}
assert(fixture.before_guest_fire(state, "hosting", guest_name, guid) == nil)
fixture.command(arm, state, "hosting")
assert(poses == 1 and restores == 1)
assert(fixture.before_guest_fire(state, "hosting", "sibling", guid) == nil)
local restore, error = fixture.before_guest_fire(state, "hosting", guest_name, guid)
assert(type(restore) == "function" and not error and poses == 2 and restores == 1)
restore()
assert(restores == 2 and fixture.before_guest_fire(state, "hosting", guest_name, guid) == nil)

fixture.command(arm, state, "hosting")
guest.Controller.Player = object("different connection")
restore, error = fixture.before_guest_fire(state, "hosting", guest_name, guid)
assert(not restore and error == "stock_spratt_shot_ticket_identity_changed" and poses == 3)
fixture.command(arm, state, "hosting")
now = now + 4
restore, error = fixture.before_guest_fire(state, "hosting", guest_name, guid)
assert(not restore and error == "stock_spratt_shot_ticket_expired" and poses == 4)

local revive = {"nonce", "native-spratt-revive-approach", guest_name, stock_name}
assert(not pcall(fixture.command, revive, state, "hosting"))
guest.IsWeaponBlocked = true
assert(fixture.command(revive, state, "hosting") == "stock revive approach" and revives == 1)
