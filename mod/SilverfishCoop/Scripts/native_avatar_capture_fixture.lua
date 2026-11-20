-- Local screenshot fixture. It waits for a rendered peer and uses the game's fixed Shot command.
local fixture = {}
local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_FIXTURE_CAPTURE") == "1"
local ready_at, captured, framing, fill

function fixture.sync(state, phase, avatar)
    if not enabled or captured or (phase ~= "hosting" and phase ~= "connected") or #state.guests ~= 1 then return end
    local local_pawn, peer = state.local_pawn.pawn, state.guests[1].pawn
    local camera, controller = local_pawn.Camera, state.controller
    if not avatar.visible(peer) or not camera:IsValid() or not controller:IsValid() then return end
    local target = peer:K2_GetActorLocation()
    if not framing then
        local origin = camera:K2_GetComponentLocation()
        local dx, dy = target.X - origin.X, target.Y - origin.Y
        local distance = math.sqrt(dx * dx + dy * dy)
        if distance < 1 then return end
        framing = { X = target.X - dx * 210 / distance, Y = target.Y - dy * 210 / distance, Z = origin.Z + 24 }
        local light_type = StaticFindObject("/Script/Engine.PointLight")
        if light_type:IsValid() then
            fill = state.world:SpawnActor(light_type,
                { X = framing.X, Y = framing.Y, Z = framing.Z + 45 }, { Pitch = 0, Yaw = 0, Roll = 0 })
            if fill:IsValid() then
                local light = fill.PointLightComponent
                light:SetMobility(2)
                light:SetAttenuationRadius(625)
                light:SetIntensity(4500)
            end
        end
    end
    camera:K2_SetWorldLocation(framing, false, {}, true)
    local x, y, z = target.X - framing.X, target.Y - framing.Y, target.Z + 10 - framing.Z
    local distance = math.sqrt(x * x + y * y)
    controller:SetControlRotation({ Pitch = math.deg(math.atan(z, distance)),
        Yaw = math.deg(math.atan(y, x)), Roll = 0 })
    ready_at = ready_at or os.time() + 2
    if os.time() >= ready_at then
        local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        if system:IsValid() then
            system:ExecuteConsoleCommand(local_pawn, "Shot", controller)
            captured = true
        end
    end
end

return fixture
