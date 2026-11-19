local catalog = {}

local statics_path = "/Script/Engine.Default__GameplayStatics"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local table_name = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local quest_struct = "/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final_field = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local cash_field = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local xp_field = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"
local items_field = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local active_limit, stock_limit, item_limit = 64, 128, 8
local source_count_limit = 1024
local cached

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and not object:GetFullName():find("Default__", 1, true)
        and same(object:GetClass(), StaticFindObject(path))
end

local function canonical_save(state)
    local details, controller, world = state.local_pawn, state.controller, state.world
    local pawn = details and details.pawn
    if not valid(world) or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map
        or not valid(pawn) or not pawn:HasAuthority() or not pawn:GetFullName():match("^Player_FP_C ")
        or not same(controller, details.controller_object) or not controller:IsLocalController()
        or not controller:HasAuthority() or not controller:GetFullName():match("^PlayerControler_FP_C ")
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world) then
        return
    end
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_path) or not exact(save, save_path) or not same(pawn.Save, save) then return end
    return save, instance, pawn
end

local function int32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil
        and value >= -2147483648 and value <= 2147483647
end

local function quest_identity(id, original)
    if type(id) ~= "string" or id == "" or #id > 96 or id == "None" or id:find("[%c]") then return end
    local name = FName(id, EFindName.FNAME_Find)
    if not name or name:GetComparisonIndex() == 0 or name:ToString() ~= id
        or (original and (original:GetComparisonIndex() ~= name:GetComparisonIndex() or original:ToString() ~= id)) then
        return
    end
    return id
end

local function reward_path(reward)
    if not valid(reward) then return end
    local path = reward:GetFullName():match("^%S+ (/Game/[%w_/%-]+%.[%w_%-]+)$")
    if path and #path <= 256 then return path end
end

local function quest_entry(source, id, quest)
    if not id or not quest then return end
    local step, final_step, cash, xp = quest[step_field], quest[final_field], quest[cash_field], quest[xp_field]
    if not int32(step) or not int32(final_step) or final_step < 0 or not int32(cash) or cash < 0
        or not int32(xp) or xp < 0 then return end

    local rewards = quest[items_field]
    local count = rewards and rewards:GetArrayNum()
    if not int32(count) or count < 0 then return end
    local items = {}
    for index = 1, math.min(count, item_limit) do
        local ok, path = pcall(function() return reward_path(rewards[index]) end)
        if ok and path then items[#items + 1] = { index = index - 1, path = path } end
    end
    -- "ready" describes the final step. Stock turn-in still checks objective items and conditions.
    local status = source == "stock" and "template" or source == "board" and "offered" or step == -10 and "completed"
        or step > 0 and step == final_step and "ready"
        or step > 0 and step < final_step and "active" or "inactive"
    return { id = id, state = status, step = step, final_step = final_step, cash = cash, xp = xp,
        count = count, items = items }
end

local function unavailable(source, status_row, reason)
    return { status_row("native-quest-catalog", source, "unavailable", "-", 0, reason) }
end

local function collect(source, total, limit, visit, status_row)
    if not int32(total) or total < 0 or total > source_count_limit then
        return unavailable(source, status_row, "catalog_count_unqualified")
    end
    local entries, seen = {}, {}
    local examined, incomplete_rewards = 0, false
    if total > 0 then
        visit(function(read)
            examined = examined + 1
            local ok, entry = pcall(read)
            if ok and entry and not seen[entry.id:lower()] then
                seen[entry.id:lower()] = true
                entries[#entries + 1] = entry
                incomplete_rewards = incomplete_rewards or #entry.items ~= entry.count
            end
            -- Pinned UE4SS iterators stop on true and require nil when continuing.
            if examined >= math.min(total, limit) then return true end
        end)
    end
    table.sort(entries, function(first, second) return first.id < second.id end)
    local reason = total > limit and "entry_limit"
        or #entries ~= total and "unqualified_entries"
        or incomplete_rewards and "reward_summary_limited" or ""
    local rows = { status_row("native-quest-catalog", source, reason == "" and "ready" or "partial",
        total, #entries, reason) }
    for _, entry in ipairs(entries) do
        rows[#rows + 1] = status_row("native-quest-entry", source, entry.id, entry.state,
            entry.step, entry.final_step, entry.cash, entry.xp, entry.count)
        for _, item in ipairs(entry.items) do
            rows[#rows + 1] = status_row("native-quest-reward-item", source, entry.id, item.index, item.path)
        end
    end
    return rows
end

local function map_rows(source, quests, limit, status_row)
    return collect(source, #quests, limit, function(accept)
        quests:ForEach(function(raw_key, raw_value)
            return accept(function()
                local key = raw_key:get()
                local id = quest_identity(key:ToString(), key)
                if not id then return end
                return quest_entry(source, id, raw_value:get())
            end)
        end)
    end, status_row)
end

function catalog.board_rows(contracts, status_row)
    return map_rows("board", contracts, stock_limit, status_row)
end

local function stock_rows(status_row)
    local quests = StaticFindObject(table_name)
    if not valid(quests) or quests:GetFullName() ~= "DataTable " .. table_name then
        return unavailable("stock", status_row, "quest_table_unavailable")
    end
    local schema = quests:GetRowStruct()
    if not valid(schema) or schema:GetFullName() ~= "UserDefinedStruct " .. quest_struct then
        return unavailable("stock", status_row, "quest_table_schema_unqualified")
    end
    local names = quests:GetRowNames()
    if type(names) ~= "table" then return unavailable("stock", status_row, "stock_row_names_unqualified") end
    return collect("stock", #names, stock_limit, function(accept)
        for _, row_name in ipairs(names) do
            if accept(function()
                local id = quest_identity(row_name)
                if id then return quest_entry("stock", id, quests:FindRow(row_name)) end
            end) then break end
        end
    end, status_row)
end

function catalog.rows(state, phase, status_row)
    if phase ~= "hosting" or not state.listening or state.server then
        cached = nil
        return {}
    end
    local qualified, save, instance, pawn = pcall(canonical_save, state)
    if not qualified or not save then
        cached = nil
        return {
            status_row("native-quest-catalog", "active", "unavailable", "-", 0, "canonical_host_unavailable"),
            status_row("native-quest-catalog", "stock", "unavailable", "-", 0, "canonical_host_unavailable"),
        }
    end

    local now, world_address, save_address = os.time(), state.world:GetAddress(), save:GetAddress()
    if cached and cached.world == world_address and cached.save == save_address and cached.time == now then
        return cached.rows
    end
    local active_ok, active = pcall(map_rows, "active", save.ActiveQuests, active_limit, status_row)
    local stock_ok, stock = pcall(stock_rows, status_row)
    if not same(pawn.Save, save) or not same(instance.SaveSlot_Ref, save) then
        cached = nil
        return {
            status_row("native-quest-catalog", "active", "unavailable", "-", 0, "canonical_save_changed"),
            status_row("native-quest-catalog", "stock", "unavailable", "-", 0, "canonical_save_changed"),
        }
    end
    if not active_ok then
        print("[SilverfishCoop] Native active quest catalog: " .. tostring(active):sub(1, 320) .. "\n")
        active = unavailable("active", status_row, "active_quest_read_failed")
    end
    if not stock_ok then
        print("[SilverfishCoop] Native stock quest catalog: " .. tostring(stock):sub(1, 320) .. "\n")
        stock = unavailable("stock", status_row, "stock_quest_read_failed")
    end
    for _, row in ipairs(stock) do active[#active + 1] = row end
    cached = { world = world_address, save = save_address, time = now, rows = active }
    return active
end

return catalog
