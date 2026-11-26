local camera = {}
local footsteps = require("native_footsteps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_HOST_CAMERA_PRIMARY_INPUT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local quest = "Photo_DB_Turrets"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final_field = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local item_class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local event_path, is_downed, binding, fixture, equipped_fixture, pending

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function active_quest(save)
    local name = FName(quest, EFindName.FNAME_Find)
    local rows = save.ActiveQuests
    if name:GetComparisonIndex() == 0 or name:ToString() ~= quest or not rows or #rows > 4096
        or rows:Contains(name) ~= true then return false end
    local row = rows:Find(name):get()
    local step = row and row[step_field]
    return (step == 1 or step == 2) and row[final_field] == 3
end

local function equipped_guid(held, save)
    local rows, selected = save.PlayerEquipment, math.tointeger(save.PlayerEquipIndex)
    local count = rows and math.tointeger(rows:GetArrayNum())
    if not selected or not count or selected < 0 or selected >= count or count > 4096 then return end
    local row = rows[selected + 1]
    local identity = row and row[guid_field]
    if not identity or not same(row[item_class_field], held:GetClass()) then return end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(identity[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return end
        words[#words + 1] = word & 0xffffffff
    end
    local guid = string.format("%08X%08X%08X%08X", table.unpack(words))
    return guid:find("[^0]") and guid or nil
end

local function inspect(state, phase, preparing)
    if not enabled or (embedded and type(SilverfishEnqueueLocalBridgeInput) ~= "function")
        or (not embedded and not event_path) or phase ~= "hosting" or state.listening ~= true or state.server ~= false
        or state.map ~= map or not valid(state.world) or state.world:GetFullName() ~= "World " .. map .. ".DeadBridge_P"
        or type(is_downed) ~= "function" then return end
    local detail, controller, world = state.local_pawn, state.controller, state.world
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_path) or not exact(controller, controller_path)
        or not same(detail.controller_object, controller) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed() or not pawn:HasAuthority()
        or not controller:HasAuthority() or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or pawn.Dead ~= false or pawn.IsWeaponBlocked ~= false or controller.Loading ~= false or is_downed(pawn) ~= false
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_path) or not exact(stock, save_path) or not same(instance["As Player FP"], pawn)
        or not same(pawn.Save, stock) or not active_quest(stock) then return end
    local current = {world = world, name = detail.name, pawn = pawn, controller = controller}
    if preparing then return current end
    local held = pawn.EquipedItem
    if not exact(held, camera_path) or held:IsActorBeingDestroyed() or not held:HasAuthority()
        or not same(held:GetWorld(), world) or not same(held:GetOwner(), pawn) or not same(held.Instigator, pawn)
        or not same(held.User, pawn) or held.HeldByPlayer ~= true or valid(pawn.HeldObject) then return end
    local guid = equipped_guid(held, stock)
    if not guid then return end
    current.camera, current.guid = held, guid
    return current
end

local function context(state, phase, preparing)
    local checked, current = pcall(inspect, state, phase, preparing)
    if checked then return current end
end

local function same_context(current, old)
    return current and old and current.name == old.name and current.guid == old.guid
        and same(current.world, old.world) and same(current.controller, old.controller)
        and same(current.pawn, old.pawn) and same(current.camera, old.camera)
end

local function sequence_valid(sequence)
    return math.type(sequence) == "integer" and sequence > 0 and sequence < 2 ^ 53
end

function camera.reset()
    if binding and type(SilverfishClearNativeHostCameraScope) == "function" then
        SilverfishClearNativeHostCameraScope()
    end
    binding, fixture, equipped_fixture, pending = nil, nil, nil, nil
end

local function publish(record)
    if embedded then return SilverfishEnqueueLocalBridgeInput(record) == true end
    local file = io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(record)
    local closed = file:close()
    return written and closed
end

local function drain(current)
    if not same_context(current, binding) or os.time() >= binding.expires then
        camera.reset()
        return
    end
    local address, emitted = current.pawn:GetAddress(), 0
    for _ = 1, 8 do
        if not pending then
            local sequence, owner, guid, kind = SilverfishTakeNativeHostCameraIntent(address)
            if not sequence_valid(sequence) or owner ~= address or guid ~= current.guid or kind ~= "camera" then break end
            pending = table.concat({"native-host-camera-intent", sequence, map, current.name, guid}, "\t") .. "\n"
        end
        if not publish(pending) then break end
        pending = nil
        footsteps.intercepted_shutter(current.pawn)
        emitted = emitted + 1
    end
    return emitted
end

function camera.sync(state, phase)
    if binding then return drain(context(state, phase)) end
end

function camera.command(fields, state, phase)
    local action = fields[2]
    if action == "native-host-camera-clear" and #fields == 2 then
        camera.reset()
        return "stock host camera scope cleared"
    end
    local equipping = action == "native-host-camera-equip-fixture"
    if action ~= "native-host-camera-scope" and action ~= "native-host-camera-input-fixture" and not equipping then return end
    local current = context(state, phase, equipping)
    if not current then
        camera.reset()
        error("The live stock host camera and active Dead Bridge photo quest are required", 0)
    end
    local address = current.pawn:GetAddress()
    if equipping then
        local guid = fields[4]
        if not fixtures or #fields ~= 4 or fields[3] ~= current.name or type(guid) ~= "string" or #guid ~= 32
            or guid:find("[^0-9A-F]") or not guid:find("[^0]")
            or type(SilverfishEquipNativeHostCameraFixture) ~= "function" then
            error("The exact owned stock camera and headless host fixture are required", 0)
        end
        camera.reset()
        local ok, status = SilverfishEquipNativeHostCameraFixture(address, current.controller:GetAddress(),
            current.world:GetAddress(), guid, quest)
        if ok ~= true or status ~= "stock_camera_equipped" then error(tostring(status), 0) end
        local equipped = context(state, phase)
        if not equipped or not same(equipped.pawn, current.pawn) or equipped.guid ~= guid then
            error("mutation_unverified_stock_host_camera_equip_readback", 0)
        end
        equipped_fixture = equipped
        return "stock host camera equipped"
    end
    if action == "native-host-camera-input-fixture" then
        if not fixtures or #fields ~= 3 or fields[3] ~= current.name
            or not same_context(current, binding) or os.time() >= binding.expires
            or type(SilverfishProbeNativeHostCameraInput) ~= "function" then
            error("The stock camera input fixture requires the exact scoped headless host pawn", 0)
        end
        local ok, status, sequence, wrappers = SilverfishProbeNativeHostCameraInput(address)
        if ok ~= true or status ~= "stock_pressed" or not sequence_valid(sequence) or wrappers ~= 2
            or drain(current) ~= 1 then error("The stock host camera input fixture did not verify (" .. tostring(status) .. ")", 0) end
        fixture = {context = current, sequence = sequence, wrappers = wrappers}
        return "stock host camera primary input verified"
    end
    if #fields ~= 6 or fields[3] ~= map or fields[4] ~= current.name or fields[5] ~= current.guid or fields[6] ~= quest
        or type(SilverfishSetNativeHostCameraScope) ~= "function" or type(SilverfishTakeNativeHostCameraIntent) ~= "function" then
        camera.reset()
        error("The authorized host camera identity is unavailable", 0)
    end
    if binding and not same_context(current, binding) then camera.reset() end
    local ok, mode = SilverfishSetNativeHostCameraScope(address, current.controller:GetAddress(), current.world:GetAddress(),
        current.guid, "camera", quest)
    if ok ~= true or mode ~= "stock_held" then
        camera.reset()
        error(tostring(mode or "stock_host_camera_scope_unavailable"), 0)
    end
    current.expires, current.mode = os.time() + 2, mode
    binding = current
    return "stock host camera scope refreshed"
end

function camera.rows(state, phase, status_row)
    local current = context(state, phase)
    if not current then return {} end
    local rows = {status_row("native-host-camera-ready", map, current.name, current.guid)}
    if same_context(current, binding) and os.time() < binding.expires then
        rows[#rows + 1] = status_row("native-host-camera-scope", map, current.name, current.guid, binding.mode)
    end
    if fixture and same_context(current, fixture.context) then
        rows[#rows + 1] = status_row("native-host-camera-fixture", map, current.name, current.guid,
            fixture.sequence, fixture.wrappers)
    end
    if equipped_fixture and same_context(current, equipped_fixture) then
        rows[#rows + 1] = status_row("native-host-camera-equip-fixture", map, current.name, current.guid, "stock_camera_equipped")
    end
    return rows
end

function camera.start(bridge, downed)
    event_path = enabled and not embedded and bridge and bridge ~= "" and bridge .. "/native-host-camera-events" or nil
    is_downed = downed
end

return camera
