local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-oil-lamps-"
local maps = {
    ["/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"] = true,
    ["/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"] = true,
}
local prop_path = "BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/"
local lighter_class = "/Game/Classes/Equipment/Eqp_Lighter.Eqp_Lighter_C"
local equip_factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
local clear = {R=0,G=0,B=0,A=0}
local dirs = {{1,0},{0,1},{-1,0},{0,-1},{.707,.707},{-.707,.707},{-.707,-.707},{.707,-.707}}
local function valid(o) return o and o:IsValid() end
local function same(a,b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local last, observed_lid
local crate_types = {LootCrate_Small_BP_C_1 = "Roll", Loot_Dumpster_BP_C_1 = "Pitch"}

local function watch_lid(action, id)
    if action == "watch-lid" then
        local viewport = FindFirstOf("GameViewportClient")
        local world = valid(viewport) and viewport.World
        local package = valid(world) and world:GetFullName():match("^World (.+)$")
        local crate = package and StaticFindObject(package .. ":PersistentLevel." .. id)
        local panel = valid(crate) and crate.SM_Prop_Cabinet_Small_Door_01
        local mesh_class = StaticFindObject("/Script/Engine.StaticMeshComponent")
        assert(valid(panel) and valid(mesh_class) and crate.Looted == false, "container lid probe unavailable")
        local state = {crate = crate, panel = panel, mesh_class = mesh_class, axis = crate_types[id],
            start = panel.RelativeRotation[crate_types[id]], samples = {}}
        observed_lid = state
        return "watching"
    end
    local state = assert(observed_lid, "container lid probe unavailable")
    if not state.done then return "pending" end
    return ("%d;%s;%.3f"):format(#state.samples, tostring(state.safe), state.panel.RelativeRotation[state.axis])
end

LoopInGameThreadWithDelay(16, function()
    local state = observed_lid
    if not state or state.done or not valid(state.crate) or not valid(state.panel) or not state.crate.Looted then return end
    if state.panel:IsVisible() then
        state.done = true
        return
    end
    local mesh = state.panel.StaticMesh
    for _, part in ipairs(state.crate:K2_GetComponentsByClass(state.mesh_class)) do
        if part and type(part.type) == "function" and part:type() == "RemoteUnrealParam" then part = part:get() end
        if valid(part) and not same(part, state.panel) and same(part.StaticMesh, mesh) and part:IsVisible() then
            local value, final = part.RelativeRotation[state.axis], state.panel.RelativeRotation[state.axis]
            local previous = state.samples[#state.samples] or state.start
            local function difference(x,y) return math.abs((x-y+180)%360-180) end
            if difference(value, previous) > 1 and difference(value, final) > 1 then
                state.samples[#state.samples + 1] = value
                state.safe = state.safe ~= false and part:GetCollisionEnabled() == 0
            end
            break
        end
    end
end)


local function scene(id, allow_absent)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local package = valid(world) and world:GetFullName():match("^World (.+)$")
    assert(maps[package], "no stock oil-lamp verification map")
    local name = id:match("^(Prop_Lantern_01)_C_%d+$") or id:match("^(Prop_GassLight_0[12])_C_1$")
    local latch = id == "DoorLatch_BP_C_2"
    assert(name or latch, "invalid authored lamp or latch")
    local class = latch and "BlueprintGeneratedClass /Game/Assets/StaticMeshes/Doors/DoorLatch_BP.DoorLatch_BP_C"
        or prop_path .. name .. "." .. name .. "_C"
    local lamp = StaticFindObject(package .. ":PersistentLevel." .. id)
    if allow_absent and (not valid(lamp) or lamp:IsActorBeingDestroyed()) then return end
    assert(valid(lamp) and same(lamp:GetWorld(), world) and same(lamp:GetOuter(), world.PersistentLevel)
        and lamp:GetClass():GetFullName() == class and not lamp:IsActorBeingDestroyed(), "exact stock oil lamp is unavailable")
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and valid(world.NetDriver), "co-op pawn is unavailable")
    return lamp, pawn, world
end

local function focus(pawn, lamp, mesh, box)
    local eye = pawn.Camera:K2_GetComponentLocation()
    for _, target in ipairs({mesh:K2_GetComponentLocation(), box:K2_GetComponentLocation()}) do
        local x,y,z = target.X-eye.X, target.Y-eye.Y, target.Z-eye.Z
        local angle={Pitch=math.deg(math.atan(z, math.sqrt(x*x+y*y))),Yaw=math.deg(math.atan(y,x)),Roll=0}
        pawn.Controller:SetControlRotation(angle)
        pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
        pawn:LookTrace()
        local part = pawn.InteractionTarget_Component
        if (pawn.CanInteract == true and valid(part) and same(part:GetOwner(),lamp))
            or (not valid(part) and same(pawn.ActorBeingLookedAt,lamp)) then return true end
    end
end

local function remote_seat(pawn, world)
    local guest
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and candidate:HasAuthority() and same(candidate:GetWorld(),world)
            and not same(candidate,pawn) and candidate.Dead == false and not candidate:IsActorBeingDestroyed() then
            assert(not guest, "the fixture requires one remote seat")
            guest = candidate
        end
    end
    return assert(guest, "remote seat not found")
end

local function stage(lamp, pawn, participant, mesh, box)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local lamp_pos = mesh:K2_GetComponentLocation()
    local origin, sphere = lamp:K2_GetActorLocation(), box:K2_GetComponentLocation()
    local reach = participant.InteractionRange - 5
    for _, offset in ipairs({80, 105, 145}) do
        for _, dir in ipairs(dirs) do
            local x,y = lamp_pos.X + dir[1]*offset, lamp_pos.Y + dir[2]*offset
            local hit = {}
            if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=lamp_pos.Z+80}, {X=x,Y=y,Z=lamp_pos.Z-160},
                0, false, {pawn,participant,lamp}, 0, hit, true, clear, clear, 0) then
                local floor = hit.OutHit or hit
                local z = floor.ImpactPoint.Z + participant.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3
                local eye = {X=x,Y=y,Z=z+60}
                local function near(point)
                    return (point.X-x)^2 + (point.Y-y)^2 + (point.Z-z)^2 < reach^2
                end
                -- Keep the other player in the sightline test when staging the guest.
                if floor.ImpactNormal.Z > .78 and near(origin) and near(sphere) and near(lamp_pos)
                    and not trace:LineTraceSingle(participant, eye, lamp_pos, 1, true,
                    {participant,lamp}, 0, {}, true, clear, clear, 0)
                    and participant:K2_TeleportTo({X=x,Y=y,Z=z},
                        {Pitch=0,Yaw=math.deg(math.atan(-dir[2],-dir[1])),Roll=0}) then
                    participant:ForceNetUpdate()
                    local p = participant:K2_GetActorLocation()
                    return ("%.2f,%.2f,%.2f"):format(p.X,p.Y,p.Z)
                end
            end
        end
    end
    error("no clear approach to the stock oil lamp")
end

local function select_lighter(pawn, seed)
    local class = StaticFindObject(lighter_class)
    assert(valid(class) and valid(pawn.Save), "stock lighter or scoped save is unavailable")
    local slots = pawn.Save.PlayerEquipment
    local empty
    for index = 5, 8 do
        local item = slots[index][item_class]
        if same(item,class) then
            local held = pawn.EquipedItem
            if pawn:IsLocallyControlled() and (not valid(held) or not same(held:GetClass(),class)) then
                pawn:Equip_NextItem(index-1,false)
            end
            return "selected"
        end
        if not empty and not valid(item) then empty = index end
    end
    if not seed then return "waiting" end
    assert(pawn:HasAuthority() and empty, "no empty utility slot for the private lighter fixture")
    local output = {}
    StaticFindObject(equip_factory):MakeDefaultItem(class,pawn,output)
    slots[empty] = output.Item or output
    slots[empty][item_payload] = class:GetCDO().DataStruct
    assert(same(slots[empty][item_class],class), "stock lighter did not enter the scope's utility slot")
    if pawn:IsLocallyControlled() then pawn:Equip_NextItem(empty-1,false) end
    return "selected"
end

local function lighter_input(pawn, lamp, pressed)
    local item = pawn.EquipedItem
    assert(valid(item) and same(item:GetClass(),StaticFindObject(lighter_class)) and same(item.Player_Ref,pawn),
        "the stock lighter is not in the local player's hands")
    if pressed then
        local eye = pawn.Camera:K2_GetComponentLocation()
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local aimed
        for _, target in ipairs({lamp.PropMesh:K2_GetComponentLocation(),lamp.Sphere:K2_GetComponentLocation()}) do
            local dx,dy,dz = target.X-eye.X,target.Y-eye.Y,target.Z-eye.Z
            local distance = math.sqrt(dx*dx+dy*dy+dz*dz)
            if distance > 0 and distance < item.Burn_ReachRange then
                local hit, scale = {}, item.Burn_ReachRange/distance
                local finish = {X=eye.X+dx*scale,Y=eye.Y+dy*scale,Z=eye.Z+dz*scale}
                if trace:LineTraceSingle(item,eye,finish,2,false,{pawn,item},0,hit,true,clear,clear,0) then
                    hit = hit.OutHit or hit
                    local component = hit.Component and hit.Component:Get()
                    if valid(component) and same(component:GetOwner(),lamp) then
                        local angle = {Pitch=math.deg(math.atan(dz,math.sqrt(dx*dx+dy*dy))),Yaw=math.deg(math.atan(dy,dx)),Roll=0}
                        pawn.Controller:SetControlRotation(angle)
                        pawn.Camera:K2_SetWorldRotation(angle,false,{},true)
                        aimed = true
                        break
                    end
                end
            end
        end
        assert(aimed, "the lighter's stock reach and projectile trace did not reach the lamp")
    end
    local library = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
    local action = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Use_Secondary.IA_Use_Secondary")
    if pressed then
        pawn:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_33(
            library:MakeInputActionValueOfType(1,0,0,0),0,0,action)
    else
        pawn:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_35(
            library:MakeInputActionValueOfType(0,0,0,0),.25,.25,action)
    end
    return tostring(item.Open) .. ";" .. tostring(item.HeldOut)
end

local function run(action,id)
    if crate_types[id] and (action == "watch-lid" or action == "lid-result") then return watch_lid(action,id) end
    if action == "break-state" then
        local lamp = scene(id, true)
        if not valid(lamp) or lamp.bHidden and not lamp:GetActorEnableCollision() then return "removed" end
        local glass = lamp.SM_Prop_Lantern_01_Glass
        return lamp.Broken_0 and (not valid(glass) or not glass:IsVisible()) and "broken" or "intact"
    end
    local lamp,pawn,world = scene(id)
    if action == "break" then
        assert(pawn:HasAuthority() and lamp.Broken_0 == false, "fixture requires the host's intact lamp")
        lamp:PropBroken(lamp:K2_GetActorLocation(),pawn)
        return "dispatched"
    end
    if action == "visual-drift" then
        assert(not pawn:HasAuthority() and lamp.Lit == true and lamp.PointLight:IsVisible(),
            "fixture requires a lit guest oil lamp")
        lamp.PointLight:SetVisibility(false, false)
        assert(lamp.Lit == true and not lamp.PointLight:IsVisible(), "fixture did not isolate the lamp visuals")
        return "visual drift"
    end
    if action == "state" then
        return table.concat({tostring(lamp.Lit), tostring(lamp.PointLight:IsVisible()),
            tostring(lamp.FX_CandleFlame_01_Converted:IsActive()), tostring(lamp.Sphere:GetGenerateOverlapEvents())},";")
    end
    if action == "seed-lighter" then
        assert(pawn:HasAuthority() and same(pawn.Save,world.OwningGameInstance.SaveSlot_Ref))
        local guest = remote_seat(pawn,world)
        assert(valid(guest.Save) and not same(guest.Save,pawn.Save), "guest fixture must use a detached save")
        assert(select_lighter(guest,true) == "selected")
        return select_lighter(pawn,true)
    end
    if action == "select-lighter" then return select_lighter(pawn,false) end
    if action == "use-lighter" or action == "lower-lighter" then
        return lighter_input(pawn,lamp,action == "use-lighter")
    end
    local latch = id == "DoorLatch_BP_C_2"
    if action == "latch-state" then
        assert(latch and valid(lamp.Door))
        return tostring(lamp.Door.Locked) .. ";" .. tostring(lamp.Door.Open)
    end
    if action == "stage" or action == "stage-host" then
        assert(pawn:HasAuthority())
        return stage(lamp,pawn,action == "stage" and remote_seat(pawn,world) or pawn,
            latch and lamp.Box_Knob or lamp.PropMesh, latch and lamp.Box_Knob or lamp.Sphere)
    end
    if action == "host-toggle" then
        assert(pawn:HasAuthority())
        lamp:SendInteractionToActor(pawn,lamp.PropMesh)
        return tostring(lamp.Lit)
    end
    if action == "guest-toggle" then
        assert(not pawn:HasAuthority() and focus(pawn, lamp, latch and lamp.Box_Knob or lamp.PropMesh,
            latch and lamp.Box_Knob or lamp.Sphere), "guest did not focus the lamp or latch")
        local library = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
        local input = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact")
        pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(library:MakeInputActionValueOfType(1,0,0,0),0,0,input)
        pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(library:MakeInputActionValueOfType(0,0,0,0),.05,.05,input)
        return "pressed"
    end
    error("unknown stock oil lamp fixture command")
end

LoopInGameThreadWithDelay(50,function()
    local file=io.open(prefix .. "command.tsv","rb")
    if not file then return end
    local ticket,action,id=file:read(100):match("^(%x+)\t([a-z%-]+)\t([%w_]+)$")
    file:close()
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last=ticket
    local ok,result=pcall(run,action,id)
    local reply=assert(io.open(prefix .. "reply.tsv","wb"))
    local message=tostring(result):gsub("[\r\n\t]"," ")
    reply:write(ticket,"\t",ok and "ok" or "error","\t",message)
    reply:close()
end)
