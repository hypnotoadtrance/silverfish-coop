local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-oil-lamps-"
local maps = {
    ["/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"] = true,
    ["/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"] = true,
}
local class = "BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
local clear = {R=0,G=0,B=0,A=0}
local dirs = {{1,0},{0,1},{-1,0},{0,-1},{.707,.707},{-.707,.707},{-.707,-.707},{.707,-.707}}
local last
local function valid(o) return o and o:IsValid() end
local function same(a,b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function scene(id)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local package = valid(world) and world:GetFullName():match("^World (.+)$")
    assert(maps[package], "no stock oil-lamp verification map")
    assert(id:match("^Prop_Lantern_01_C_%d+$"), "invalid authored oil lamp")
    local lamp = StaticFindObject(package .. ":PersistentLevel." .. id)
    assert(valid(lamp) and same(lamp:GetWorld(), world) and same(lamp:GetOuter(), world.PersistentLevel)
        and lamp:GetClass():GetFullName() == class and not lamp:IsActorBeingDestroyed(), "exact stock oil lamp is unavailable")
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and valid(world.NetDriver), "co-op pawn is unavailable")
    return lamp, pawn, world
end

local function focus(pawn, lamp)
    local eye = pawn.Camera:K2_GetComponentLocation()
    for _, target in ipairs({lamp.PropMesh:K2_GetComponentLocation(), lamp.Sphere:K2_GetComponentLocation()}) do
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

local function stage(lamp, pawn, world)
    local guest
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and candidate:HasAuthority() and same(candidate:GetWorld(),world)
            and not same(candidate,pawn) and candidate.Dead == false and not candidate:IsActorBeingDestroyed() then
            assert(not guest, "the fixture requires one remote seat")
            guest = candidate
        end
    end
    assert(guest, "remote seat not found")
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local lamp_pos = lamp.PropMesh:K2_GetComponentLocation()
    for _, offset in ipairs({105, 145, 80}) do
        for _, dir in ipairs(dirs) do
            local x,y = lamp_pos.X + dir[1]*offset, lamp_pos.Y + dir[2]*offset
            local hit = {}
            if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=lamp_pos.Z+80}, {X=x,Y=y,Z=lamp_pos.Z-160},
                0, false, {pawn,guest,lamp}, 0, hit, true, clear, clear, 0) then
                local floor = hit.OutHit or hit
                local z = floor.ImpactPoint.Z + guest.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3
                local eye = {X=x,Y=y,Z=z+60}
                if floor.ImpactNormal.Z > .78 and not trace:LineTraceSingle(pawn, eye, lamp_pos, 1, true,
                    {pawn,guest,lamp}, 0, {}, true, clear, clear, 0)
                    and guest:K2_TeleportTo({X=x,Y=y,Z=z},
                        {Pitch=0,Yaw=math.deg(math.atan(-dir[2],-dir[1])),Roll=0}) then
                    guest:ForceNetUpdate()
                    local p = guest:K2_GetActorLocation()
                    return ("%.2f,%.2f,%.2f"):format(p.X,p.Y,p.Z)
                end
            end
        end
    end
    error("no clear approach to the stock oil lamp")
end

local function run(action,id)
    local lamp,pawn,world = scene(id)
    if action == "state" then
        return table.concat({tostring(lamp.Lit), tostring(lamp.PointLight:IsVisible()),
            tostring(lamp.FX_CandleFlame_01_Converted:IsActive()), tostring(lamp.Sphere:GetGenerateOverlapEvents())},";")
    end
    if action == "ignite" then
        lamp:SetOnFire(5,0.2,pawn)
        return tostring(lamp.Lit)
    end
    if action == "stage" then
        assert(pawn:HasAuthority())
        return stage(lamp,pawn,world)
    end
    if action == "host-off" then
        assert(pawn:HasAuthority())
        lamp:SendInteractionToActor(pawn,lamp.PropMesh)
        return tostring(lamp.Lit)
    end
    if action == "guest-off" then
        assert(not pawn:HasAuthority() and lamp.Lit and focus(pawn,lamp), "guest did not focus the lit oil lamp")
        local ok,reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(),true)
        assert(ok and (reason == "stock_started_consumed" or reason == "stock_started"), tostring(reason))
        return reason
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
