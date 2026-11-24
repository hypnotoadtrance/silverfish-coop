-- Injected into native_stash.lua only by the isolated large portable scenario.
local portable_seed
local portable_map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local portable_merchant_path = portable_map .. ".Observitory_P:PersistentLevel.Shop_BP_C_2"
local portable_bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local portable_stash_id = "Stash_ID_6_90081F6E44D7BFA5FA8C15887774759B"
local portable_contents = "Contents_9_134977CA456AB05C7AE11DAAF3F758BE"
local portable_item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local portable_item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local portable_rows = 384

local portable_reader_calls = 0
local portable_census_items = census_items
if portable_census_items then
    census_items = function(reader, ...)
        if portable_seed and reader == SilverfishReadLiveNativeStashCensus then
            portable_reader_calls = portable_reader_calls + 1
        end
        return portable_census_items(reader, ...)
    end
end

local function portable_stash_array(stashes, combined, id)
    if combined and stashes:GetArrayNum() > 0 then return stashes[1][portable_contents] end
    for index = 1, stashes:GetArrayNum() do
        local record = stashes[index]
        if identifier(record[portable_stash_id]) == id then return record[portable_contents] end
    end
end

local function portable_stash_census(pawn, combined, site, guids)
    local ok, status, rows, total = SilverfishReadLiveNativeStashCensus(pawn:GetAddress(), combined, site)
    assert(ok == true and status == "complete" and type(rows) == "string" and total == #guids,
        "portable large stash authority census is unavailable")
    local index = 0
    for guid, offset, class, amount in rows:gmatch("([^\t\n]+)\tstash\t(%d+)\t([^\t\n]+)\t(%d+)[^\n]*\n") do
        index = index + 1
        assert(guid == guids[index] and tonumber(offset) == index - 1 and class == portable_bandage_path and amount == "1",
            "portable large stash authority row changed")
    end
    assert(index == total, "portable large stash authority census is incomplete")
end

local function portable_large_stash(fields, state, phase)
    assert(#fields == 5 and phase == "hosting" and state.map == portable_map and #state.guests == 1
        and os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1",
        "portable large stash fixture requires the isolated Observatory host")
    local name, site, character = fields[3], fields[4], fields[5]
    local pawn, combined, host = stash_context(state, phase, name, site)
    local bound, detached, owner = guest_save.exact_item_context(state, phase, name)
    assert(same(pawn, bound) and not same(pawn, host) and same(pawn.Save, detached)
        and not same(host.Save, detached) and owner == character and type(combined) == "boolean",
        "portable large stash fixture has no exact private guest save")
    local stashes = detached.Stashes
    local contents = portable_stash_array(stashes, combined, site)
    if fields[2] == "portable-large-stash-census-reads" then
        assert(portable_seed and same(portable_seed.save, detached) and same(portable_seed.pawn, pawn)
            and same(portable_seed.world, state.world) and portable_seed.site == site
            and portable_seed.owner == owner and portable_seed.combined == combined)
        return ("portable live stash census reads %d"):format(portable_reader_calls)
    end
    if fields[2] == "portable-large-stash-clear" then
        assert(portable_seed and same(pawn, portable_seed.pawn) and same(detached, portable_seed.save)
            and same(state.world, portable_seed.world) and site == portable_seed.site and owner == portable_seed.owner
            and combined == portable_seed.combined and contents, "portable large stash clear left its original scope")
        portable_stash_census(pawn, combined, site, portable_seed.guids)
        contents:Empty()
        if portable_seed.created then stashes:Empty() end
        portable_stash_census(pawn, combined, site, {})
        portable_seed = nil
        return "portable large stash cleared without save"
    end

    local created = stashes:GetArrayNum() == 0
    assert(not portable_seed and (created or contents and contents:GetArrayNum() == 0),
        "portable large stash fixture requires the original empty stash")
    portable_stash_census(pawn, combined, site, {})
    local merchant = StaticFindObject(portable_merchant_path)
    local class = StaticFindObject(portable_bandage_path)
    if not valid(class) then LoadAsset(portable_bandage_path); class = StaticFindObject(portable_bandage_path) end
    assert(valid(merchant) and same(merchant:GetWorld(), state.world) and merchant:HasAuthority()
        and merchant:GetFullName() == "Shop_BP_C " .. portable_merchant_path and valid(class),
        "portable large stash stock item generator is unavailable")
    local items, guids, seen = {}, {}, {}
    for index = 1, portable_rows do
        local result = {}
        merchant:GenerateItem(class, 1, result)
        local item = result.Item or result
        local guid = identifier(item[portable_item_guid])
        assert(same(item[portable_item_class], class) and guid and not seen[guid],
            "portable large stash stock item generator did not produce a unique bandage")
        items[index], guids[index], seen[guid] = item, guid, true
    end
    if created then
        local parts = {}
        for index, part in ipairs({"A", "B", "C", "D"}) do
            local word = tonumber(site:sub(index * 8 - 7, index * 8), 16)
            parts[part] = word >= 0x80000000 and word - 0x100000000 or word
        end
        stashes[1] = {[portable_stash_id] = parts}
        contents = assert(portable_stash_array(stashes, combined, site))
    end
    local filled, failure = pcall(function()
        for index, item in ipairs(items) do contents[index] = item end
        portable_stash_census(pawn, combined, site, guids)
    end)
    if not filled then
        contents:Empty()
        if created then stashes:Empty() end
        error(failure, 0)
    end
    portable_seed = {pawn = pawn, save = detached, world = state.world, site = site, owner = owner,
        combined = combined, guids = guids, created = created}
    return "portable large stash seeded " .. table.concat(guids, ",")
end
