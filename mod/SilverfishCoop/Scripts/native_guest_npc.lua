local npc = {}
local transport = require("native_transport")

local map = "/Game/Classes/Maps/PlaytestMap_01"
local world_name = "World " .. map .. ".PlaytestMap_01"
local source_name = "AI_Spawner_Single_BP_C " .. map .. ".PlaytestMap_01:PersistentLevel.AI_Spawner_Single_BP_C_4"
local bandit_class = "BlueprintGeneratedClass /Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_NPC_TEST") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local hostile = os.getenv("SILVERFISH_COOP_NATIVE_NPC_HOSTILE") == "1"

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function active(state, phase)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    if not enabled or state.map ~= map or not valid(state.world) or state.world:GetFullName() ~= world_name
        or not valid(pawn) or not same(pawn:GetWorld(), state.world) or detail.name ~= pawn:GetFullName()
        or not valid(detail.controller_object) or not detail.controller_object:IsLocalController()
        or not same(detail.controller_object.Pawn, pawn) or phase ~= "hosting" and phase ~= "connected" then return end
    return pawn
end

local function authority_guest(state, phase)
    if phase ~= "hosting" or #state.guests ~= 1 then return end
    local detail = state.guests[1]
    local pawn, controller = detail.pawn, detail.controller_object
    if not valid(pawn) or detail.name ~= pawn:GetFullName() or not pawn:HasAuthority()
        or not same(pawn:GetWorld(), state.world) or not valid(controller) or controller:IsLocalController()
        or not same(controller.Pawn, pawn) or not same(controller:GetWorld(), state.world) then return end
    return pawn
end

local function source_in(world)
    local source
    for _, candidate in ipairs(FindAllOf("AI_Spawner_Single_BP_C") or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and candidate:GetFullName() == source_name then
            if source then return end
            source = candidate
        end
    end
    local arrow, class = valid(source) and source.Arrow, valid(source) and source.AI_Class
    if not valid(arrow) or not valid(class) or not same(arrow:GetOwner(), source) or class:GetFullName() ~= bandit_class then return end
    return source, arrow
end

local function read(pawn, watched)
    return table.pack(SilverfishReadNativeBandit(pawn:GetAddress(), watched and watched:GetAddress() or 0))
end

function npc.rows(state, phase, status_row)
    local pawn = active(state, phase)
    if not pawn or type(SilverfishReadNativeBandit) ~= "function" then return {} end
    local watched = authority_guest(state, phase)
    local report = read(pawn, watched)
    if report.n ~= 24 then return {} end
    if phase == "hosting" and hostile and type(SilverfishReadNativeBanditCombat) == "function" then
        local combat = table.pack(SilverfishReadNativeBanditCombat(pawn:GetAddress(), watched and watched:GetAddress() or 0))
        if combat.n == 8 then
            for index = 1, combat.n do report[report.n + index] = combat[index] end
            report.n = report.n + combat.n
        end
    end
    for index = 1, report.n do
        if type(report[index]) == "boolean" then report[index] = report[index] and 1 or 0 end
        if report[index] == "" then report[index] = "-" end
    end
    return { status_row("native-bandit", map, phase, pawn:GetFullName(), watched and watched:GetFullName() or "-",
        table.unpack(report, 1, report.n)) }
end

local function place(pawn, point, rotation, forward_x, forward_y, offset)
    local dx, dy = forward_x * offset, forward_y * offset
    for _, height in ipairs({ 90, 130, 170 }) do
        if pawn:K2_TeleportTo({ X = point.X + dx, Y = point.Y + dy, Z = point.Z + height }, rotation) then
            pawn:ForceNetUpdate()
            return true
        end
    end
    return false
end

function npc.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-bandit-position" and action ~= "native-bandit-spawn" and action ~= "native-bandit-client-spawner-probe" then return end
    local pawn = active(state, phase)
    if not pawn or type(SilverfishReadNativeBandit) ~= "function" then error("The staged Playtest Bandit scope is unavailable", 0) end
    local source, arrow = source_in(state.world)
    if not source then error("The exact authored Bandit spawner is unavailable", 0) end

    if action == "native-bandit-client-spawner-probe" then
        if phase ~= "connected" or #fields ~= 2 then error("The Bandit spawner denial probe requires a staged client", 0) end
        local before = read(pawn)
        if not before[1] or before[3] ~= 0 or before[4] ~= 0 then error("The client Bandit baseline is unavailable", 0) end
        source:Send_PowerSignal(true, pawn)
        local after = read(pawn)
        if not after[1] or after[3] ~= 0 or after[4] ~= 0 or after[23] <= before[23] then
            error("The stock client spawner denial was not verified", 0)
        end
        return "stock client Bandit spawner denied"
    end

    local host, fresh = transport.scoped_host(state, phase)
    local guest = authority_guest(state, phase)
    if not fixtures or not host or not fresh or not same(host.pawn, pawn) or not guest or not source:HasAuthority() then
        error("The stock Bandit fixture requires an exact paired listen host", 0)
    end
    local before = read(pawn, guest)
    if not before[1] or before[2] ~= "waiting_for_stock_bandit" then error("The stock Bandit fixture requires an empty baseline", 0) end

    if action == "native-bandit-spawn" then
        if #fields ~= 2 or type(SilverfishSpawnNativeBandit) ~= "function" then error("The native Bandit spawn is unavailable", 0) end
        local ok, status, name = SilverfishSpawnNativeBandit(pawn:GetAddress(), source:GetAddress())
        if not ok then error("The stock Bandit spawn was not verified (" .. tostring(status) .. ")", 0) end
        return "stock Bandit spawned " .. name
    end

    if #fields ~= 3 or fields[3] ~= "host" and fields[3] ~= "guest" then error("Expose the exact host or guest to the stock Bandit", 0) end
    local exposed = fields[3] == "host" and pawn or guest
    local covered = same(exposed, pawn) and guest or pawn
    local origin, direction = arrow:K2_GetComponentLocation(), arrow:K2_GetComponentRotation()
    local yaw = math.rad(direction.Yaw)
    local forward_x, forward_y = math.cos(yaw), math.sin(yaw)
    local original_location, original_rotation = covered:K2_GetActorLocation(), covered:K2_GetActorRotation()
    if not place(covered, origin, original_rotation, forward_x, forward_y, -1300) then
        error("The rear Bandit fixture station could not be reached", 0)
    end
    if not place(exposed, origin, exposed:K2_GetActorRotation(), forward_x, forward_y, hostile and 480 or 850) then
        covered:K2_TeleportTo(original_location, original_rotation)
        covered:ForceNetUpdate()
        error("The front Bandit fixture station could not be reached", 0)
    end
    return "staged Bandit position for " .. fields[3]
end

return npc
