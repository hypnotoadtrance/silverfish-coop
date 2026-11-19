local guest_save = require("guest_save")
local aim = require("native_flood_jammer_aim_fixture")

local lenny = {}
local area = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
local level = area .. ".RailYard_P:PersistentLevel."
local spawner_path = level .. "Quest_AssassinTargetSpawn_BP_C_1"
local spawner_name = "Quest_AssassinTargetSpawn_BP_C " .. spawner_path
local target_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Lenny.NPC_Lenny_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local observing = false

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end

local function world(state)
    return enabled and state.map == area and valid(state.world) and
        state.world:GetFullName() == "World " .. area .. ".RailYard_P"
end

local function read(state, phase)
    local local_pawn = state.local_pawn and state.local_pawn.pawn
    if not world(state) or phase ~= "hosting" or state.listening ~= true or state.server ~= false
        or not valid(local_pawn) or not valid(local_pawn.Save) or type(SilverfishWatchLennyQuest) ~= "function" then
        if observing and type(SilverfishClearLennyQuest) == "function" then SilverfishClearLennyQuest() end
        observing = false
        return
    end
    local remotes = {}
    for _, detail in ipairs(state.pawns) do
        if detail.name ~= state.local_pawn.name then
            local verified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, detail.name)
            if verified and valid(pawn) and valid(detached) then
                remotes[#remotes + 1], remotes[#remotes + 2] = pawn:GetAddress(), detached:GetAddress()
            end
        end
    end
    observing = true
    return SilverfishWatchLennyQuest(local_pawn:GetAddress(), local_pawn.Save:GetAddress(), table.unpack(remotes))
end

function lenny.command(fields, state, phase)
    if fields[2] ~= "native-lenny-aim" then return end
    if #fields ~= 4 then error("stock_lenny_aim_arguments_unqualified", 0) end
    local status, name, replicated = read(state, phase)
    if status ~= "watching" or replicated ~= true or type(name) ~= "string" then error("stock_lenny_aim_native_target_unavailable", 0) end
    local spawner = StaticFindObject(spawner_path)
    local npc = valid(spawner) and spawner.TargetNPC
    if not valid(npc) or npc:GetFullName() ~= name or not same(npc:GetClass(), StaticFindObject(target_class))
        or npc.Alive ~= true then error("stock_lenny_aim_exact_living_target_required", 0) end
    return aim.aim_target(state, phase, fields[3], fields[4], "native-lenny", npc, npc.CapsuleComponent, npc.Mesh)
end

function lenny.rows(state, phase, status_row)
    local status, target, replicated, before, after, flag_before, flag_after, xp_before, xp_after, role, shooter, shot, hp = read(state, phase)
    if status then
        return {status_row("native-lenny-objective", area, spawner_name, target or "-", status,
            replicated == true and 1 or 0, before or "-", after or "-", flag_before or "-", flag_after or "-",
            xp_before or "-", xp_after or "-", role or "-", shooter or "-", shot or "-", hp or "-")}
    end
    if not world(state) or phase ~= "connected" or state.listening ~= true or state.server ~= true then return {} end
    local class = StaticFindObject(target_class)
    if not valid(class) then return {} end
    local authority, simulated = 0, 0
    for _, npc in ipairs(FindAllOf("NPC_Lenny_C") or {}) do
        if valid(npc) and same(npc:GetClass(), class) and same(npc:GetWorld(), state.world) and not npc:IsActorBeingDestroyed() then
            if npc:HasAuthority() then authority = authority + 1
            else simulated = simulated + 1 end
        end
    end
    return {status_row("native-lenny-client", area, spawner_name, authority, simulated)}
end

return lenny
