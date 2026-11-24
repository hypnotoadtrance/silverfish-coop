local critters = {}
local reference = require("replicated_actor_reference")
local guest_save = require("guest_save")

local critter_path = "/Game/Classes/Ambiant_FX/Critter_Master_BP.Critter_Master_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local inventory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local collectibles = {
    Critter_Frog_BP_C = "/Game/Classes/Items/Item_LiveFrog.Item_LiveFrog_C",
    Critter_Weta_BP_C = "/Game/Classes/Items/Item_LiveWeta.Item_LiveWeta_C",
    Critter_Beetle_BP_C = "/Game/Classes/Items/Item_DeadBeetle.Item_DeadBeetle_C",
}
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local payload_field = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"

local current, phase, world, critter_class, marker_class, statics
local proof
local next_view = 0
local entries, numbered, pending = {}, {}, {}
local installed, censused

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function live(actor)
    return valid(actor) and not actor:IsActorBeingDestroyed() and actor:GetLocalRole() ~= 0
        and same(actor:GetWorld(), world)
end

local function exact_critter(actor)
    return live(actor) and valid(critter_class) and actor:IsA(critter_class) and actor.Health > 0
end

local function cargo(actor)
    return collectibles[actor:GetClass():GetFName():ToString()]
end

local function enroll(actor)
    if not exact_critter(actor) or not actor:HasAuthority() then return end
    local address = actor:GetAddress()
    if entries[address] then return end
    if not actor.bReplicates then actor:SetReplicates(true) end
    if not actor.bReplicateMovement then actor:SetReplicateMovement(true) end
    if not actor.bReplicates or not actor.bReplicateMovement then return end
    local number = current + 1
    local marker = reference.spawn(world, marker_class, "critter", actor, number)
    if not live(marker) then return end
    current = number
    local entry = {actor = actor, marker = marker, number = number}
    entries[address], numbered[number] = entry, entry
end

local function observe(marker)
    if not live(marker) or not same(marker:GetClass(), marker_class) or marker:HasAuthority() then return end
    local number = reference.number(marker, "critter")
    local actor = marker:GetOwner()
    if number and exact_critter(actor) and actor:GetLocalRole() == 1 then
        entries[actor:GetAddress()] = {actor = actor, marker = marker, number = number}
    end
end

local function install()
    if installed then return end
    if type(RegisterHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function"
        or type(RegisterEndPlayPreHook) ~= "function" then return end
    RegisterBeginPlayPostHook(function(context)
        if phase ~= "hosting" then return end
        local actor = context:get()
        if exact_critter(actor) then pending[actor:GetAddress()] = actor end
    end)
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if phase ~= "connected" then return end
        local actor = context:get()
        if live(actor) and same(actor:GetClass(), marker_class) then pending[actor:GetAddress()] = actor end
    end)
    RegisterEndPlayPreHook(function(context, reason)
        local actor = context:get()
        if not phase or not valid(actor) then return end
        local address = actor:GetAddress()
        local entry = entries[address]
        entries[address], pending[address] = nil, nil
        if entry and phase == "hosting" then
            numbered[entry.number] = nil
            if reason:get() == 0 and valid(entry.marker) then entry.marker:K2_DestroyActor() end
        end
    end)
    installed = true
end

function critters.sync(state, seat)
    if seat ~= "hosting" and seat ~= "connected" or not valid(state.world) then
        phase = nil
        return
    end
    install()
    if not installed then return end
    if phase ~= seat or not same(world, state.world) then
        world, phase, current, entries, numbered, pending = state.world, seat, 0, {}, {}, {}
        proof, censused, next_view = nil, false, 0
    end
    if not censused then
        marker_class, critter_class, statics = StaticFindObject(reference.class), StaticFindObject(critter_path),
            StaticFindObject(statics_path)
        if not valid(marker_class) or not valid(critter_class) or not valid(statics) then return end
        if seat == "hosting" then
            local output = {}
            statics:GetAllActorsOfClass(world, critter_class, output)
            for _, actor in ipairs(output.OutActors or output) do
                if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                enroll(actor)
            end
        else
            for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do observe(marker) end
        end
        censused = true
    end
    for address, actor in pairs(pending) do
        pending[address] = nil
        if phase == "hosting" then enroll(actor) else observe(actor) end
    end
    if phase ~= "connected" or os.clock() < next_view then return end
    next_view = os.clock() + .05
    for address, entry in pairs(entries) do
        local actor = entry.actor
        if not exact_critter(actor) then
            entries[address] = nil
        else
            local mesh, movement = actor.StaticMesh, actor.CharacterMovement
            local asset = valid(movement) and (movement:IsFalling() and actor.Mesh_Hopping or actor.Mesh_Normal)
            if valid(mesh) and valid(asset) and not same(mesh.StaticMesh, asset) then mesh:SetStaticMesh(asset) end
        end
    end
end

function critters.focused_target(pawn, component)
    if phase ~= "connected" or type(component) ~= "string" or #component > 512
        or not valid(pawn) or not same(pawn:GetWorld(), world) then return end
    local focus = StaticFindObject(component)
    local actor = valid(focus) and focus:GetOwner()
    if not exact_critter(actor) or not cargo(actor) then return end
    local entry = entries[actor:GetAddress()]
    if entry and live(entry.marker) and same(entry.marker:GetOwner(), actor) then
        return "stock-critter:" .. entry.number
    end
end

local function guid(row)
    local value = row[guid_field]
    return ("%08X%08X%08X%08X"):format(value.A & 0xffffffff, value.B & 0xffffffff,
        value.C & 0xffffffff, value.D & 0xffffffff)
end

function critters.command(args, state, seat)
    if args[2] ~= "native-stock-critter-pickup" then return end
    proof = nil
    if #args ~= 4 or seat ~= "hosting" or phase ~= seat or not same(state.world, world) then
        error("stock_critter_scope_unavailable", 0)
    end
    local number = math.tointeger(args[3])
    local entry = number and numbered[number]
    local actor = entry and entry.actor
    local item = exact_critter(actor) and cargo(actor)
    local pawn, save = guest_save.exact_item_context(state, seat, args[4])
    local stock = state.world.OwningGameInstance.SaveSlot_Ref
    if not item or not actor:HasAuthority() or not valid(pawn) or pawn.Dead or not valid(save) or same(save, stock) then
        error("stock_critter_pickup_unavailable", 0)
    end
    local class = StaticFindObject(item)
    if not valid(class) then LoadAsset(item) class = StaticFindObject(item) end
    local converter, save_class = StaticFindObject(inventory_path), StaticFindObject(save_path)
    if not valid(class) or not valid(converter) or not valid(save_class) or type(SilverfishClaimNativeLootEscrow) ~= "function" then
        error("stock_critter_inventory_unavailable", 0)
    end
    local scratch = statics:CreateSaveGameObject(save_class)
    if not valid(scratch) then error("stock_critter_inventory_unavailable", 0) end
    local converted, defaults = {}, class:GetCDO()
    converter:ItemActorToStruct(defaults, pawn, converted)
    local row = converted.Item or converted
    if not same(row[class_field], class) then error("stock_critter_inventory_unavailable", 0) end
    scratch.PlayerBackpack[1] = row
    scratch.PlayerBackpack[1][payload_field] = defaults.DataStruct
    local id = guid(scratch.PlayerBackpack[1])
    local ok, outcome, amount, before, after, inventory_guid = SilverfishClaimNativeLootEscrow(
        pawn:GetAddress(), save:GetAddress(), stock:GetAddress(), scratch:GetAddress(), id)
    if not ok or outcome ~= "claimed" and outcome ~= "merged" then
        if not ok and type(outcome) == "string" and outcome:match("^native_loot_") then
            error("stock_critter_inventory_unavailable", 0)
        end
        error("mutation_unverified_stock_critter_inventory", 0)
    end
    local name = actor:GetFullName()
    actor:K2_DestroyActor()
    if live(actor) and numbered[number] then error("mutation_unverified_stock_critter_removal", 0) end
    proof = {number, args[4], name, item, id, outcome, inventory_guid or "-", amount or 0, before or 0, after or 0}
    return "stock critter moved into guest inventory"
end

function critters.row(state, seat, row)
    if seat == "hosting" and proof and same(state.world, world) then
        return row("native-stock-critter-claim", state.map, table.unpack(proof))
    end
end

return critters
