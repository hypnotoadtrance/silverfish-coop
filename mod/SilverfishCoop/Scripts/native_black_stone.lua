local stone = {}
local transport = require("native_transport")

local map = "/Game/Classes/Maps/Dreams/BlackOcean_P"
local actor_path = "Art_BlackStone_C " .. map .. ".BlackOcean_P:PersistentLevel.Art_BlackStone_C_1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local receipt, current_world

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function placed(world)
    local match
    for _, actor in ipairs(FindAllOf("Art_BlackStone_C") or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and actor:GetFullName() == actor_path then
            if match then return end
            match = actor
        end
    end
    return match
end

function stone.sync(state, phase)
    if type(SilverfishSetNativeBlackStoneScope) ~= "function" then return end
    local host = phase == "hosting"
    if state.map ~= map or not valid(state.world) or not host and phase ~= "connected" then
        if current_world and type(SilverfishClearNativeBlackStoneScope) == "function" then
            SilverfishClearNativeBlackStoneScope()
        end
        receipt, current_world = nil, nil
        return
    end
    if not same(current_world, state.world) then receipt, current_world = nil, state.world end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(pawn) then return end
    local actor = placed(state.world)
    local _, fresh = transport.scoped_host(state, phase)
    SilverfishSetNativeBlackStoneScope(pawn:GetAddress(), valid(actor) and actor:GetAddress() or 0, host, fresh == true)
    if host and type(SilverfishTakeNativeBlackStonePickup) == "function" then
        local ok, guid, entry = SilverfishTakeNativeBlackStonePickup()
        if ok and transport.valid_guid(guid) and (entry == "stock_f" or fixtures and entry == "fixture") then
            receipt = { guid = guid, entry = entry, pawn = pawn:GetFullName() }
        end
    end
end

function stone.rows(state, phase, status_row)
    if state.map ~= map or phase ~= "hosting" or type(SilverfishReadNativeBlackStoneStatus) ~= "function" then return {} end
    local installed, armed, fresh, status = SilverfishReadNativeBlackStoneStatus()
    return { status_row("native-black-stone", map, installed and 1 or 0, armed and 1 or 0, fresh and 1 or 0,
        status, receipt and receipt.guid or "-", receipt and receipt.entry or "-", receipt and receipt.pawn or "-") }
end

function stone.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-dream-stone-position" and action ~= "native-dream-stone-fixture" then return end
    local actor = placed(state.world)
    local host, fresh = transport.scoped_host(state, phase)
    if not fixtures or #fields ~= 3 or state.map ~= map or not host or not fresh or not valid(actor)
        or actor.IsDisarmed ~= true or not valid(actor.Box) or not same(actor.Box:GetOwner(), actor) then
        error("The exact stock Black Stone fixture is unavailable", 0)
    end
    local pawn
    if fields[3] == host.name then pawn = host.pawn end
    for _, guest in ipairs(state.guests) do
        if guest.name == fields[3] and valid(guest.pawn) and guest.pawn:HasAuthority()
            and same(guest.pawn:GetWorld(), state.world) and same(guest.controller_object.Pawn, guest.pawn) then
            if pawn then error("The exact Black Stone fixture pawn is ambiguous", 0) end
            pawn = guest.pawn
        end
    end
    if not pawn then error("The Black Stone fixture pawn is unavailable", 0) end
    if action == "native-dream-stone-fixture" then
        if not same(pawn, host.pawn) then
            actor:SendInteractionToActor(pawn, actor.Box)
            local _, _, _, status = SilverfishReadNativeBlackStoneStatus()
            if status ~= "guest_or_unknown_denied" or not valid(placed(state.world)) then
                error("The guest Black Stone denial could not be verified", 0)
            end
            return "stock Black Stone guest use denied"
        end
        local ok, reason = SilverfishProbeNativeBlackStonePickup(pawn:GetAddress(), actor:GetAddress())
        if not ok then error("The stock Black Stone pickup was not verified (" .. tostring(reason) .. ")", 0) end
        if reason == "pickup_verifying" then return "stock Black Stone pickup awaiting native destruction" end
        stone.sync(state, phase)
        if not receipt then error("The stock Black Stone pickup receipt was not available", 0) end
        return "stock Black Stone pickup verified"
    end
    local position, rotation = actor.Box:K2_GetComponentLocation(), pawn:K2_GetActorRotation()
    for _, shift in ipairs({ { 140, 0 }, { -140, 0 }, { 0, 140 }, { 0, -140 } }) do
        for _, height in ipairs({ 60, 100, 140 }) do
            if pawn:K2_TeleportTo({ X = position.X + shift[1], Y = position.Y + shift[2], Z = position.Z + height },
                rotation) then
                pawn:ForceNetUpdate()
                return "stock Black Stone fixture position verified"
            end
        end
    end
    error("The stock Black Stone has no safe test position", 0)
end

return stone
