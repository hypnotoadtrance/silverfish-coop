# Native integration status

The staged loopback build has one host and one guest. The campaign store, guest catalog, action channel, and checkpoint format are active in both playtest maps. See the [locked design](DESIGN.md) for release requirements.

| Area | Verified or implemented in the staged build | Work before campaign play |
| --- | --- | --- |
| Hosting and players | Authenticated Direct IP, guest-owned character selection, native replicated movement, reconnect and host restart | More than one guest, real-network validation, and Steam Invite |
| Combat and respawn | Fixture damage, crouch and speed restrictions, revive, locked wipe, and checkpoint placement. The qualified local lab routes the stock death wrapper to co-op. | Authoritative weapon and NPC damage, permanent native health ownership, friendly-fire routing, and native respawn UI |
| Loot and inventory | Replicated toy pickup, one committed winner, independent durable co-op inventory, and owner-only bag contents | Stock item and equipment routing, trait-aware native loot rolls, and visual backpack actors |
| Wallet, shops, and quests | Shared wallet transaction, native cash projection, read-only placed shop offers, and native start/read-back for one authored quest | Native shop checkout and dialogue, stock objective routing, and end-to-end quest turn-in rewards |
| World and travel | Authoritative scene snapshots and host-led authenticated travel between the two playtest maps | Native new-game setup and restoration, clock and weather projection, and a gate on real zone boundaries |

The cooked game uses the same game-instance save reference for both server-side pawns by default. A live probe moved the guest to a distinct transient SaveGame object and read 125 guest HP with the host still at 250 HP. Native inventory and dialog logic still calls the shared GameInstance or player/controller index zero. Replacing the save reference alone leaves those call sites shared.

The local death overlay qualifies one exact cooked build and changes the common Blueprint death function. The live lab ran the stock direct-damage call against the transient guest save. Guest health crossed from 125 to -1, host health stayed at 250, and the guest remained on the same pawn for co-op revival. Its code generates the overlay from local extracted inputs. Epic-linked GitHub access now gives us the exact pinned UEPseudo headers. A matched v4.0.0 loader and C++ add-on reproduced the same lethal-damage result on a stock-package stage through a pre-call hook. The C++ canary activates around the explicit diagnostic call. See the [native build and lab instructions](../native/README.md). [UE4SS build guide](https://github.com/UE4SS-RE/RE-UE4SS/blob/v4.0.0/docs/guides/creating-a-c%2B%2B-mod.md)

The stock Silverfish build ships Unreal's Null subsystem. A Steam Invite test needs an authorized Steamworks integration under the game's App ID and two separately entitled Steam sessions for a real invite round-trip. The existing Direct IP path supports local two-process testing without Steam. [Steamworks setup](https://partner.steamgames.com/doc/sdk/api) · [Steam lobby invites](https://partner.steamgames.com/doc/api/ISteamMatchmaking#InviteUserToLobby)
