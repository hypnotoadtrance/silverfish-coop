local install = "native-mindcore-install-fixture"
local pawn = "Player_FP_C exact-listen-host"
local function object(properties)
    properties.IsValid = function() return true end
    properties.GetAddress = function() return properties end
    return properties
end

local function run()
    local exact, origin = object({}), {X = 0, Y = 6750, Z = 6600}
    local center = {X = 0, Y = 6662.06, Z = 6719.47}
    local scene = {status = 0, installed = false}
    local encountered_overlap, moves = false, 0
    scene.host = object({Dead = false, GetFullName = function() return pawn end})
    scene.cradle = setmetatable(object({K2_GetActorLocation = function() return origin end}), {
        __index = function(_, property)
            assert(property ~= "Powered", "Powered is a stock signal argument rather than a cradle property")
        end,
    })
    scene.cradle.RootComponent = object({})
    scene.cradle.SM_MindCoffin_Socket = object({
        GetOwner = function() return scene.cradle end,
        GetAttachParent = function() return scene.cradle.RootComponent end,
    })
    scene.mount = object({GetAttachParent = function() return scene.cradle.SM_MindCoffin_Socket end})
    scene.cradle.Capsule = object({
        GetClass = function() return exact end,
        GetOwner = function() return scene.cradle end,
        GetAttachParent = function() return scene.mount end,
        GetGenerateOverlapEvents = function() return true end,
        IsOverlappingActor = function(_, core) assert(core == scene.core); return encountered_overlap end,
        K2_GetComponentLocation = function() return center end,
    })
    scene.core = object({
        GetActorEnableCollision = function() return true end,
        K2_GetActorLocation = function() return {X = 714.39, Y = -1686.95, Z = 118.37} end,
        K2_SetActorLocation = function(_, position, sweep, hit, teleport)
            assert(position.X == center.X and position.Y == center.Y and position.Z == center.Z)
            assert(sweep == false and type(hit) == "table" and teleport == true)
            moves, encountered_overlap = moves + 1, true
            return true
        end,
    })
    scene.frame = object({
        GetGenerateOverlapEvents = function() return true end,
        IsSimulatingPhysics = function() return true end,
    })
    local enabled = true
    local env = setmetatable({
        require = function(name)
            if name == "native_mindcore" then
                return {scene = function(state, phase) assert(state == scene and phase == "hosting"); return scene end}
            end
            return {}
        end,
        os = {getenv = function() return enabled and "1" or nil end},
        StaticFindObject = function(path) assert(path == "/Script/Engine.CapsuleComponent"); return exact end,
        FName = function(name) assert(name == "None"); return name end,
        EFindName = {FNAME_Find = 0},
    }, {__index = _G})
    local path = "mod/SilverfishCoop/Scripts/native_mindcore_fixture.lua"
    local fixture = assert(loadfile(path, "t", env))()
    local function request(name)
        local accepted, result = pcall(fixture.command, {"1", install, name}, scene, "hosting")
        return accepted, result
    end
    local function refused(name, reason)
        local ok, result = request(name)
        assert(not ok and result == "native_mindcore_fixture_refused_" .. reason, tostring(result))
        assert(moves == 0)
    end
    refused("Player_FP_C wrong-host", "exact_host_pawn_changed")
    scene.host.HeldObject = object({})
    refused(pawn, "host_holding_object")
    scene.host.HeldObject = nil
    scene.status = 1
    refused(pawn, "console_not_dormant")
    scene.status = 0
    scene.frame.GetGenerateOverlapEvents = function() return false end
    refused(pawn, "stock_core_overlap_disabled")
    scene.frame.GetGenerateOverlapEvents = function() return true end
    scene.cradle.Capsule.GetAttachParent = function() return scene.cradle.RootComponent end
    refused(pawn, "stock_capsule_attachment_changed")
    scene.cradle.Capsule.GetAttachParent = function() return scene.mount end
    scene.cradle.Capsule.K2_GetComponentLocation = function() return origin end
    refused(pawn, "stock_capsule_displaced:cradle=0.0,6750.0,6600.0:capsule=0.0,6750.0,6600.0")
    scene.cradle.Capsule.K2_GetComponentLocation = function() return center end
    enabled = false
    local disabled = assert(loadfile(path, "t", env))()
    local ok, result = pcall(disabled.command, {"1", install, pawn}, scene, "hosting")
    assert(not ok and result == "native_mindcore_fixture_refused_not_enabled" and moves == 0)
    assert(request(pawn))
    assert(moves == 1 and encountered_overlap and scene.status == 0,
        "fixture must move the Core without writing the stock console status")
end

run()
