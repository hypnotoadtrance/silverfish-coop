local audio = {}
local guest_save = require("guest_save")
local creator = require("native_guest_creator")
local player = "/Game/Classes/Player/Player_FP.Player_FP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local attenuation_path = "/Game/Assets/Sounds/SoundClasses/Vocal_Attenuation.Vocal_Attenuation"
local stock_volume, voice_range = 2, 5030
local voices_path = "/Game/Classes/Libraries/DataTables/Player_Vox_DT.Player_Vox_DT"
local fields = {
    hurt = {"Vox_Hurt", "Hurt_12_75EF6B0846F3DB6C14A2B7A0B87D768B"},
    cough = {"Vox_Cough", "Cough_6_55773E784129D29653402E8321212748"},
    gasp = {"Vox_Gasp", "SurfaceGasp_4_233A78E142BD30C24559E695390DA09D"},
    drown = {"Vox_Drown", "Drowning_2_FFD0669A42978CDA00EC8FA8B8151978"},
    death = {"Vox_Death", "Death_16_D62EEA16439329F99DF0C9BB6AFF47B4"},
    stunned = {"Vox_Stunned", "Panic_14_5386B72945F3382BCE1023A37D4D1814"},
}
local current, hooked, events, profiles = nil, false, {}, {}
local world, counter, visit, seen, history = nil, 0, nil, {}, {}
local feedback_pawn

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function capture(context, sound)
    local pawn, cue = context:get(), sound:get()
    if same(pawn, feedback_pawn) then return end
    if not current or not valid(pawn) or not valid(cue) or not same(pawn:GetWorld(), current.state.world) then return end
    local name = pawn:GetFullName()
    if current.phase == "connected" then
        if not same(pawn, current.state.local_pawn and current.state.local_pawn.pawn) then return end
    elseif not profiles[name] then
        return
    end
    for kind, property in pairs(fields) do
        if same(pawn[property[1]], cue) then
            counter = counter + 1
            events[#events + 1] = {id = counter, kind = kind, pawn = name, expires = os.time() + 4}
            if #events > 12 then table.remove(events, 1) end
            return
        end
    end
end

function audio.local_feedback(pawn, play)
    feedback_pawn = pawn
    local ok, issue = pcall(play)
    feedback_pawn = nil
    if not ok then error(issue, 0) end
end

function audio.sync(state, phase)
    local active = (phase == "hosting" or phase == "connected") and valid(state.world)
    local address = active and state.world:GetAddress() or nil
    if world ~= address then
        world, visit, events, seen, history = address, nil, {}, {}, {}
    end
    profiles = {}
    current = active and {state = state, phase = phase} or nil
    if not current then return end
    if not hooked and valid(StaticFindObject(player .. ":Speak")) then
        RegisterHook(player .. ":Speak", function(context, cue) pcall(capture, context, cue) end)
        hooked = true
    end
    if phase == "hosting" then
        for _, entry in ipairs(state.pawns) do
            local pawn = entry.pawn
            if valid(pawn) and pawn:HasAuthority() then
                local save
                if pawn:IsLocallyControlled() then
                    save = pawn.Save
                else
                    local ok, _, detached = pcall(guest_save.exact_item_context, state, phase, entry.name)
                    if ok then save = detached end
                end
                if valid(save) then
                    local personality, pitch = creator.voice(save.Character)
                    if personality then
                        local emitter = pawn.Voice
                        local point = valid(emitter) and same(emitter:GetOwner(), pawn)
                            and emitter:K2_GetComponentLocation() or nil
                        profiles[entry.name] = point and {personality, pitch, point.X, point.Y, point.Z} or {personality, pitch}
                    end
                end
            end
        end
    end
end

function audio.rows(state, phase, row)
    if not current or current.phase ~= phase or not same(state.world, current.state.world) then return {} end
    local result = {}
    for pawn, info in pairs(profiles) do
        result[#result + 1] = row("native-voice-speaker", pawn, table.unpack(info))
    end
    local now = os.time()
    for _, entry in ipairs(events) do
        if entry.expires > now then result[#result + 1] = row("native-voice-event", entry.id, entry.kind, entry.pawn) end
    end
    return result
end

local function play(pawn, kind, personality, pitch, point)
    local voices = asset(voices_path)
    local selected = valid(voices) and voices:FindRow(personality)
    local cue = selected and selected[fields[kind][2]]
    local attenuation, statics = asset(attenuation_path), StaticFindObject(statics_path)
    if not valid(cue) or not cue:IsA(StaticFindObject("/Script/Engine.SoundBase"))
        or not valid(attenuation) or not valid(statics) then return false end
    statics:PlaySoundAtLocation(pawn, cue, point, {Pitch = 0, Yaw = 0, Roll = 0}, stock_volume, pitch, 0,
        attenuation, nil, pawn, nil)
    return true
end

function audio.command(args, state, phase)
    if args[2] == "native-voice-fixture" then
        local pawn = state.local_pawn and state.local_pawn.pawn
        local field = fields[args[3]]
        if #args ~= 3 or not field or not valid(pawn) or not pawn:IsLocallyControlled()
            or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
            or (phase ~= "hosting" and phase ~= "connected") then error("Voice fixture unavailable", 0) end
        pawn:Compile_VoxSetup()
        local cue = pawn[field[1]]
        if not valid(cue) then error("Voice fixture needs a stock selected cue", 0) end
        pawn:Speak(cue)
        return "stock voice call-out invoked"
    end
    if args[2] ~= "native-sound-voice" then return end
    local pawn, epoch = state.local_pawn and state.local_pawn.pawn, args[4]
    if (phase ~= "hosting" and phase ~= "connected") or not valid(pawn) or not pawn:IsLocallyControlled()
        or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$")
        or #args < 5 or #args > 16 then error("Voice playback scope is unavailable", 0) end
    if epoch ~= visit then visit, seen, history = epoch, {}, {} end
    local origin, played = pawn:K2_GetActorLocation(), {}
    for index = 5, #args do
        local id, kind, personality, pitch, x, y, z = args[index]:match(
            "^([0-9a-f]+)|([a-z]+)|([%w_]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        pitch, x, y, z = tonumber(pitch), tonumber(x), tonumber(y), tonumber(z)
        if id and #id == 32 and not seen[id] and fields[kind] and #personality <= 30 and pitch and pitch > 0 and pitch <= 3
            and x and y and z and (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 <= voice_range^2 then
            local ok, heard = pcall(play, pawn, kind, personality, pitch, {X = x, Y = y, Z = z})
            if ok and heard then
                played[#played + 1], history[#history + 1], seen[id] = id, id, true
                if #history > 32 then seen[table.remove(history, 1)] = nil end
            end
        end
    end
    return table.concat(played, ",")
end

return audio
