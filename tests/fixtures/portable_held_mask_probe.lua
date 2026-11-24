local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-held-mask-"
local dock = "World /Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local pistol = "/Game/Classes/Weapons/Wep_RustyPistol.Wep_RustyPistol_C"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local statics_class = "/Script/Engine.Default__GameplayStatics"
local identity = {Translation={X=0,Y=0,Z=4}, Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
local last, added

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function unwrap(object)
    if type(object) == "userdata" and type(object.type) == "function" and object:type() == "RemoteUnrealParam" then
        return object:get()
    end
    return object
end

local function meshes(actor)
    return actor:K2_GetComponentsByClass(StaticFindObject(mesh_class)) or {}
end

local function selected()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == dock and valid(world.NetDriver), "expected the private Dock")
    local driver = world.NetDriver
    local guest = valid(driver.ServerConnection) == true
    local statics = StaticFindObject(statics_class)
    assert(valid(statics))
    local pawn = statics:GetPlayerPawn(world, 0)
    if not guest then
        local peers = driver.ClientConnections
        assert(peers:GetArrayNum() == 1 and valid(peers[1].PlayerController), "expected a single admitted guest")
        pawn = peers[1].PlayerController.Pawn
    end
    if not valid(pawn) or pawn:IsActorBeingDestroyed() then return end
    assert(same(pawn:GetWorld(), world) and pawn:IsLocallyControlled() == guest and pawn:HasAuthority() ~= guest)
    local stock = pawn.EquipedItem
    if not valid(stock) or stock:IsActorBeingDestroyed() or not same(stock:GetClass(), StaticFindObject(pistol)) then
        return
    end
    assert(same(stock:GetWorld(), world) and same(stock:GetAttachParentActor(), pawn)
        and (same(stock:GetOwner(), pawn) or same(stock.User, pawn)), "expected the guest's selected stock pistol")
    local frame = stock.Mesh_Frame
    assert(valid(frame) and same(frame:GetOwner(), stock) and valid(frame.StaticMesh), "expected the stock pistol frame")
    return world, guest, pawn, stock, frame
end

local function attached_to_hand(copy)
    local node = copy
    for _ = 1, 4 do
        local parent = node:GetAttachParent()
        if not valid(parent) then return false end
        local socket = node:GetAttachSocketName()
        if socket:ToString() == "Weapon_Socket01" then return parent:DoesSocketExist(socket) end
        node = parent
    end
    return false
end

local function projection(pawn, frame)
    local result
    for _, raw in ipairs(meshes(pawn)) do
        local copy = unwrap(raw)
        if valid(copy) and same(copy:GetOwner(), pawn) and same(copy.StaticMesh, frame.StaticMesh) and attached_to_hand(copy)
            and copy:IsVisible() and not copy.bHiddenInGame and not copy.bOnlyOwnerSee then
            assert(not result, "multiple pawn-owned hand copies")
            result = copy
        end
    end
    return result
end

local function existing(world, stock)
    if not added then return end
    assert(world:GetAddress() == added.world and stock:GetAddress() == added.stock,
        "the scoped stock actor changed")
    for _, raw in ipairs(meshes(stock)) do
        local component = unwrap(raw)
        if valid(component) and component:GetAddress() == added.component then
            assert(same(component:GetOwner(), stock), "the fixture mesh's owner changed")
            return component
        end
    end
end

local function inject(world, stock, frame)
    assert(not added and valid(stock.RootComponent) and same(stock.RootComponent:GetOwner(), stock))
    local mesh = stock:AddComponentByClass(StaticFindObject(mesh_class), true, identity, true)
    assert(valid(mesh) and same(mesh:GetOwner(), stock), "could not add the test mesh to the stock actor")
    local ok, reason = pcall(function()
        mesh:SetMobility(2)
        mesh:SetIsReplicated(false)
        mesh:SetCollisionEnabled(0)
        mesh:SetGenerateOverlapEvents(false)
        mesh:SetStaticMesh(frame.StaticMesh)
        stock:FinishAddComponent(mesh, true, identity)
        assert(mesh:K2_AttachToComponent(stock.RootComponent, FName("None"), 0, 0, 0, false))
        mesh:SetOnlyOwnerSee(false)
        mesh:SetHiddenInGame(false, false)
        mesh:SetVisibility(true, false)
        assert(same(mesh.StaticMesh, frame.StaticMesh) and same(mesh:GetAttachParent(), stock.RootComponent)
            and not mesh.bReplicates and mesh:IsVisible() and not mesh.bHiddenInGame and not mesh.bOnlyOwnerSee)
    end)
    if not ok then
        mesh:K2_DestroyComponent(stock)
        error(reason, 0)
    end
    added = {world=world:GetAddress(), stock=stock:GetAddress(), component=mesh:GetAddress()}
    return mesh
end

local function bit(value) return value and 1 or 0 end

local function run(action)
    local world, guest, pawn, stock, frame = selected()
    if not world then return "pending" end
    if guest then
        assert(action == "owner", "the owner seat is read-only")
        return table.concat({stock:GetAddress(), frame:GetAddress(), bit(frame.bOnlyOwnerSee),
            bit(frame.bHiddenInGame), bit(frame:IsVisible()), #meshes(stock)}, ";")
    end

    local copy = projection(pawn, frame)
    if action == "clear" then
        local component = existing(world, stock)
        if valid(component) then
            component:K2_DestroyComponent(stock)
            assert(not existing(world, stock), "fixture mesh remains registered")
        end
        added = nil
    end
    if not valid(copy) or not frame.bOnlyOwnerSee then return "pending" end
    local late
    if action == "add" then
        late = inject(world, stock, frame)
    else
        assert(action == "read" or action == "clear")
        late = existing(world, stock)
    end
    return table.concat({stock:GetAddress(), frame:GetAddress(), copy:GetAddress(), bit(frame.bOnlyOwnerSee),
        valid(late) and late:GetAddress() or 0, bit(valid(late) and late.bOnlyOwnerSee)}, ";")
end

LoopInGameThreadWithDelay(40, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(128)
    file:close()
    if not request or request == last then return end
    last = request
    local ticket, action = request:match("^([a-f0-9]+)\t(%a+)$")
    if not ticket or #ticket ~= 32 then return end
    local ok, result = pcall(run, action)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, ok and "\tok\t" or "\tfailed\t", tostring(result), "\n")
    reply:close()
end)
