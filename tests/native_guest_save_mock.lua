local scripts = "mod/SilverfishCoop/Scripts/"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local connection_path = "/Script/Engine.NetConnection"
local map = "/Test/Observatory"
local character = "12345678-1234-1234-1234-123456789abc"
local next_address = 0

local function object(name, class)
    next_address = next_address + 1
    local address = next_address
    return {
        GetAddress = function() return address end,
        GetFullName = function() return name end,
        GetClass = function() return class end,
        IsValid = function() return true end,
        IsA = function(_, candidate) return candidate == class end,
    }
end

local function empty_array()
    return { GetArrayNum = function() return 0 end }
end

local world, driver = object("World"), object("Listen driver")
world.NetDriver, driver.World = driver, world
local save_class, instance_class, connection_class = object(save_path), object(instance_path), object(connection_path)
local stock, detached = object("Save Stock", save_class), object("Save Detached", save_class)
stock.PlayerBackpack, stock.PlayerEquipment = empty_array(), empty_array()
detached.PlayerBackpack, detached.PlayerEquipment = empty_array(), empty_array()
function save_class:GetCDO() return stock end
local instance = object("Game instance", instance_class)
instance.SaveSlot_Ref = stock
local statics = object("Gameplay statics")
function statics:GetGameInstance() return instance end
function statics:CreateSaveGameObject(class)
    assert(class == save_class)
    return detached
end

local function player(name, local_player)
    local pawn, controller = object(name), object(name .. " controller")
    local save = stock
    setmetatable(pawn, {
        __index = function(self, key)
            if key == "Save" then
                assert(not self.retiring, "Do not read the save of a retiring pawn")
                return save
            end
        end,
        __newindex = function(self, key, value)
            if key == "Save" then
                assert(not self.retiring, "Do not write the save of a retiring pawn")
                save = value
            else
                rawset(self, key, value)
            end
        end,
    })
    function pawn:GetWorld() return world end
    function pawn:HasAuthority() return true end
    function pawn:IsActorBeingDestroyed() return self.retiring == true end
    function pawn:ApplyDynamicStats() end
    function pawn:Compile_VoxSetup() end
    function controller:GetWorld() return world end
    function controller:IsLocalController() return local_player end
    function controller:HasAuthority() return true end
    function controller:IsActorBeingDestroyed() return false end
    pawn.MaxHealth, pawn.Controller, controller.Pawn = 250, controller, pawn
    local connection
    if not local_player then
        connection = object(name .. " connection", connection_class)
        connection.Driver, connection.PlayerController, connection.OwningActor = driver, controller, controller
        controller.Player, controller.NetConnection = connection, connection
        driver.ClientConnections = { connection, GetArrayNum = function() return 1 end }
    end
    return { name = name, pawn = pawn, controller_object = controller, kind = local_player and "local" or "guest" }, connection
end

local root_pinned = false
local classes = { [statics_path] = statics, [instance_path] = instance_class,
    [save_path] = save_class, [connection_path] = connection_class }
local module = assert(loadfile(scripts .. "guest_save.lua", "t", setmetatable({
    os = setmetatable({ getenv = function(key)
        return key == "SILVERFISH_COOP_NATIVE_DEATH" and "cpp" or "1"
    end }, { __index = os }),
    require = function(name)
        if name == "native_event_heartbeat" then return { fresh = function() return true end } end
        if name == "vitals" then return { active = function() return false end } end
        assert(name == "zone_maps")
        return { [map] = {} }
    end,
    SilverfishNativeDeathReady = function() return true end,
    SilverfishClearNativeDeathScope = function() return true end,
    SilverfishReferenceNativeDetachedSave = function(owner, candidate, pin)
        assert(owner == stock:GetAddress() and candidate == detached:GetAddress())
        root_pinned = pin
        return true
    end,
    StaticFindObject = function(path) return assert(classes[path]) end,
}, { __index = _G })))()
local last_revoked
module.start("mock", function(name) last_revoked = name; return true end)
local host = player("Host pawn", true)
local guest, connection = player("Departing pawn", false)
local state = { map = map, world = world, controller = host.controller_object, server = false,
    listening = true, clients = 1, local_pawn = host, pawns = { host, guest } }

local function bind()
    assert(module.command({ "ticket", "native-guest-bind-exact", guest.name, guest.controller_object:GetFullName(),
        connection:GetFullName(), character, "-" }, state, "hosting") == "native guest save bound")
    assert(guest.pawn.Save == detached)
end

bind()
assert(module.command({ "ticket", "native-guest-save-retain", guest.name, character },
    state, "hosting") == "retained exact co-op guest inventory")
guest.pawn.retiring = true
assert(module.sync(state, "hosting"))
assert(not module.active() and last_revoked == guest.name and root_pinned,
    "A retiring guest's binding must clear while its retained save stays available")

guest, connection = player("Replacement pawn", false)
state.pawns[2] = guest
bind()
assert(module.command({ "ticket", "native-guest-clear" }, state, "hosting") == "native guest save cleared")
assert(not module.active() and guest.pawn.Save == stock and last_revoked == guest.name and not root_pinned)
print("native guest save retirement fixtures passed")
