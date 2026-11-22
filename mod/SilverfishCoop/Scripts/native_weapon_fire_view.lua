local view = {}

local weapon_base = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local attenuation_path = "/Game/Assets/Sounds/SoundClasses/Weapon_Attenuation.Weapon_Attenuation"
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local maximum, range = 36, 30000
local current, hooked, visit, last_played
local serial, published, pending = 0, 0, {}

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

local function stock_weapon(path)
    if type(path) ~= "string" or #path > 160 then return false end
    local package, class = path:match("^(/Game/Classes/Weapons/[%w_/%-]+)%.([%w_%-]+)_C$")
    return package and package:match("([%w_%-]+)$") == class
end

local function bounded(n, minimum, maximum)
    return type(n) == "number" and n >= minimum and n <= maximum
end

local function capture(context)
    if not current then return end
    local weapon = context:get()
    if not valid(weapon) or not weapon:HasAuthority() or not same(weapon:GetWorld(), current.world) then return end
    local pawn, muzzle = weapon.User, weapon.Arrow_Muzzle
    local pawn_class = StaticFindObject("/Game/Classes/Player/Player_FP.Player_FP_C")
    if not valid(pawn) or not valid(pawn_class) or not pawn:IsA(pawn_class) or not pawn:HasAuthority()
        or not same(pawn.EquipedItem, weapon) or not valid(muzzle) or not same(muzzle:GetOwner(), weapon) then return end
    local path = weapon:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
    if not stock_weapon(path) then return end
    local position, rotation = muzzle:K2_GetComponentLocation(), muzzle:K2_GetComponentRotation()
    local scale, volume, pitch = weapon.MuzzleFlash_Scale, weapon.Fire_Volume, weapon.Fire_PitchShift
    if not bounded(scale, 0, 10) or not bounded(volume, 0, 10) or not bounded(pitch, -24, 24) then return end
    serial = serial + 1
    pending[#pending + 1] = {serial, path, position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw,
        rotation.Roll, scale, volume, pitch - 2, expires = current.statics:GetRealTimeSeconds(pawn) + .25}
    if #pending > maximum then table.remove(pending, 1) end
end

function view.sync(state, phase)
    if phase ~= "hosting" or not valid(state.world) then
        current, pending = nil, {}
        return
    end
    if not current or not same(current.world, state.world) then
        current = {world = state.world, map = state.map, statics = StaticFindObject(statics_path)}
        pending = {}
    end
    local path = weapon_base .. ":SpawnProjectile"
    if not hooked and valid(StaticFindObject(path)) then
        -- The stock firearm calls this once, after CheckAmmoCost succeeds, even for a multi-pellet shot.
        RegisterHook(path, function(context) pcall(capture, context) end)
        hooked = true
    end
end

function view.flush(get_world)
    if not current or not bridge or published == serial or #pending == 0 then return end
    local world = get_world()
    if not same(world, current.world) then return end
    local now, rows = current.statics:GetRealTimeSeconds(world), {current.map}
    while pending[1] and pending[1].expires <= now do table.remove(pending, 1) end
    if #pending == 0 then return end
    for _, entry in ipairs(pending) do rows[#rows + 1] = table.concat(entry, "|") end
    local target = bridge .. "/native-weapon-shots"
    local temporary = target .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return end
    local written = file:write(table.concat(rows, "\n"), "\n")
    local closed = file:close()
    if written and closed then
        if not os.rename(temporary, target) then
            os.remove(target)
            if not os.rename(temporary, target) then return end
        end
        published = serial
    end
end

local function play(pawn, path, point, rotation, scale, volume, pitch, distance)
    local class, base = asset(path), asset(weapon_base)
    local defaults = valid(class) and class:GetCDO()
    if not valid(defaults) or not valid(base) or not defaults:IsA(base) then return end
    local attenuation, sound = asset(attenuation_path), defaults.FireSound
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local statics = StaticFindObject(statics_path)
    local particles, audio = false, false
    if scale > 0 and valid(niagara) and valid(defaults.MuzzleFlash) then
        local component = niagara:SpawnSystemAtLocation(pawn, defaults.MuzzleFlash, point, rotation,
            {X = scale, Y = scale, Z = scale}, true, true, 0, true)
        particles = valid(component) == true
    end
    if volume > 0 and valid(statics) and valid(sound) and valid(attenuation) then
        local component = statics:SpawnSoundAtLocation(pawn, sound, point, rotation, volume, 1, 0,
            attenuation, defaults["Concurrency Settings"], true)
        if valid(component) then
            component:SetFloatParameter(FName("Distance"), distance)
            component:SetFloatParameter(FName("PitchShift"), pitch)
            audio = true
        end
    end
    -- A muted process has no audio device, but can still verify the selected stock sound.
    return particles, audio, valid(sound) == true
end

function view.command(args, state, phase)
    if args[2] ~= "native-weapon-play-shots" then return end
    local pawn, epoch = state.local_pawn and state.local_pawn.pawn, args[4]
    if phase ~= "connected" or not valid(pawn) or not pawn:IsLocallyControlled() or not valid(state.world)
        or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[a-f0-9]+$")
        or #args < 5 or #args > maximum + 4 then error("Weapon shot playback scope is unavailable", 0) end
    local scope = tostring(state.world:GetAddress()) .. epoch
    if scope ~= visit then visit, last_played = scope, 0 end
    local origin, receipts = pawn:K2_GetActorLocation(), {}
    for index = 5, #args do
        local values = {}
        for field in (args[index] .. "|"):gmatch("([^|]*)|") do values[#values + 1] = field end
        local id, path = math.tointeger(tonumber(values[1])), values[2]
        local x, y, z, pitch_angle, yaw, roll, scale, volume, pitch = table.unpack(values, 3, 11)
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        pitch_angle, yaw, roll = tonumber(pitch_angle), tonumber(yaw), tonumber(roll)
        scale, volume, pitch = tonumber(scale), tonumber(volume), tonumber(pitch)
        if #values == 11 and id and id > last_played and stock_weapon(path)
            and bounded(x, -1e7, 1e7) and bounded(y, -1e7, 1e7) and bounded(z, -1e7, 1e7)
            and bounded(pitch_angle, -180, 180) and bounded(yaw, -180, 180) and bounded(roll, -180, 180)
            and bounded(scale, 0, 10) and bounded(volume, 0, 10) and bounded(pitch, -26, 24) then
            last_played = id
            local distance = math.sqrt((origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2)
            if distance <= range then
                local ok, particles, audio, stock_sound = pcall(play, pawn, path, {X = x, Y = y, Z = z},
                    {Pitch = pitch_angle, Yaw = yaw, Roll = roll}, scale, volume, pitch, distance)
                if ok then
                    receipts[#receipts + 1] = string.format("%d:%d:%d:%d", id,
                        particles and 1 or 0, audio and 1 or 0, stock_sound and 1 or 0)
                end
            end
        end
    end
    return table.concat(receipts, ",")
end

return view
