local fire = {}
local child_class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Molotov.Fire_Molotov_C"
local particle_path = "/Game/Assets/PFX/NI_FireMolotov.NI_FireMolotov"
local max_fields, max_particles, max_field_bytes = 4, 64, 6400
local chunk_bytes, max_chunks = 960, 27
local world, phase, host, guest, serial

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function statics() return StaticFindObject("/Script/Engine.Default__GameplayStatics") end

local function remove(component, immediate)
    if valid(component) then
        if immediate then
            local owner = component:GetOwner()
            component:K2_DestroyComponent(valid(owner) and owner or nil)
        else component:Deactivate() end
    end
end

function fire.clear()
    if guest then
        for _, item in pairs(guest.rendered) do remove(item.component, true) end
    end
    world, phase, host, guest = nil, nil, nil, nil
end

local function enter(current, seat)
    if same(world, current) and phase == seat then return end
    fire.clear()
    world, phase = current, seat
    host = {fields = {}, content = "", rows = {}, revision = 0, next_sample = 0}
    guest = {rendered = {}, rendered_count = 0, jobs = {}, next_job = 1, revision = 0}
end

local function number(value, minimum, maximum)
    return type(value) == "number" and value >= minimum and value <= maximum
end

local function transform(component)
    local p, r, s = component:K2_GetComponentLocation(), component:K2_GetComponentRotation(), component:K2_GetComponentScale()
    if not (number(p.X, -1e7, 1e7) and number(p.Y, -1e7, 1e7) and number(p.Z, -1e7, 1e7)
        and number(r.Pitch, -360, 360) and number(r.Yaw, -360, 360) and number(r.Roll, -360, 360)
        and number(s.X, .001, 10) and number(s.Y, .001, 10) and number(s.Z, .001, 10)) then return end
    return ("%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f"):format(
        p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll, s.X, s.Y, s.Z)
end

local function admit()
    local added = false
    for _ = 1, max_fields do
        local path = SilverfishTakeNativeDrumFire(world:GetAddress())
        if not path then break end
        local actor = StaticFindObject(path)
        if #host.fields < max_fields and valid(actor) and same(actor:GetWorld(), world) and actor:HasAuthority()
            and actor:GetClass():GetFullName() == child_class and not actor:IsActorBeingDestroyed() then
            serial = (serial or 0) + 1
            local origin = actor:K2_GetActorLocation()
            if number(origin.X, -1e7, 1e7) and number(origin.Y, -1e7, 1e7) then
                host.fields[#host.fields + 1] = {actor = actor, id = serial,
                    center = ("%.3f,%.3f"):format(origin.X, origin.Y), ids = {}, next_id = 0}
                added = true
            end
        end
    end
    return added
end

local function sample(entry)
    local actor = entry.actor
    local particles = actor.Particles
    local count = particles and particles:GetArrayNum() or 0
    if count == 0 or count > max_particles then return end
    local parts = {}
    for index = 1, count do
        local object = particles[index]
        if valid(object) and same(object:GetOwner(), actor) and valid(object.Asset)
            and object.Asset:GetFullName() == "NiagaraSystem " .. particle_path then
            local at = transform(object)
            local address = object:GetAddress()
            local id = entry.ids[address]
            if at and not id and entry.next_id < max_particles then
                entry.next_id = entry.next_id + 1
                id = entry.next_id
                entry.ids[address] = id
            end
            if at and id then parts[#parts + 1] = id .. "," .. at end
        end
    end
    if #parts > 0 then return ("%d|%s|%s"):format(entry.id, entry.center, table.concat(parts, ";")) end
end

local function host_rows(row)
    local removed = false
    for index = #host.fields, 1, -1 do
        local actor = host.fields[index].actor
        if not valid(actor) or actor:IsActorBeingDestroyed() then
            table.remove(host.fields, index)
            removed = true
        end
    end
    local admitted = admit()
    if #host.fields == 0 and not removed then return host.rows end
    local now = os.clock()
    if not admitted and not removed and now < host.next_sample then return host.rows end
    host.next_sample = now + .10
    local fields = {}
    for index = #host.fields, 1, -1 do
        local encoded = sample(host.fields[index])
        if encoded then fields[#fields + 1] = encoded end
    end
    local content = table.concat(fields, "\t")
    if content ~= host.content then
        host.content = content
        host.revision = host.revision + 1
        host.rows = {row("native-drum-fire-host", host.revision, table.unpack(fields))}
    end
    return host.rows
end

local function values(encoded, width)
    local result = {}
    for value in (encoded .. ","):gmatch("(.-),") do
        if #result == width or #value > 15 or not value:match("^%-?%d+%.?%d*$") then return end
        result[#result + 1] = tonumber(value)
    end
    if #result == width then return result end
end

local function decode(args)
    local wanted, fields, count, field_count = {}, {}, 0, 0
    if #args == 5 then return wanted, count end
    for index = 6, #args do
        if #args[index] == 0 or #args[index] > chunk_bytes then return end
    end
    local content = table.concat(args, "", 6)
    for value in (content .. "~"):gmatch("(.-)~") do
        field_count = field_count + 1
        if field_count > max_fields or #value > max_field_bytes then return end
        local id, center, parts = value:match("^(%d+)|([^|]+)|([^|]+)$")
        local xy = center and values(center, 2)
        id = id and math.tointeger(id)
        if not id or not number(id, 1, 2147483647) or not xy or fields[id]
            or not number(xy[1], -1e7, 1e7) or not number(xy[2], -1e7, 1e7) then return end
        fields[id] = true
        local particle_count = 0
        for part in (parts .. ";"):gmatch("(.-);") do
            local v = values(part, 10)
            particle_count = particle_count + 1
            if particle_count > max_particles or not v or not math.tointeger(v[1]) or not number(v[1], 1, max_particles)
                or not (number(v[2], -1e7, 1e7) and number(v[3], -1e7, 1e7) and number(v[4], -1e7, 1e7)
                    and number(v[5], -360, 360) and number(v[6], -360, 360) and number(v[7], -360, 360)
                    and number(v[8], .001, 10) and number(v[9], .001, 10) and number(v[10], .001, 10)) then return end
            local key = id * 128 + v[1]
            if wanted[key] then return end
            wanted[key] = {pose = part, center = {X = xy[1], Y = xy[2]}, location = {X = v[2], Y = v[3], Z = v[4]},
                rotation = {Pitch = v[5], Yaw = v[6], Roll = v[7]}, scale = {X = v[8], Y = v[9], Z = v[10]}}
            count = count + 1
        end
    end
    return wanted, count
end

local function schedule(wanted, count)
    local jobs = {}
    for key in pairs(guest.rendered) do
        if not wanted[key] then jobs[#jobs + 1] = {key = key} end
    end
    for key, item in pairs(wanted) do
        local current = guest.rendered[key]
        if not current or not valid(current.component) or current.pose ~= item.pose
            or current.center.X ~= item.center.X or current.center.Y ~= item.center.Y then
            jobs[#jobs + 1] = {key = key, item = item}
        end
    end
    guest.jobs, guest.next_job, guest.desired = jobs, 1, count
end

function fire.command(args, state, seat)
    if args[2] ~= "native-drum-fire-view" then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    local epoch, revision = args[4], math.tointeger(args[5])
    if seat ~= "connected" or state.server ~= true or not state.listening or not valid(state.world)
        or not valid(pawn) or not pawn:IsLocallyControlled() or not same(pawn:GetWorld(), state.world)
        or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$")
        or not number(revision, 1, 2147483647) or #args < 5 or #args > 5 + max_chunks then
        error("Ground fire view is outside the current guest visit", 0)
    end
    local wanted, count = decode(args)
    if not wanted then error("Invalid ground fire view", 0) end
    enter(state.world, seat)
    if guest.epoch ~= epoch then
        fire.clear()
        enter(state.world, seat)
        guest.epoch = epoch
    end
    if revision > guest.revision then
        schedule(wanted, count)
        guest.revision = revision
        guest.expires = count > 0 and statics():GetTimeSeconds(world) + 18 or nil
    end
    return "Accepted stock drum fire view"
end

local function render(job, pawn)
    local current, item = guest.rendered[job.key], job.item
    if not item then
        if current then
            remove(current.component, false)
            guest.rendered_count = guest.rendered_count - 1
            guest.rendered[job.key] = nil
        end
        return
    end
    local component = current and current.component
    if valid(component) then
        component:K2_SetWorldLocationAndRotation(item.location, item.rotation, false, {}, true)
        component:K2_SetWorldScale3D(item.scale)
        component:SetVariableVec2(FName("User.Center"), item.center)
    else
        if not guest.asset_checked then
            guest.asset_checked = true
            guest.asset = StaticFindObject(particle_path)
            if not valid(guest.asset) then LoadAsset(particle_path); guest.asset = StaticFindObject(particle_path) end
        end
        local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
        if valid(guest.asset) and valid(niagara) then
            component = niagara:SpawnSystemAtLocation(pawn, guest.asset, item.location, item.rotation, item.scale,
                true, false, 0, false)
            if valid(component) then
                component:SetVariableVec2(FName("User.Center"), item.center)
                component:Activate(false)
            end
        end
    end
    if valid(component) then
        if not current then guest.rendered_count = guest.rendered_count + 1 end
        guest.rendered[job.key] = {component = component, pose = item.pose, center = item.center}
    elseif current then
        guest.rendered_count = guest.rendered_count - 1
        guest.rendered[job.key] = nil
    end
end

function fire.rows(state, seat, row)
    if not valid(state.world) or (seat ~= "hosting" and seat ~= "connected") then fire.clear(); return {} end
    enter(state.world, seat)
    if seat == "hosting" then
        if type(SilverfishTakeNativeDrumFire) ~= "function" then return {} end
        return host_rows(row)
    end
    if not guest.epoch then return {} end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if guest.expires and statics():GetTimeSeconds(world) >= guest.expires then
        schedule({}, 0)
        guest.expires = nil
    end
    if valid(pawn) and pawn:IsLocallyControlled() then
        for index = guest.next_job, math.min(guest.next_job + 3, #guest.jobs) do
            render(guest.jobs[index], pawn)
            guest.next_job = index + 1
        end
    end
    return {row("native-drum-fire-view", guest.epoch, guest.revision, guest.desired, guest.rendered_count)}
end

return fire
