local drill = {}
local area = "/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P"
local world_name = "World " .. area .. ".Sci_Tunnel_01_P"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local objective = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
drill.quest, drill.flag = "Clt_DrillHead", "Topic_MicroDrill"
drill.profile = {
    map = area,
    path = area .. ".Sci_Tunnel_01_P:PersistentLevel.Art_DrillHead_C_1",
    class = "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C",
    name = "Art_DrillHead_C",
    source = "scientist-tunnel-drill-head",
    authority_sight = true,
    quest_source = true,
    approach = {},
}

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end

function drill.read(state, phase, active_required)
    local detail, controller = state.local_pawn, state.controller
    local host = detail and detail.pawn
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if phase ~= "hosting" or state.map ~= area or state.listening ~= true or state.server ~= false
        or not valid(state.world) or state.world:GetFullName() ~= world_name
        or not exact(host, pawn_class) or not exact(controller, controller_class) or not valid(statics)
        or not host:HasAuthority() or not controller:HasAuthority() or not controller:IsLocalController()
        or not same(host:GetWorld(), state.world) or not same(controller.Pawn, host) then return end
    local instance = statics:GetGameInstance(host)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_class) or not exact(save, save_class)
        or not same(host.Save, save) or not same(instance["As Player FP"], host) then return end

    -- The stock destruction handler overwrites this flag only if its old value is lower.
    -- A live value of at least one makes quiet retirement equivalent for the active contract.
    local wire = require("native_wire_quest")
    local topic, found = wire.stock_flag(instance, drill.flag)
    if found ~= true or math.type(topic) ~= "integer" or topic < 1 or topic > 0x7fffffff then return end
    if active_required then
        local key = FName(drill.quest, EFindName.FNAME_Find)
        if key:GetComparisonIndex() == 0 or key:ToString() ~= drill.quest then return end
        local row = wire.stock_row(save.ActiveQuests, key)
        local items = row and row[objective]
        if not row or row[step_field] ~= 1 or not items or items:GetArrayNum() ~= 1
            or not same(items[1], StaticFindObject(drill.profile.class)) then return end
    end
    return {save = save, instance = instance, topic = topic}
end

function drill.unchanged(before, after)
    return after and same(before.save, after.save) and same(before.instance, after.instance) and after.topic == before.topic
end

return drill
