local aim = require("native_flood_jammer_aim_fixture")
local wire = require("native_wire_quest")
local guest_save = require("guest_save")
local guest_npc = require("native_guest_npc")
local fixture = {}

local area = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world_name = "World " .. area .. ".Cistern_P"
local spawner_path = area .. ".Cistern_P:PersistentLevel.Quest_AssassinTargetSpawn_BP_C_0"
local spawner_class = "/Game/Classes/ScriptingActors/Quest_AssassinTargetSpawn_BP.Quest_AssassinTargetSpawn_BP_C"
local target_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Spratt.NPC_Spratt_C"
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local ticket
fixture.map = area

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end

local function stock_target(state, phase, expected)
    if not enabled or phase ~= "hosting" or state.map ~= area or state.listening ~= true or state.server ~= false
        or not valid(state.world) or state.world:GetFullName() ~= world_name then
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
        or not same(host.Save, instance.SaveSlot_Ref) or not valid(target) or target:GetFullName() ~= expected
        or not same(target:GetClass(), StaticFindObject(target_class)) or target.Alive ~= true
        or not same(target:GetWorld(), state.world) or not target:HasAuthority() or target:IsActorBeingDestroyed() then
        error("stock_spratt_aim_target_unqualified", 0)
    end
    local key = FName("Asn_Spratt", EFindName.FNAME_Find)
    local active = wire.stock_row(host.Save.ActiveQuests, key)
    if not active or active[step] ~= 1 then error("stock_spratt_aim_quest_inactive", 0) end
    return host, target
end

local function exact_owner(state, phase, name)
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    local controller = valid(pawn) and pawn.Controller
    local connection = valid(controller) and controller.Player
    if not valid(pawn) or not valid(save) or not valid(connection) or not same(controller.Pawn, pawn) then
        error("stock_spratt_exact_guest_owner_required", 0)
    end
    return pawn, save, controller, connection
end

function fixture.before_guest_fire(state, phase, name, guid)
    local pending = ticket
    if not pending or pending.name ~= name then return end
    ticket = nil
    if not enabled or state.map ~= area or not same(pending.world, state.world) or os.time() >= pending.expires then
        return nil, "stock_spratt_shot_ticket_expired"
    end
    local _, target = stock_target(state, phase, pending.npc)
    local pawn, save, controller, connection = exact_owner(state, phase, name)
    if guid ~= pending.guid or not same(target, pending.target) or not same(pawn, pending.pawn)
        or not same(save, pending.save) or not same(controller, pending.controller)
        or not same(connection, pending.connection) or not same(pawn.EquipedItem, pending.weapon) then
        return nil, "stock_spratt_shot_ticket_identity_changed"
    end
    return aim.aim_target(state, phase, name, guid, "native-spratt", target, target.CapsuleComponent, target.Mesh, true)
end

function fixture.command(fields, state, phase)
    local revive = fields[2] == "native-spratt-revive-approach"
    if not revive and fields[2] ~= "native-spratt-aim" then return end
    if #fields ~= (revive and 4 or 5) then error("stock_spratt_aim_arguments_unqualified", 0) end
    ticket = nil
    local host, target = stock_target(state, phase, fields[revive and 4 or 5])
    local pawn, save, controller, connection = exact_owner(state, phase, fields[3])
    if revive then
        if pawn.Dead ~= false or pawn.IsWeaponBlocked ~= true then error("stock_spratt_exact_downed_guest_required", 0) end
        return guest_npc.approach_revive(host, pawn, target, StaticFindObject("/Script/Engine.Default__KismetSystemLibrary"))
    end
    local pose = aim.aim_target(state, phase, fields[3], fields[4], "native-spratt", target, target.CapsuleComponent, target.Mesh, true)
    pose()
    ticket = {world = state.world, npc = fields[5], target = target, name = fields[3], guid = fields[4], pawn = pawn,
        save = save, controller = controller, connection = connection, weapon = pawn.EquipedItem, expires = os.time() + 4}
    return "verified stock Spratt muzzle lane and armed one scoped fixture shot"
end

return fixture
