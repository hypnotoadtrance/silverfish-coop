local aim = require("native_flood_jammer_aim_fixture")
local wire = require("native_wire_quest")
local fixture = {}

local area = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world_name = "World " .. area .. ".Cistern_P"
local spawner_path = area .. ".Cistern_P:PersistentLevel.Quest_AssassinTargetSpawn_BP_C_0"
local spawner_class = "/Game/Classes/ScriptingActors/Quest_AssassinTargetSpawn_BP.Quest_AssassinTargetSpawn_BP_C"
local target_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Spratt.NPC_Spratt_C"
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end

function fixture.command(fields, state, phase)
    if fields[2] ~= "native-spratt-aim" then return end
    if not enabled or phase ~= "hosting" or state.map ~= area or state.listening ~= true or state.server ~= false
        or not valid(state.world) or state.world:GetFullName() ~= world_name or #fields ~= 5 then
        error("stock_spratt_aim_scope_unqualified", 0)
    end
    local spawner = StaticFindObject(spawner_path)
    if not valid(spawner) or spawner:GetFullName() ~= "Quest_AssassinTargetSpawn_BP_C " .. spawner_path
        or not same(spawner:GetClass(), StaticFindObject(spawner_class)) or not spawner:HasAuthority()
        or spawner:IsActorBeingDestroyed() or not same(spawner:GetWorld(), state.world) then
        error("stock_spratt_aim_spawner_unqualified", 0)
    end
    local host = state.local_pawn and state.local_pawn.pawn
    local instance, target = spawner["As Game Instance FP"], spawner.TargetNPC
    if not valid(host) or not valid(instance) or not same(instance["As Player FP"], host)
        or not same(host.Save, instance.SaveSlot_Ref) or not valid(target) or target:GetFullName() ~= fields[5]
        or not same(target:GetClass(), StaticFindObject(target_class)) or target.Alive ~= true then
        error("stock_spratt_aim_target_unqualified", 0)
    end
    local key = FName("Asn_Spratt", EFindName.FNAME_Find)
    local active = wire.stock_row(host.Save.ActiveQuests, key)
    if not active or active[step] ~= 1 then error("stock_spratt_aim_quest_inactive", 0) end
    return aim.aim_target(state, phase, fields[3], fields[4], "native-spratt", target, target.CapsuleComponent, target.Mesh)
end

return fixture
