local enemy_loot = {}
local guest_save = require("guest_save")
local transport = require("native_transport")

local map = "/Game/Classes/Maps/PlaytestMap_01"
local bandit_class = "/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ENEMY_LOOT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_NPC_TEST") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local watch

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function participants(state, phase)
    if not enabled or phase ~= "hosting" or state.map ~= map or state.server == true or not state.listening
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. map .. ".PlaytestMap_01"
        or not state.guests or #state.guests ~= 1 then return end
    local host, fresh = transport.scoped_host(state, phase)
    local guest = state.guests[1]
    if not host or not fresh or not same(host.pawn:GetWorld(), state.world) or type(guest.name) ~= "string"
        or not valid(guest.pawn) or guest.name ~= guest.pawn:GetFullName() then return end
    local protected, pawn, detached = pcall(guest_save.exact_item_context, state, phase, guest.name)
    if not protected or not same(pawn, guest.pawn) or not valid(detached) then return end
    return host.pawn, pawn
end

function enemy_loot.clear()
    if watch and type(SilverfishClearNativeEnemyLootScope) == "function" then
        SilverfishClearNativeEnemyLootScope()
    end
    watch = nil
end

local function read(host)
    local result = table.pack(SilverfishReadNativeEnemyLoot(host:GetAddress()))
    if result.n ~= 15 and result.n ~= 23 then return end
    for index = 1, result.n do
        if result[index] == "" then result[index] = "-" end
        if type(result[index]) == "boolean" then result[index] = result[index] and 1 or 0 end
    end
    return result
end

function enemy_loot.sync(state, phase)
    if not watch then return end
    local host, guest = participants(state, phase)
    if not same(state.world, watch.world) or not same(host, watch.host) or not same(guest, watch.guest) then
        enemy_loot.clear()
        return
    end
    if not valid(watch.bandit) or watch.bandit:IsActorBeingDestroyed() then return end
    local accepted = SilverfishSetNativeEnemyLootScope(host:GetAddress(), guest:GetAddress(), watch.bandit:GetAddress())
    if accepted then watch.proof = read(host) or watch.proof end
end

function enemy_loot.command(fields, state, phase)
    local action = fields[2]
    if action == "native-enemy-loot-clear" and #fields == 2 then
        enemy_loot.clear()
        return "stock enemy loot observer cleared"
    end
    if action ~= "native-enemy-loot-watch" then return end
    local expected = fields[3]
    local host, guest = participants(state, phase)
    if not valid(host) or not valid(guest) or #fields ~= 3 or type(expected) ~= "string" or #expected > 512
        or expected:find("%c") or type(SilverfishReadNativeBandit) ~= "function"
        or type(SilverfishSetNativeEnemyLootScope) ~= "function" or type(SilverfishReadNativeEnemyLoot) ~= "function" then
        error("The stock enemy loot observer requires an exact staged host and enrolled guest", 0)
    end
    local authority = table.pack(SilverfishReadNativeBandit(host:GetAddress(), guest:GetAddress()))
    if authority.n ~= 24 or authority[1] ~= true or authority[2] ~= "stock_bandit_authority"
        or authority[3] ~= 1 or authority[4] ~= 0 or authority[5] ~= expected
        or authority[11] ~= true or authority[12] ~= true then
        error("The exact live stock Bandit authority receipt is unavailable", 0)
    end
    local bandit
    local class = StaticFindObject(bandit_class)
    for _, candidate in ipairs(FindAllOf("NPC_Bandit_C") or {}) do
        if valid(candidate) and candidate:GetFullName() == expected and candidate:HasAuthority()
            and same(candidate:GetWorld(), state.world) and same(candidate:GetClass(), class) then
            if bandit then error("The exact stock Bandit is ambiguous", 0) end
            bandit = candidate
        end
    end
    if not valid(bandit) then error("The exact stock Bandit is unavailable", 0) end
    enemy_loot.clear()
    local accepted, reason = SilverfishSetNativeEnemyLootScope(host:GetAddress(), guest:GetAddress(), bandit:GetAddress())
    if accepted ~= true or reason ~= "watching" then
        error("The stock enemy loot watch could not bind (" .. tostring(reason) .. ")", 0)
    end
    watch = { world = state.world, host = host, guest = guest, bandit = bandit, name = expected, proof = read(host) }
    return "stock enemy loot watch armed"
end

function enemy_loot.rows(state, phase, status_row)
    if watch and watch.proof and phase == "hosting" and state.map == map and same(state.world, watch.world) then
        local rows = {status_row("native-enemy-loot", map, watch.name, table.unpack(watch.proof, 1, 15))}
        if watch.proof.n == 23 then
            rows[#rows + 1] = status_row("native-enemy-bounty", map, watch.name, table.unpack(watch.proof, 16, 23))
        end
        return rows
    end
    return {}
end

return enemy_loot
