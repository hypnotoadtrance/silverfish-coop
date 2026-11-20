local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local level = map .. ".DataSilo_01_P:PersistentLevel."
local core_name = "MindCore_BP_C " .. level .. "MindCore_BP_C_1"
local owner_name = "Player_FP_C " .. level .. "Player_FP_C_21"
local other_name = "Player_FP_C " .. level .. "Player_FP_C_22"
local lease, next_lease = string.rep("a", 32), string.rep("b", 32)
local verbs = "native-mindcore-carry"

local function run()
    local count = 0
    local function object(properties)
        count = count + 1
        local address = count
        properties.IsValid = function() return true end
        properties.GetAddress = function() return address end
        return properties
    end
    local scene = {world = object({}), canonical = object({}), host = object({}), status = 0, installed = false}
    scene.core = object({BeingHeld = false, bReplicates = true, bReplicateMovement = true,
        GetFullName = function() return core_name end, GetActorEnableCollision = function() return true end})
    scene.cradle = object({GetFullName = function() return "MindCore_Cradle_BP_C " .. level .. "MindCore_Cradle_BP_C_1" end})
    scene.frame = object({IsSimulatingPhysics = function() return true end, GetMass = function() return 95.46 end})
    local detached, focus = object({}), object({})
    local pawn = object({Save = detached, Dead = false, InteractionRange = 400, MaxHoldWeight = 110,
        ActorBeingLookedAt = focus, ActorLookedAt_Distance = 29, GetFullName = function() return owner_name end,
        HasAuthority = function() return true end, IsActorBeingDestroyed = function() return false end,
        GetWorld = function() return scene.world end, K2_GetActorLocation = function() return {X = 0, Y = 0, Z = 0} end})
    local camera_type, handle_type = object({}), object({})
    local eye = {X = 0, Y = 0, Z = 45}
    local camera = object({GetOwner = function() return pawn end, GetClass = function() return camera_type end,
        K2_GetComponentLocation = function() return eye end})
    local handle = object({GetOwner = function() return pawn end, GetClass = function() return handle_type end,
        GetGrabbedComponent = function(self) return self.grabbed end})
    pawn.Camera, pawn.PhysicsHandle = camera, handle
    local first = scene.core
    local blocker = object({})
    local trace = object({LineTraceSingle = function(_, origin_pawn, origin, endpoint, channel, complex, ignored, _, hit)
        assert(origin_pawn == pawn and origin == eye and endpoint.X == 400 and endpoint.Z == 45)
        assert(channel == 1 and complex == true and #ignored == 0)
        hit.Component = {Get = function() return object({GetOwner = function() return first end}) end}
        hit.ImpactPoint = {X = 100, Y = 0, Z = 45}
        return true
    end})
    local state = {map = map, world = scene.world}
    local actions, native_state, native_reason, native_address = {}, nil, nil, nil
    local aim, allowed, fail_release = nil, true, false
    local function mock_native(pawn_address, save, stock, core, cradle, action, pitch, yaw)
        assert(pawn_address == pawn:GetAddress() and save == detached:GetAddress() and stock == scene.canonical:GetAddress()
            and core == scene.core:GetAddress() and cradle == scene.cradle:GetAddress())
        actions[#actions + 1] = {action, pitch, yaw}
        native_address = pawn_address
        if action == "grab" then
            assert(pawn.ActorBeingLookedAt == scene.core and pawn.ActorLookedAt_Distance == 100)
            native_state, native_reason = "held", "stock_core_grabbed"
            scene.core.BeingHeld, scene.core["Holding Actor"] = true, pawn
            handle.grabbed, pawn.HeldObject = scene.frame, scene.core
        elseif action == "hold" then
            assert(pawn.ActorBeingLookedAt == focus and pawn.ActorLookedAt_Distance == 29)
            native_state, native_reason = "held", "stock_core_renewed"
        else
            assert(action == "release")
            if fail_release then return false, "mutation_unverified_native_mindcore_carry_drop" end
            native_state, native_reason = "released", "stock_core_released"
            scene.core.BeingHeld, scene.core["Holding Actor"] = false, nil
            handle.grabbed, pawn.HeldObject = nil, nil
        end
        return true, native_reason
    end
    local env = setmetatable({
        os = {getenv = function(key) return key == "SILVERFISH_COOP_NATIVE_ZONE" and "1" end},
        io = {open = function(path, mode)
            assert(path == "private/native-mindcore-authority-aim.tsv" and mode == "rb")
            return aim and {read = function(_, cap) assert(cap == 129); return aim:sub(1, cap) end, close = function() end}
        end},
        require = function(name)
            if name == "guest_save" then
                return {exact_item_context = function(_, _, requested)
                    if allowed and requested == owner_name then return pawn, detached, "character" end
                end}
            end
            if name == "native_mindcore" then return {scene = function() return scene end} end
            assert(name == "players")
            return {is_standing_guest = function(checked, requested) return checked == pawn and requested == owner_name end}
        end,
        StaticFindObject = function(path) return ({["/Script/Engine.CameraComponent"] = camera_type,
            ["/Script/Engine.PhysicsHandleComponent"] = handle_type,
            ["/Script/Engine.Default__KismetSystemLibrary"] = trace})[path] end,
        FName = function(name) assert(name == "None"); return name end,
        EFindName = {FNAME_Find = 0},
        SilverfishRunNativeMindCoreCarry = mock_native,
        SilverfishReadNativeMindCoreCarry = function() return native_state, native_reason, native_address end,
    }, {__index = _G})
    local carry = assert(loadfile("mod/SilverfishCoop/Scripts/native_mindcore_carry.lua", "t", env))()
    local function request(name, token, action, number, ...)
        return pcall(carry.command, {"1", verbs, name, token, action, number, ...}, state, "hosting")
    end
    local function report()
        for _, row in ipairs(carry.rows(state, "hosting", function(...) return {...} end)) do
            if row[1] == "native-mindcore-authority-carry" then return row end
        end
    end
    local function refuses(name, token, action, number, reason, ...)
        local ok, why = request(name, token, action, number, ...)
        assert(not ok and why == "native_mindcore_carry_refused_" .. reason, tostring(why))
    end
    carry.start("private")
    assert(carry.rows(state, "hosting", function(...) return {...} end)[1][3] == core_name)
    scene.core.bReplicateMovement = false
    assert(#carry.rows(state, "hosting", function(...) return {...} end) == 0)
    scene.core.bReplicateMovement = true
    refuses(other_name, lease, "grab", "1", "owner_or_core_unavailable", "0", "0")
    refuses(owner_name, lease, "grab", "01", "request_unqualified", "0", "0")
    refuses(owner_name, lease, "grab", "1", "aim_unqualified", "90", "0")
    refuses(owner_name, lease, "grab", "1", "aim_unqualified", "0", "0.33333")
    pawn.MaxHoldWeight = 85
    refuses(owner_name, lease, "grab", "1", "stock_body_unqualified", "0", "0")
    pawn.MaxHoldWeight = 110
    first = blocker
    refuses(owner_name, lease, "grab", "1", "stock_core_not_first_visible_hit", "0", "0")
    assert(#actions == 0 and pawn.ActorBeingLookedAt == focus and pawn.ActorLookedAt_Distance == 29)
    first = scene.core
    assert(request(owner_name, lease, "grab", "1", "0", "0"))
    assert(pawn.ActorBeingLookedAt == focus and pawn.ActorLookedAt_Distance == 29 and report()[6] == "held")
    refuses(other_name, lease, "drop", "2", "holder_or_sequence_changed")
    aim = "1\t" .. next_lease .. "\t2\t14.5\t-29.25\n"
    carry.sync(state, "hosting")
    assert(#actions == 1)
    aim = "1\t" .. lease .. "\t2\t14.5\t-29.25\n"
    carry.sync(state, "hosting")
    carry.sync(state, "hosting")
    assert(#actions == 2 and actions[2][1] == "hold" and actions[2][2] == 14.5 and actions[2][3] == -29.25)
    refuses(owner_name, lease, "drop", "2", "holder_or_sequence_changed")
    for _, ignored in ipairs({"1\t" .. next_lease .. "\t3\tdrop\n", "1\t" .. lease .. "\t2\tdrop\n",
        "1\t" .. lease .. "\t3\tdrop\t0\n"}) do
        aim = ignored
        carry.sync(state, "hosting")
        assert(#actions == 2 and report()[6] == "held")
    end
    aim = "1\t" .. lease .. "\t3\tdrop\n"
    carry.sync(state, "hosting")
    carry.sync(state, "hosting")
    assert(#actions == 3 and actions[3][1] == "release" and not pawn.HeldObject
        and report()[6] == "released" and report()[7] == 3 and report()[8] == "stock_core_released")
    refuses(owner_name, lease, "grab", "4", "holder_or_sequence_changed", "0", "0")
    assert(request(owner_name, next_lease, "grab", "7", "0", "0"))
    mock_native(pawn:GetAddress(), detached:GetAddress(), scene.canonical:GetAddress(),
        scene.core:GetAddress(), scene.cradle:GetAddress(), "release")
    native_reason = "stock_core_capsule_release"
    scene.status, scene.installed = 1, true
    carry.sync(state, "hosting")
    assert(report()[6] == "mount_released" and report()[8] == "stock_core_capsule_release")

    local final_lease = string.rep("c", 32)
    scene.status, scene.installed = 0, false
    assert(request(owner_name, final_lease, "grab", "1", "0", "0"))
    assert(carry.clear(other_name) and pawn.HeldObject == scene.core)
    allowed, fail_release = false, true
    carry.sync(state, "hosting")
    assert(report()[6] == "failure" and report()[8] == "mutation_unverified_native_mindcore_carry_drop")
    local ok, reason = pcall(carry.clear)
    assert(not ok and reason == "mutation_unverified_native_mindcore_carry_drop")
    fail_release = false
    carry.sync(state, "hosting")
    assert(not pawn.HeldObject and report()[6] == "failure" and carry.clear())
end

run()
