local barter = {}
local profile = require("native_dialogue_mama")
local guest_save = require("guest_save")
local wire = require("native_wire_quest")
local map = profile.map
local copper = "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"
local ammo = "/Game/Classes/Items/Ammo_Intr_AP_BP.Ammo_Intr_AP_BP_C"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local bench_path = observatory .. ".Observitory_P:PersistentLevel.StaticMeshActor_51"
local board = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
local scrap = "/Game/Classes/Items/Item_ElectronicScrap.Item_ElectronicScrap_C"
local report

local function refuse(reason) error("borough_barter_refused_" .. reason, 0) end
local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function guid(value)
    return value and #value == 32 and value:match("^[0-9A-F]+$") and value ~= string.rep("0", 32)
end

function barter.command(fields, state, phase, players)
    local seed = fields[2] == "native-crafting-bench-scrap-fixture"
    local crafting = seed or fields[2] == "native-crafting-bench"
    if not crafting and fields[2] ~= "native-borough-mama-trade" then return end
    local area = crafting and observatory or map
    local exchange = SilverfishTradeNativeBoroughMama
    if crafting then exchange = SilverfishTradeNativeCraftingBench end
    if seed then exchange = SilverfishSeedNativeCraftingScrap end
    report = nil
    local name, input = fields[3], fields[4]
    if (seed and #fields ~= 3 or not seed and (#fields ~= 4 or not guid(input)))
        or phase ~= "hosting" or state.map ~= area or state.server ~= false
        or not players or type(exchange) ~= "function" then refuse("arguments") end
    local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not qualified or not valid(pawn) or not valid(save) or pawn:GetFullName() ~= name
        or not players.is_standing_guest(pawn, name) or pawn.Dead ~= false then refuse("guest_scope") end
    local instance = state.world.OwningGameInstance
    local post, stock
    if crafting then
        post, stock = StaticFindObject(bench_path), instance.SaveSlot_Ref
        if not valid(post) or post:GetFullName() ~= "CraftingBench_BP_C " .. bench_path
            or not same(post:GetWorld(), state.world) or not valid(stock) or same(save, stock) then
            refuse("exact_workbench")
        end
    else
        local npc
        npc, post, stock = profile.authorized_trade(state, phase, pawn)
        if not npc or same(save, stock) then refuse("trade_dialogue_expired") end
        local point, limit = pawn:K2_GetActorLocation(), pawn.InteractionRange
        if type(limit) ~= "number" or not (limit > 0 and limit <= 1000) then refuse("guest_reach") end
        for _, target in ipairs({npc:K2_GetActorLocation(), npc.Sphere_Interact:K2_GetComponentLocation()}) do
            if not ((point.X - target.X)^2 + (point.Y - target.Y)^2 + (point.Z - target.Z)^2 <= limit^2) then
                refuse("guest_reach")
            end
        end
    end
    local before = wire.stock_flag(instance, "Player_Cash")
    if not math.tointeger(before) then refuse("wallet_unavailable") end
    if seed then
        local ok, result = exchange(pawn:GetAddress(), save:GetAddress(), stock:GetAddress())
        if ok ~= true or result ~= "seeded" then error(tostring(result), 0) end
        if wire.stock_flag(instance, "Player_Cash") ~= before or not same(instance.SaveSlot_Ref, stock) then
            error("mutation_unverified_crafting_scrap_fixture_readback", 0)
        end
        return "stock Electron Scrap fixture verified"
    end
    local ok, result, output, armor_guid = exchange(pawn:GetAddress(), save:GetAddress(), stock:GetAddress(), input)
    if ok ~= true then error(tostring(result), 0) end
    local after = wire.stock_flag(instance, "Player_Cash")
    if result ~= "exchanged" or not guid(output) or output == input or before ~= after or not same(instance.SaveSlot_Ref, stock)
        or (crafting and (not guid(armor_guid) or armor_guid == input or armor_guid == output)) then
        error("mutation_unverified_borough_barter_readback", 0)
    end
    report = {world = state.world, map = area, pawn = pawn, name = name, post = post, input = input, output = output,
        wallet = before, armor_guid = crafting and armor_guid or nil}
    return crafting and "exact stock Crafting Bench exchange verified" or "exact stock Mama Copper Wire trade verified"
end

function barter.rows(state, phase, status_row)
    if not report or phase ~= "hosting" or state.map ~= report.map or not same(report.world, state.world)
        or not valid(report.pawn) or not valid(report.post) then return {} end
    if report.armor_guid then
        return {status_row("native-crafting-bench", observatory, report.name, report.post:GetFullName(), "exchanged", report.input,
            board, 1, report.output, scrap, 1, report.wallet, report.wallet, 0, report.armor_guid)}
    end
    return {status_row("native-borough-barter", map, report.name, report.post:GetFullName(), "exchanged", report.input,
        copper, 1, report.output, ammo, 25, report.wallet, report.wallet, 0)}
end

return barter
