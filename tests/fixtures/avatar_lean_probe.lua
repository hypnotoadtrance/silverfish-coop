-- Private, muted title fixture for the two stock lean actions and the actual display rig.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox then return end
local portable = mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]")
if not portable and os.getenv("SILVERFISH_COOP_AVATAR_LEAN_PROBE") ~= "1" then return end
local seat, nonce = mailbox:match("[/\\]titles[/\\]([a-zA-Z0-9][a-zA-Z0-9_%-]*)[/\\]run[/\\]game%-bridge[/\\]([0-9a-f]+)$")
if not seat or #seat > 64 or #nonce ~= 32 then return end

local command = mailbox .. "/avatar-lean-probe-command.tsv"
local reply = mailbox .. "/avatar-lean-probe-reply.tsv"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local player_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local arrow_class = "/Script/Engine.ArrowComponent"
local input_root = "/Game/Classes/Player/Player_Inputs/"
local subsystem_class = "/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem"
local sides = {left = {"IA_Lean_Left", 3}, right = {"IA_Lean_Right", 10}}
local input_vector = {X = 1, Y = 0, Z = 0}
local trial, last, hooks_installed

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function unwrap(value)
    if type(value) == "userdata" and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then
        return value:get()
    end
    return value
end

local function stock_arrow(pawn)
    local arrow = pawn.LeanPivotArrow
    assert(exact(arrow, arrow_class) and same(arrow:GetOwner(), pawn) and same(arrow:GetAttachParent(), pawn.CapsuleComponent),
        "stock lean pivot unavailable")
    return arrow
end

local function rig(pawn)
    local body, head
    local meshes = pawn:K2_GetComponentsByClass(StaticFindObject("/Script/Engine.SkeletalMeshComponent"))
    for index = 1, #meshes do
        local mesh = unwrap(meshes[index])
        if valid(mesh) and same(mesh:GetAttachParent(), pawn.Mesh) and valid(mesh.SkinnedAsset) then
            assert(not body, "multiple stock avatar display meshes")
            body = mesh
        end
    end
    assert(body, "stock avatar display mesh unavailable")
    local components = pawn:K2_GetComponentsByClass(StaticFindObject("/Script/Engine.SceneComponent"))
    for index = 1, #components do
        local pivot = unwrap(components[index])
        if exact(pivot, "/Script/Engine.SceneComponent") and same(pivot:GetAttachParent(), body)
            and pivot:GetAttachSocketName():ToString() == "Head" then
            assert(not head, "multiple avatar head pivots")
            head = pivot
        end
    end
    local bone = FName("Head", EFindName.FNAME_Find)
    assert(head and body:GetBoneIndex(bone) >= 0, "stock avatar neck unavailable")
    return body, head, bone
end

local function install_hooks()
    if hooks_installed then return end
    for side, data in pairs(sides) do
        local wrapper = player_class .. ":InpActEvt_" .. data[1] .. "_K2Node_EnhancedInputActionEvent_"
        RegisterHook(wrapper .. data[2], function(context)
            local run = trial
            if run and run.drive == "input" and run.side == side and same(context:get(), run.pawn) then
                run.starts = run.starts + 1
                run.started_request = run.pawn.LeanAngle
                run.started_limit = run.pawn.Max_LeanAngle
            end
        end)
        RegisterHook(wrapper .. (data[2] + 1), function(context)
            local run = trial
            if run and run.drive == "input" and run.side == side and same(context:get(), run.pawn) then
                run.triggers = run.triggers + 1
            end
        end)
        RegisterHook(wrapper .. (data[2] + 2), function(context)
            local run = trial
            if run and run.drive == "input" and run.side == side and same(context:get(), run.pawn) then run.completes = run.completes + 1 end
        end)
    end
    hooks_installed = true
end

local function input_action(run)
    local source = sides[run.side][1]
    local action = StaticFindObject(input_root .. source .. "." .. source)
    local library = StaticFindObject("/Script/Engine.Default__SubsystemBlueprintLibrary")
    local subclass = StaticFindObject(subsystem_class)
    local mapping = StaticFindObject(input_root .. "IMC_FP.IMC_FP")
    local controller = run.controller
    assert(valid(action) and valid(library) and valid(subclass) and valid(mapping) and valid(controller.PlayerInput)
        and controller.PlayerInput.AppliedInputContexts:Contains(mapping), "stock local lean mapping unavailable")
    local mapped = false
    for index = 1, #mapping.Mappings do
        if same(mapping.Mappings[index].Action, action) then mapped = true; break end
    end
    local subsystem = library:GetLocalPlayerSubSystemFromPlayerController(controller, subclass)
    assert(mapped and exact(subsystem, subsystem_class), "stock lean action or exact local EnhancedInput subsystem unavailable")
    local instance = run.world.OwningGameInstance
    local config = valid(instance) and instance.GamePlay_Config
    assert(same(run.pawn["As Game Instance FP"], instance) and valid(config)
        and (config.LeanBehavior == 0 or config.LeanBehavior == 2),
        "the isolated lean input trial requires stock Hold or Hybrid input behavior")
    run.action, run.subsystem = action, subsystem
    run.input_method = type(SilverfishProbeContinuousLean) == "function" and "continuous" or "vector"
    run.behavior = config.LeanBehavior
    install_hooks()
end

LoopInGameThreadWithDelay(1, function()
    local run = trial
    if not run or run.input_method ~= "vector" or not run.down_at or run.up_at or run.input_error
        or os.clock() - run.down_at >= 1 then return end
    if not same(run.controller.Pawn, run.pawn) or not same(run.pawn:GetWorld(), run.world)
        or not run.pawn:IsLocallyControlled() then return end
    local ok, failure = pcall(run.subsystem.InjectInputVectorForAction, run.subsystem, run.action, input_vector, {}, {})
    if ok then run.injections = run.injections + 1 else run.input_error = failure end
end)

local function begin(id, side, drive, now)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(driver) and world:GetFullName() == "World " .. map
        and (valid(driver.ServerConnection) or #driver.ClientConnections == 1), "expected the isolated Observatory pair")
    local pawn, remote
    for _, actor in ipairs(FindAllOf("Player_FP_C") or {}) do
        if exact(actor, player_class) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if actor:IsLocallyControlled() then
                assert(not pawn, "multiple locally controlled pawns")
                pawn = actor
            else
                assert(not remote, "multiple remote pawns")
                remote = actor
            end
        end
    end
    assert(pawn and remote and pawn.Dead == false and pawn.MoveInputs_Enabled == true and not pawn.CharacterMovement:IsFalling(),
        "the exact local player and its remote avatar must be ready")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    local controller = valid(statics) and statics:GetPlayerController(world, 0)
    assert(valid(math_api) and valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn)
        and same(controller:GetWorld(), world), "the exact local lean controller is unavailable")
    local body, head, bone = rig(remote)
    local run = {id = id, side = side, drive = drive, start = now, world = world, pawn = pawn, remote = remote,
        controller = controller, injections = 0, starts = 0, completes = 0, triggers = 0,
        arrow = stock_arrow(pawn), remote_arrow = stock_arrow(remote), math_api = math_api,
        body = body, head = head, bone = bone, rows = {}}
    if drive == "input" then input_action(run) end
    return run
end

local function start_solver(run)
    local pawn = run.pawn
    local initial, maximum = pawn.LeanAngle, pawn.Max_LeanAngle
    assert(type(initial) == "number" and math.abs(initial) < .01 and type(maximum) == "number"
        and maximum >= 30 and maximum <= 40, "stock lean solver is not neutral or its range changed")
    run.initial_request = initial
    run.solver_request = run.side == "left" and -30 or 30
    -- Supply only the request. The game's own tick solves and clamps the arrow roll.
    pawn.LeanAngle = run.solver_request
    assert(pawn.LeanAngle == run.solver_request, "stock lean solver request was not accepted")
end

local function restore_solver(run)
    if run.solver_request == nil then return end
    local pawn = run.pawn
    assert(valid(pawn) and same(run.controller.Pawn, pawn) and same(pawn:GetWorld(), run.world)
        and pawn:IsLocallyControlled(), "lean solver scope changed before restoration")
    pawn.LeanAngle = run.initial_request
    assert(pawn.LeanAngle == run.initial_request, "the stock lean request changed during the solver trial")
    run.solver_request = nil
end

local function stop_input(run)
    if run.injecting then
        local stopped, reason = SilverfishProbeContinuousLean(run.pawn:GetAddress(), run.side, false)
        assert(stopped, reason)
        run.injecting = nil
    end
end

local function sample(run, now)
    local time, phase = now - run.start, "baseline"
    assert(same(run.pawn:GetWorld(), run.world) and same(run.remote:GetWorld(), run.world)
        and run.pawn:IsLocallyControlled() and run.pawn.Dead == false and valid(run.head), "lean trial pawn or display changed")
    assert(not run.input_error, run.input_error)
    if time >= .2 and not run.down_at then
        run.down_at = now
        if run.drive == "solver" then start_solver(run) end
        if run.input_method == "continuous" then
            local started, reason = SilverfishProbeContinuousLean(run.pawn:GetAddress(), run.side, true)
            assert(started, reason)
            run.injecting, run.injections = true, 1
        end
    end
    if run.down_at then
        local held = now - run.down_at
        phase = run.up_at and "released" or "held"
        if not run.up_at and held >= 1 then
            restore_solver(run)
            stop_input(run)
            run.up_at, phase = now, "released"
        end
    end

    local basis = run.remote:GetBaseRotationOffsetRotator()
    local rotation = run.math_api:ComposeRotators(
        run.math_api:ComposeRotators(run.math_api:NegateRotator(basis), run.body.RelativeRotation), basis)
    local neck, pivot = run.body:GetSocketLocation(run.bone), run.head:K2_GetComponentLocation()
    local neck_error = math.sqrt((pivot.X - neck.X)^2 + (pivot.Y - neck.Y)^2 + (pivot.Z - neck.Z)^2)
    run.rows[#run.rows + 1] = string.format("%.5f\t%s\t%.5f\t%.5f\t%.5f\t%.5f\t%.5f\t%.5f",
        time, phase, run.pawn.LeanAngle, run.arrow.RelativeRotation.Roll, run.remote_arrow.RelativeRotation.Roll,
        rotation.Roll, run.head:K2_GetComponentRotation().Roll, neck_error)
    return run.up_at and now - run.up_at >= .75
end

local function answer(id, result, rows)
    local file = assert(io.open(reply, "wb"))
    file:write(id, "\t", result, "\n")
    if rows then file:write(table.concat(rows, "\n"), "\n") end
    file:close()
end

LoopInGameThreadWithDelay(50, function()
    local now = os.clock()
    if trial then
        local ok, done = pcall(sample, trial, now)
        if not ok then
            local restored, failure = pcall(function()
                stop_input(trial)
                restore_solver(trial)
            end)
            answer(trial.id, "error\t" .. tostring(done) .. (restored and "" or " | restore: " .. tostring(failure)))
        elseif done then
            answer(trial.id, table.concat({"ok", trial.side, trial.pawn:GetFullName(), trial.remote:GetFullName(),
                trial.pawn.LeanAngle, #trial.rows, trial.injections, trial.starts, trial.completes, trial.drive,
                trial.triggers, trial.started_request or "-", trial.started_limit or "-", trial.behavior or "-",
                trial.input_method or "-"},
                "\t"), trial.rows)
        else
            return false
        end
        trial = nil
        return false
    end
    local file = io.open(command, "rb")
    if not file then return false end
    local request = file:read(64)
    file:close()
    local id, operation = (request or ""):match("^(%x+)\t([a-z]+)\n$")
    local solver_side = operation and operation:match("^solver([a-z]+)$")
    local side, drive = solver_side or operation, solver_side and "solver" or "input"
    if not id or #id ~= 32 or id == last or not sides[side] then return false end
    last = id
    local ok, result = pcall(begin, id, side, drive, now)
    if ok then trial = result else answer(id, "error\t" .. tostring(result)) end
    return false
end)
