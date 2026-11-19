local campaign_npc = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local active

local function valid(value)
    return value and value:IsValid()
end

function campaign_npc.clear()
    active = nil
    if enabled and type(SilverfishClearCampaignNpc) == "function" then SilverfishClearCampaignNpc() end
end

function campaign_npc.rows(state, phase, status_row)
    if not enabled then return {} end
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local pawn = state.local_pawn and state.local_pawn.pawn
    if state.map ~= map or not state.listening or not (host or client) or not valid(state.world) or not valid(pawn) then
        -- During initial load a native receipt can precede the first bridge poll.
        -- Only clear state after leaving a world that the bridge observed.
        if active then campaign_npc.clear() end
        return {}
    end
    if type(SilverfishReadCampaignNpc) ~= "function" then return {} end
    local receipts, stage, spawner, stock_data, cohort, replicated, spawn_denials, ai_denials, natural_calls,
        authority, simulated, other, simulated_controllers, antfarm, roster =
        SilverfishReadCampaignNpc(pawn:GetAddress(), host)
    if type(receipts) ~= "number" then return {} end
    active = state.world:GetAddress()
    local viewer = host and "host" or "client"
    local rows = {
        status_row("native-campaign-npc", state.map, viewer, receipts, stage, spawner ~= "" and spawner or "-",
            stock_data ~= "" and stock_data or "-", cohort, replicated, spawn_denials),
        status_row("native-campaign-npc-census", state.map, viewer, natural_calls, authority, simulated, other,
            simulated_controllers, ai_denials, antfarm),
    }
    for actor in roster:gmatch("[^\n]+") do
        rows[#rows + 1] = status_row("native-campaign-npc-member", state.map, viewer, actor)
    end
    return rows
end

return campaign_npc
