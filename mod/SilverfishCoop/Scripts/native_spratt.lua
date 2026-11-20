local guest_save = require("guest_save")
local spratt = {}

local area = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world_name = "World " .. area .. ".Cistern_P"
local spawner_name = "Quest_AssassinTargetSpawn_BP_C " .. area .. ".Cistern_P:PersistentLevel.Quest_AssassinTargetSpawn_BP_C_0"
local target_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Spratt.NPC_Spratt_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local observing = false

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end

function spratt.rows(state, phase, row)
    local current = enabled and state.map == area and valid(state.world) and state.world:GetFullName() == world_name
        and state.listening == true
    local host = state.local_pawn and state.local_pawn.pawn
    if current and phase == "hosting" and state.server == false and valid(host) and valid(host.Save)
        and type(SilverfishWatchSprattQuest) == "function" then
        local remotes = {}
        for _, detail in ipairs(state.pawns) do
            if detail.name ~= state.local_pawn.name then
                local ok, pawn, save = pcall(guest_save.exact_item_context, state, phase, detail.name)
                if ok and valid(pawn) and valid(save) then
                    remotes[#remotes + 1], remotes[#remotes + 2] = pawn:GetAddress(), save:GetAddress()
                end
            end
        end
        observing = true
        local state_name, target, replicated, before, after, _, _, xp_before, xp_after, role, shooter, shot, hp,
            cash_before, cash_after, callbacks =
            SilverfishWatchSprattQuest(host:GetAddress(), host.Save:GetAddress(), table.unpack(remotes))
        if state_name then
            return {row("native-spratt-objective", area, spawner_name, target or "-", state_name, replicated == true and 1 or 0,
                before or "-", after or "-", xp_before or "-", xp_after or "-", cash_before or "-", cash_after or "-",
                role or "-", shooter or "-", shot or "-", hp or "-", callbacks or "-")}
        end
    end
    if observing and type(SilverfishClearSprattQuest) == "function" then SilverfishClearSprattQuest() end
    observing = false
    if not current or phase ~= "connected" or state.server ~= true then return {} end
    local class = StaticFindObject(target_class)
    if not valid(class) then return {} end
    local authority, simulated = 0, 0
    for _, pawn in ipairs(FindAllOf("NPC_Spratt_C") or {}) do
        if valid(pawn) and same(pawn:GetClass(), class) and same(pawn:GetWorld(), state.world) and not pawn:IsActorBeingDestroyed() then
            if pawn:HasAuthority() then authority = authority + 1
            else simulated = simulated + 1 end
        end
    end
    return {row("native-spratt-client", area, spawner_name, authority, simulated)}
end

return spratt
