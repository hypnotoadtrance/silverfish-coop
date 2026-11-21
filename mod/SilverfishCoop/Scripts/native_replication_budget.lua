local budget = {}

local player_hz = 24
local saved = {}

local function valid(object)
    return object and object:IsValid() and not object:IsActorBeingDestroyed()
end

function budget.sync(state, phase)
    local active = {}
    if phase == "hosting" and state.listening and not state.server then
        for _, details in ipairs(state.pawns) do
            local pawn = details.pawn
            if valid(pawn) and pawn:HasAuthority() and pawn.bReplicates then
                local address = pawn:GetAddress()
                local frequency = pawn.NetUpdateFrequency
                local entry = saved[address]
                if not entry or not valid(entry.pawn) or entry.name ~= details.name then
                    entry = {pawn = pawn, name = details.name, frequency = frequency}
                    saved[address] = entry
                end
                if type(frequency) == "number" and frequency > player_hz then
                    pawn.NetUpdateFrequency = player_hz
                end
                active[address] = true
            end
        end
    end
    for address, entry in pairs(saved) do
        if not active[address] then
            if valid(entry.pawn) and type(entry.frequency) == "number" and entry.pawn.NetUpdateFrequency == player_hz then
                entry.pawn.NetUpdateFrequency = entry.frequency
            end
            saved[address] = nil
        end
    end
end

function budget.rows(row)
    local rows = {}
    for _, entry in pairs(saved) do
        if valid(entry.pawn) and type(entry.frequency) == "number" then
            rows[#rows + 1] = row("native-replication-rate", entry.name, entry.frequency, entry.pawn.NetUpdateFrequency)
        end
    end
    return rows
end

return budget
