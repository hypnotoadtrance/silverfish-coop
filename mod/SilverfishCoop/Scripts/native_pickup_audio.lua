local audio = {}
local pickup_base = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local attenuation_path = "/Game/Assets/Sounds/SoundClasses/SmallSound_Attenuation.SmallSound_Attenuation"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local zero_rotation = { Pitch = 0, Yaw = 0, Roll = 0 }
local world, visit, seen, history

local function valid(object)
    return object and object:IsValid()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function token(text)
    return text and #text == 32 and text:match("^[0-9a-f]+$")
end

local function cue_at(path, pawn, point)
    local class = asset(path)
    local item = valid(class) and class:GetCDO()
    local base = asset(pickup_base)
    if not valid(item) or not valid(base) or not item:IsA(base) then return false end
    local sound = item.Sound_Inventory
    if not valid(sound) or not sound:IsA(StaticFindObject("/Script/Engine.SoundBase")) then return false end
    local attenuation = asset(attenuation_path)
    local statics = StaticFindObject(statics_path)
    if not valid(attenuation) or not valid(statics) then return false end
    statics:PlaySoundAtLocation(pawn, sound, point, zero_rotation, 1, 1, 0, attenuation, nil, pawn, nil)
    return true
end

function audio.command(fields, state, phase)
    if fields[2] ~= "native-sound-pickup" then return end
    local epoch = fields[4]
    local pawn = state.local_pawn and state.local_pawn.pawn
    if (phase ~= "connected" and phase ~= "hosting") or not valid(pawn) or not pawn:IsLocallyControlled()
        or not valid(state.world) or state.map ~= fields[3] or not token(epoch) or #fields < 5 or #fields > 20 then
        error("Pickup sound scope is unavailable", 0)
    end
    if world ~= state.world:GetAddress() or visit ~= epoch then
        world, visit, seen, history = state.world:GetAddress(), epoch, {}, {}
    end
    local heard, origin = {}, pawn:K2_GetActorLocation()
    for i = 5, #fields do
        local id, path, x, y, z = fields[i]:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local package, export
        if path and #path <= 256 then package, export = path:match("^(/Game/Classes/[%w_/%-]+)%.([%w_%-]+)_C$") end
        if token(id) and not seen[id] and package and package:match("([^/]+)$") == export and x and y and z
            and (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 <= 1800^2 then
            local ok, played = pcall(cue_at, path, pawn, { X = x, Y = y, Z = z })
            if ok and played then
                seen[id] = true
                history[#history + 1] = id
                if #history > 32 then seen[table.remove(history, 1)] = nil end
                heard[#heard + 1] = id
            end
        end
    end
    return table.concat(heard, ",")
end

return audio
