local area = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local epoch, first, second = ("e"):rep(32), ("A"):rep(32), ("B"):rep(32)
local selected = "enemy-shell:" .. epoch .. ":" .. first
local classes, world, serial = {}, nil, 0
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local camera_path = "/Script/Engine.CameraComponent"
local actor_path = "/Script/Engine.StaticMeshActor"
local mesh_path = "/Engine/BasicShapes/Cube.Cube"
local material_path = "/Game/Assets/Materials/M_Light_Yellow.M_Light_Yellow"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local text_path = "/Script/UMG.TextBlock"
local raw_path = area .. ".DeadBridge_P:PersistentLevel.Ammo_ShotgunShells_BP_C_"
local raw_class = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"
local function raw_name(suffix) return "Ammo_ShotgunShells_BP_C " .. raw_path .. suffix end
local game_time, blocked, downed = 0, false, false
local spawned, destroyed, created, removed = 0, 0, 0, 0

local function object(name, class)
    serial = serial + 1
    local address = serial
    return { IsValid = function() return true end, GetAddress = function() return address end,
        GetFullName = function() return name end, GetClass = function() return classes[class] end,
        GetWorld = function() return world end, IsActorBeingDestroyed = function(self) return self.destroyed == true end }
end

os.getenv = function() return "1" end
StaticFindObject = function(path) return classes[path] end
FText = function(value) return { ToString = function() return value end } end
SilverfishTakeNativeInteractPress = function() end
for _, path in ipairs({pawn_path, controller_path, camera_path, actor_path, mesh_path, material_path, widget_path, text_path, raw_class}) do
    classes[path] = object(path)
end
world = object("World " .. area .. ".DeadBridge_P")

local pawn, controller, camera = object("Player_FP_C host", pawn_path), object("Controller", controller_path), object("Camera", camera_path)
pawn.Controller, pawn.Camera, pawn.Dead, pawn.CanInteract, pawn.InteractionRange = controller, camera, false, false, 250
controller.Pawn, controller.Loading = pawn, false
controller.MenuStack = {GetArrayNum = function() return 0 end}
pawn.IsLocallyControlled = function() return true end
pawn.HasAuthority = function() return true end
controller.IsLocalController = pawn.IsLocallyControlled
pawn.K2_GetActorLocation = function() return { X = 0, Y = 0, Z = 0 } end
camera.GetOwner = function() return pawn end
camera.K2_GetComponentLocation = function() return { X = 0, Y = 0, Z = 40 } end
camera.RelativeRotation = { Pitch = 0, Yaw = 0, Roll = 0 }
camera.K2_SetWorldRotation = function(self, value) self.RelativeRotation = value end
camera.K2_SetRelativeRotation = camera.K2_SetWorldRotation
camera.GetForwardVector = function(self)
    local pitch, yaw = math.rad(self.RelativeRotation.Pitch), math.rad(self.RelativeRotation.Yaw)
    return { X = math.cos(pitch) * math.cos(yaw), Y = math.cos(pitch) * math.sin(yaw), Z = math.sin(pitch) }
end
pawn.LookTrace = function() end

classes["/Script/Engine.Default__GameplayStatics"] = object("GameplayStatics")
classes["/Script/Engine.Default__GameplayStatics"].GetRealTimeSeconds = function() return game_time end
classes["/Script/Engine.Default__KismetSystemLibrary"] = object("KismetSystemLibrary")
classes["/Script/Engine.Default__KismetSystemLibrary"].LineTraceSingle = function(_, local_pawn, eye, target, _, _, ignore)
    assert(local_pawn == pawn and ignore[1] == pawn and eye.Z == 40 and target.Z == 40)
    return blocked
end
classes["/Script/UMG.Default__WidgetBlueprintLibrary"] = object("WidgetBlueprintLibrary")
classes["/Script/UMG.Default__WidgetBlueprintLibrary"].Create = function(_, local_pawn, requested, owner)
    assert(requested == classes[widget_path] and local_pawn == pawn and owner == controller)
    created = created + 1
    local widget, text = object("Shell prompt", widget_path), object("Prompt text", text_path)
    widget.TextBlock_Notes = text
    widget.GetOwningPlayer = function() return owner end
    widget.SetVisibility = function(_, visibility) assert(visibility == 3) end
    widget.SetDesiredSizeInViewport = function() end
    widget.SetPositionInViewport = function() end
    widget.AddToPlayerScreen = function() return true end
    widget.IsInViewport = function() return true end
    widget.RemoveFromParent = function() removed = removed + 1 end
    text.SetText = function(self, rendered) self.rendered = rendered; assert(rendered:ToString():find("Interact")) end
    text.GetText = function(self) return self.rendered end
    return widget
end
world.SpawnActor = function(_, class, point)
    assert(class == classes[actor_path] and point.Z == 40)
    spawned = spawned + 1
    local actor, component = object("local marker", actor_path), object("hint mesh")
    actor.StaticMeshComponent, actor.bReplicates, actor.bHidden = component, false, false
    actor.HasAuthority = function() return true end
    actor.K2_GetActorLocation = function() return point end
    actor.SetActorEnableCollision = function(self, value) self.collision = value end
    actor.GetActorEnableCollision = function(self) return self.collision end
    actor.SetActorScale3D = function() end
    actor.K2_DestroyActor = function(self) self.destroyed = true; destroyed = destroyed + 1 end
    component.GetOwner = function() return actor end
    component.IsVisible = function() return true end
    component.SetMobility = function() end
    component.SetStaticMesh = function(self, mesh) self.StaticMesh = mesh; return true end
    component.SetMaterial = function(self, _, material) self.material = material end
    component.GetMaterial = function(self) return self.material end
    component.SetCollisionEnabled = function(self, value) self.collision = value end
    component.GetCollisionEnabled = function(self) return self.collision end
    return actor
end

local view = assert(loadfile("mod/SilverfishCoop/Scripts/native_enemy_shell_view.lua"))()
view.start(function() return downed end)
local state = { map = area, world = world, server = false, listening = true, controller = controller,
    local_pawn = { pawn = pawn, name = pawn:GetFullName(), controller_object = controller } }
local frame = { "nonce", "native-enemy-shell-frame", epoch, pawn:GetFullName(),
    first .. "|6|150|0|0|" .. raw_name("10"), second .. "|5|0|160|0|" .. raw_name("11") }
assert(view.command(frame, state, "hosting"))
view.sync(state, "hosting")
assert(spawned == 2 and created == 1 and view.focused_target(pawn) == selected)
local rows = view.rows(state, "hosting", table.pack)
assert(rows[2][5] == first .. "," .. second and rows[3][4] == first .. "," .. second)
blocked = true
assert(not view.focused_target(pawn) and not view.clear_line(pawn, { X = 150, Y = 0, Z = 0 }))
view.sync(state, "hosting")
assert(removed == 1 and destroyed == 0)
blocked, pawn.CanInteract = false, true
view.sync(state, "hosting")
assert(not view.focused_target(pawn) and created == 1)
pawn.CanInteract, downed = false, true
view.sync(state, "hosting")
assert(not view.focused_target(pawn))
downed = false
local with_second = "enemy-shell:" .. epoch .. ":" .. second
assert(view.fixture(state, "hosting", with_second, function() return view.focused_target(pawn) end))
assert(camera.RelativeRotation.Yaw == 0 and view.focused_target(pawn) == selected and spawned == 2)

frame[6] = nil
assert(view.command(frame, state, "hosting"))
view.sync(state, "hosting")
assert(destroyed == 1 and spawned == 2)
local raw = object(raw_name("10"), raw_class)
classes[raw_path .. "10"] = raw
view.sync(state, "hosting")
assert(not view.focused_target(pawn) and not view.raw_retired(world, raw_name("10")))
assert(destroyed == 2 and view.rows(state, "hosting", table.pack)[2][5] == "-")
local exact_world = raw.GetWorld
raw.GetWorld, raw.destroyed = function() return object("old world") end, true
view.sync(state, "hosting")
assert(not view.raw_retired(world, raw_name("10")) and spawned == 2)
raw.GetWorld = exact_world
view.sync(state, "hosting")
assert(view.raw_retired(world, raw_name("10")) and view.focused_target(pawn) == selected and spawned == 3)
game_time = 3
view.sync(state, "hosting")
assert(destroyed == 3 and not view.focused_target(pawn) and #view.rows(state, "hosting", table.pack) == 1)
state.server, pawn.HasAuthority = true, function() return false end
frame[5] = first .. "|6|150|0|0"
assert(view.command(frame, state, "connected"))
view.sync(state, "connected")
assert(view.focused_target(pawn) == selected)
state.server = false
view.sync(state, "connected")
assert(destroyed == 4 and not view.focused_target(pawn))
print("Exact shell cues yield to stock focus and walls, remain noncolliding, expire, and use the selected local stock press")
