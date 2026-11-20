local mindcore = {}
local guest_save = require("guest_save")
local players = require("players")
local transport = require("native_transport")
local wire = require("native_wire_quest")

local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world = map .. ".DataSilo_01_P"
local persistent = world .. ":PersistentLevel."
local cradle_path, cradle_class = persistent .. "MindCore_Cradle_BP_C_1",
    "/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C"
local upload_path, upload_class = persistent .. "DataTape_Uploader_BP_C_1",
    "/Game/Classes/Actors/DataTape_Uploader_BP.DataTape_Uploader_BP_C"
local core_path, core_class = persistent .. "MindCore_BP_C_1", "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"
local product = "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"
local instance_class, save_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C",
    "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local box_class, mesh_class = "/Script/Engine.BoxComponent", "/Script/Engine.StaticMeshComponent"
local flag = "MC_007_Console"
local proofs = {
    ["native-mindcore-preflight"] = true, ["native-mindcore-upload"] = true, ["native-mindcore-verify"] = true,
}
local last

local function refuse(reason) error("native_mindcore_refused_" .. reason, 0) end
local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) <= 10000000 end

local function vector(point)
    return point and finite(point.X) and finite(point.Y) and finite(point.Z)
end

local function within(a, b, distance)
    return vector(a) and vector(b) and (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= distance^2
end

local function actor(state, path, class, short_class)
    local object = StaticFindObject(path)
    if not exact(object, class) or object:GetFullName() ~= short_class .. " " .. path
        or not object:HasAuthority() or not same(object:GetWorld(), state.world)
        or object:IsActorBeingDestroyed() then refuse("placed_actor_unavailable") end
    return object
end

local function scope(state, phase)
    if phase ~= "hosting" or state.map ~= map or not state.listening or state.server or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. world then refuse("listen_scope_unavailable") end
    local driver, local_pawn = state.world.NetDriver, state.local_pawn and state.local_pawn.pawn
    if not valid(driver) or not same(driver.World, state.world) or valid(driver.ServerConnection)
        or not valid(local_pawn) or not local_pawn:HasAuthority() then refuse("driver_unavailable") end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(local_pawn)
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_class) or not exact(stock, save_class)
        or not same(instance["As Player FP"], local_pawn) or not same(local_pawn.Save, stock) then
        refuse("canonical_save_unavailable")
    end

    local cradle = actor(state, cradle_path, cradle_class, "MindCore_Cradle_BP_C")
    local uploader = actor(state, upload_path, upload_class, "DataTape_Uploader_BP_C")
    local core = actor(state, core_path, core_class, "MindCore_BP_C")
    local mount, controls, frame = cradle.SM_MindCoffin_Cradle, cradle.Button_Tape, core.PropMesh
    if not same(cradle.MindCore_Ref, core) or not same(cradle.TapeInserter_Ref, uploader)
        or not exact(mount, mesh_class) or not same(mount:GetOwner(), cradle)
        or not exact(frame, mesh_class) or not same(frame:GetOwner(), core) or not same(core.RootComponent, frame)
        or not exact(controls, box_class) or not same(controls:GetOwner(), cradle)
        or not cradle.ConsoleID or cradle.ConsoleID:ToString() ~= flag then refuse("authored_links_unavailable") end
    local status, saved = math.tointeger(cradle.Status), wire.stock_flag(instance, flag)
    if not status or status < 0 or status > 2 or status ~= saved then refuse("stock_status_unsettled") end
    local installed = within(core:K2_GetActorLocation(), mount:K2_GetComponentLocation(), 12)
        and frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) == false
    return {world = state.world, cradle = cradle, uploader = uploader, controls = controls, core = core, installed = installed,
        status = status, canonical = stock}
end

local function qualify(item, state, phase, name, guid, reach)
    local pawn, detached = guest_save.exact_item_context(state, phase, name)
    if not item.installed or item.status == 0 or not valid(pawn) or not valid(detached)
        or not same(pawn.Save, detached) or same(item.canonical, detached) or not pawn:HasAuthority()
        or pawn.Dead ~= false or not players.is_standing_guest(pawn, name) then refuse("guest_or_core_unavailable") end
    if reach then
        local position, distance = pawn:K2_GetActorLocation(), pawn.InteractionRange
        if not finite(distance) or distance <= 0 or distance > 1000
            or not within(position, item.cradle:K2_GetActorLocation(), distance)
            or not within(position, item.controls:K2_GetComponentLocation(), distance) then refuse("console_out_of_reach") end
    end
    transport.load_class(product)
    if type(SilverfishReadNativeMindCoreResults) ~= "function" then refuse("native_bridge_unavailable") end
    local ok, code = SilverfishReadNativeMindCoreResults(
        pawn:GetAddress(), detached:GetAddress(), item.canonical:GetAddress(), guid)
    if ok ~= true or code ~= "stock_results_in_backpack" then refuse("guest_results_unverified") end
    return pawn, detached
end

function mindcore.command(fields, state, phase)
    local verb, pawn_name, guid = fields[2], fields[3], fields[4]
    if not proofs[verb] then return end
    last = nil
    if #fields ~= 4 or not transport.valid_guid(guid) then refuse("request_unqualified") end
    local current = scope(state, phase)
    local pawn, detached = qualify(current, state, phase, pawn_name, guid, verb ~= "native-mindcore-verify")
    local status, proof = current.status, "owned"
    if verb == "native-mindcore-upload" then
        if status ~= 1 or type(SilverfishUploadNativeMindCoreResults) ~= "function" then refuse("not_ready_to_upload") end
        local ok, reason = SilverfishUploadNativeMindCoreResults(
            pawn:GetAddress(), detached:GetAddress(), current.canonical:GetAddress(), guid)
        if ok ~= true then error(reason, 0) end
        if reason ~= "stock_insert_dispatched" then error("mutation_unverified_native_mindcore_dispatch", 0) end
        proof = "dispatched"
    elseif verb == "native-mindcore-verify" and status ~= 2 then refuse("compilation_incomplete") end
    last = {world = state.world, verb = verb, pawn = pawn_name, guid = guid, proof = proof, status = status}
    return "exact MindCore stock ownership and console state verified"
end

function mindcore.rows(state, phase, row)
    if state.map ~= map or phase ~= "hosting" then return {} end
    if type(SilverfishReadNativeMindCoreResults) ~= "function" then return {} end
    local accepted, item = pcall(scope, state, phase)
    if not accepted then return {} end
    local lines = {row("native-mindcore-scene", map, "MindCore_Cradle_BP_C " .. cradle_path,
        "DataTape_Uploader_BP_C " .. upload_path, "MindCore_BP_C " .. core_path, flag, item.status,
        item.installed and 1 or 0)}
    if last and same(last.world, state.world) then
        lines[#lines + 1] = row("native-mindcore-tape", map, last.verb, last.pawn, last.guid, last.proof, last.status)
    end
    return lines
end

function mindcore.clear()
    last = nil
end

return mindcore
