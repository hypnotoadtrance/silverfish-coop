local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-footstep-"
local map = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local statics = "/Script/Engine.GameplayStatics:"
local footsteps_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.FL_Characters_C:PlayFootStep"
local concurrency_path = "/Game/Assets/Sounds/SoundClasses/SoundCon_FootSteps.SoundCon_FootSteps"
local attenuation_path = "/Game/Assets/Sounds/SoundClasses/Player_FootStep_Attenuation.Player_FootStep_Attenuation"
local local_pawn, peer, attenuation, concurrency, last, hook_failure
local originals, replays = {}, {}
local hooked = false
local stepping, stock_calls, spawn_calls

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function select_players()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == map, "stock Observatory unavailable")
    local local_owner, remote_owner
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and same(pawn:GetWorld(), world) and not pawn:IsActorBeingDestroyed() then
            if pawn:IsLocallyControlled() then
                assert(not local_owner, "duplicate local player")
                local_owner = pawn
            else
                assert(not remote_owner, "duplicate remote player")
                remote_owner = pawn
            end
        end
    end
    assert(local_owner and remote_owner, "both stock players must be present")
    return local_owner, remote_owner
end

local function capture_original(_, sound, component, _, _, _, _, _stop_with_owner, volume, pitch, _, radius, group)
    if stepping then spawn_calls = spawn_calls + 1 end
    if not same(group:get(), concurrency) or not same(component:get(), local_pawn.CapsuleComponent)
        or not same(radius:get(), attenuation) then return end
    local cue = sound:get()
    if valid(cue) then
        originals[#originals + 1] = string.format("%s,%.3f,%.3f", cue:GetFullName(), volume:get(), pitch:get())
    end
end

local function capture_replay(_, context, sound, location, _, volume, pitch, _, radius, group, owner)
    if not same(group:get(), concurrency) or not same(context:get(), local_pawn) or not same(radius:get(), attenuation) then return end
    local cue, emitter = sound:get(), owner:get()
    if valid(cue) and valid(emitter) then
        assert(same(emitter, peer), "footstep replay belongs to the wrong player")
        local capsule, point = emitter.CapsuleComponent, location:get()
        local center = capsule:K2_GetComponentLocation()
        local height = capsule:GetScaledCapsuleHalfHeight()
        local offset = math.sqrt((center.X - point.X)^2 + (center.Y - point.Y)^2 + (center.Z - height - point.Z)^2)
        local listener = local_pawn:K2_GetActorLocation()
        local distance = math.sqrt((listener.X - point.X)^2 + (listener.Y - point.Y)^2 + (listener.Z - point.Z)^2)
        replays[#replays + 1] = string.format("%s,%.3f,%.3f,%.3f,%.3f", cue:GetFullName(), volume:get(), pitch:get(), offset, distance)
    end
end

local function guard(callback)
    return function(...)
        local ok, error_message = pcall(callback, ...)
        if not ok then hook_failure = error_message end
    end
end

local function run(action)
    if action == "arm" then
        local_pawn, peer = select_players()
        attenuation, concurrency = StaticFindObject(attenuation_path), StaticFindObject(concurrency_path)
        assert(valid(attenuation) and valid(concurrency), "stock footstep sound settings unavailable")
        if not hooked then
            RegisterHook(statics .. "SpawnSoundAttached", guard(capture_original))
            RegisterHook(statics .. "PlaySoundAtLocation", guard(capture_replay))
            RegisterHook(footsteps_library, function() if stepping then stock_calls = stock_calls + 1 end end)
            hooked = true
        end
        local_pawn:Crouch(false)
        return "armed"
    elseif action == "ready" then
        return tostring(local_pawn.bIsCrouched == true)
    elseif action == "step" then
        assert(local_pawn.bIsCrouched == true, "stock crouch has not applied")
        stepping, stock_calls, spawn_calls = true, 0, 0
        local_pawn:PlayFootStep(false)
        stepping = false
        assert(not hook_failure, hook_failure)
        assert(#originals > 0, ("stock footstep emitted no matched audio (%d library calls, %d SpawnSoundAttached calls)")
            :format(stock_calls, spawn_calls))
        return table.concat(originals, "|")
    elseif action == "clear" then
        originals, replays, hook_failure = {}, {}, nil
        return "clear"
    elseif action == "read" then
        assert(not hook_failure, hook_failure)
        return table.concat(originals, "|") .. ";" .. table.concat(replays, "|")
    end
    error("unknown footstep probe action")
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(96)
    request:close()
    if not data then return end
    local ticket, action = data:match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
