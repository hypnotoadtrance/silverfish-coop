local avatar = require("native_player_avatar")
local players = require("players")
local lean_bridge = {enabled = (os.getenv("SILVERFISH_COOP_AVATAR_LEAN") or "1") == "1"}
local scope

local function valid(object)
    return object and object:IsValid() == true or false
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function lean_bridge.sync(state, phase)
    if not lean_bridge.enabled then return end
    local role = phase == "hosting" and "host" or phase == "connected" and "client" or nil
    local details, world = state.local_pawn, state.world
    local controller = details and details.controller_object
    local driver = valid(world) and world.NetDriver
    if not role or not details or not valid(details.pawn) or not state.listening or not valid(driver)
        or not same(controller, state.controller)
        or type(details.name) ~= "string" or not details.name:match("^Player_FP_C [!-~]+$") or #details.name > 480 then
        scope = nil
        return
    end
    if not scope or scope.role ~= role or scope.name ~= details.name or not same(scope.world, world)
        or not same(scope.pawn, details.pawn) or not same(scope.controller, controller) or not same(scope.driver, driver) then
        scope = {world = world, pawn = details.pawn, name = details.name, controller = controller, driver = driver,
            role = role, pawn_address = details.pawn:GetAddress(), world_address = world:GetAddress(), next_sample = 0}
    end
end

local function project(frame, current)
    if type(frame) ~= "string" or #frame > 512 then return end
    local name, lines = frame:match("^([^\n]+)\n(.*)$")
    if name ~= current.name or lines == "" then return end
    if lines:sub(-1) ~= "\n" then lines = lines .. "\n" end
    local angles, count = {}, 0
    for line in lines:gmatch("([^\n]*)\n") do
        local id_text, roll_text = line:match("^(%d+)\t(%-?%d+)$")
        count = count + 1
        if count > 16 or not id_text or #id_text > 10 or #roll_text > 5 then return end
        local id, roll = math.tointeger(id_text), math.tointeger(roll_text)
        if not id or id > 2147483647 or not roll or math.abs(roll) > 4000 or angles[id] ~= nil then return end
        angles[id] = roll / 100
    end
    for id, roll in pairs(angles) do avatar.set_remote_lean(current.world, id, roll) end
end

local function qualify_lua(current, world)
    local controller, pawn = current.controller, current.pawn
    return same(world.NetDriver, current.driver)
        and valid(current.driver.ServerConnection) == (current.role == "client")
        and valid(controller) and controller:IsLocalController() and same(controller:GetWorld(), world)
        and same(controller.Pawn, pawn) and same(pawn:GetWorld(), world) and pawn:IsLocallyControlled()
        and not pawn:IsActorBeingDestroyed() and pawn:GetFullName() == current.name
end

function lean_bridge.update(get_world, role, leaving)
    local current = scope
    if not current then return end
    if leaving or role ~= current.role then
        scope = nil
        return
    end
    if type(SilverfishTakeRemoteAvatarPoses) ~= "function" or type(SilverfishPublishLocalAvatarPose) ~= "function" then return end
    local now = os.clock()
    if now < current.next_sample then return end
    current.next_sample = now + .05

    local world, pawn = get_world(), current.pawn
    if not same(world, current.world) then
        scope = nil
        return
    end
    local native_roll
    if type(SilverfishReadNativeLocalLean) == "function" then
        native_roll = SilverfishReadNativeLocalLean(current.pawn_address, current.world_address, role == "host")
    end
    if native_roll == nil and not qualify_lua(current, world) then
        scope = nil
        return
    end
    local attached, frame = SilverfishTakeRemoteAvatarPoses()
    if attached == nil then return end
    if attached == false then
        current.last_roll, current.last_publish = nil, nil
        return
    end
    project(frame, current)

    local roll = players.is_downed(pawn) == false and (native_roll or avatar.local_lean(pawn, world)) or 0
    local centidegrees = math.floor(roll * 100 + .5)
    if centidegrees == current.last_roll and (centidegrees == 0 or now - current.last_publish < .2) then return end
    if SilverfishPublishLocalAvatarPose(current.name .. "\t" .. centidegrees) ~= false then
        current.last_roll, current.last_publish = centidegrees, now
    end
end

return lean_bridge
