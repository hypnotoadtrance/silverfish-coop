local electrodes = {}
local retired = {}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function idle_components(actor)
    local sparks, loop = actor.NI_Sparks, actor.SparkLoop_Cue
    if valid(sparks) and valid(loop) and same(sparks:GetOwner(), actor) and same(loop:GetOwner(), actor) then
        return sparks, loop
    end
end

function electrodes.suspend(actor)
    local sparks, loop = idle_components(actor)
    if sparks then
        sparks:Deactivate()
        loop:Stop()
    end
end

function electrodes.clear()
    retired = {}
end

function electrodes.observe(entry, phase, row, point)
    local actor = entry.actor
    if entry.placed then entry.last_position = point end
    local sparks = idle_components(actor)
    local center = valid(actor.Sphere) and actor.Sphere:K2_GetComponentLocation() or point
    local armed = phase == "hosting" and actor.Triggered == false or phase == "connected" and entry.armed == true
    return row("native-electrode-phase", entry.placed or "-", point.X, point.Y, point.Z,
        armed and 1 or 0, sparks and sparks:IsActive() and 1 or 0, center.X, center.Y, center.Z)
end

function electrodes.retire(entry)
    if entry.placed and entry.last_position then retired[entry.placed] = entry.last_position end
end

function electrodes.retired_rows(row)
    local rows = {}
    for path, position in pairs(retired) do
        rows[#rows + 1] = row("native-electrode-phase", path, position.X, position.Y, position.Z, 2, 0,
            position.X, position.Y, position.Z)
    end
    return rows
end

local function passive(entry, world)
    local actor = entry.actor
    return entry.kind == "electrode" and valid(actor) and not actor:IsActorBeingDestroyed()
        and same(actor:GetWorld(), world) and not actor:GetActorEnableCollision()
end

local function find_electrode(active, world, path, x, y, z)
    if path ~= "-" then
        local actor = StaticFindObject(path)
        local entry = valid(actor) and active[actor:GetAddress()]
        if entry and entry.placed == path and passive(entry, world) then return entry end
        return
    end
    local match
    for _, entry in pairs(active) do
        if not entry.placed and passive(entry, world) then
            local point = entry.actor:K2_GetActorLocation()
            if (point.X - x)^2 + (point.Y - y)^2 + (point.Z - z)^2 < 9 then
                if match then return end
                match = entry
            end
        end
    end
    return match
end

function electrodes.command(args, state, phase, world, active)
    if args[2] ~= "native-electrode-apply-phase" then return end
    local visit = args[4]
    if phase ~= "connected" or state.map ~= args[3] or not same(state.world, world)
        or not visit or #visit ~= 32 or not visit:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Electrodes are outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, x, y, z, mode = args[index]:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([012])$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        if path and #path <= 512 and x and y and z then
            local entry = find_electrode(active, world, path, x, y, z)
            if entry and mode == "2" and entry.placed then
                entry.actor:K2_DestroyActor()
            elseif entry and mode ~= "2" and entry.armed ~= (mode == "1") then
                local sparks, loop = idle_components(entry.actor)
                if sparks then
                    if mode == "1" then
                        sparks:Activate(false)
                        loop:FadeIn(.5, 1, 0, 0)
                    else
                        sparks:Deactivate()
                        loop:FadeOut(.2, 0, 0)
                    end
                    entry.armed = mode == "1"
                end
            end
        end
    end
    return "Applied the host's Electrode phase"
end

return electrodes
