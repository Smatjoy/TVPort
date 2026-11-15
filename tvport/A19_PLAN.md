# TVPort on the Apple TV 4K (A19): plan

Goal: the best experience TVPort can give on the 2026 Apple TV 4K (A19), with games you own and dumped from your own Switch or Switch 2. TVPort was built and tuned on the 2022 model (A15, 4 GB, no BC texture formats). Most of its defaults are workarounds for that chip. This plan finds out which of them the A19 still needs, removes the ones it doesn't, and then pushes quality and frame rate as far as the hardware goes.

Branches in this fork (`Smatjoy/TVPort`):

| Branch | What it is | State |
|---|---|---|
| `tvport` | The original TVPort, as tested on the A15 | Builds and runs |
| `a19` | This plan, and the A19 work as it lands | Based on `tvport` |
| `merge-upstream-nxnative` | `tvport` + 760 nxnative commits (PR to 68hctmzzjf-svg/TVPort) | **Never compiled** |

Work happens on `a19`. Phases 1 to 4 use the tested engine, so any problem found there is about the A19 and not the merge. Phase 5 moves `a19` onto the merge.

## What we know about the A19 Apple TV

- **Chip:** A19, the iPhone 17's chip. Apple says the CPU is up to 50% faster and the GPU up to 2x faster than the A15 model.
- **RAM:** Apple doesn't publish it. 8 GB is likely, up from 4 GB. Phase 1 measures what an app actually gets.
- **GPU:** A19-class GPU (likely Metal GPU family Apple 10; the device report in phase 3 confirms it). It should have BC texture formats, as every iPhone from the A17 Pro on does. nxnative names its pipeline-archive architecture `g18p` (A19 / A19 Pro).
- **Storage:** 128 GB.

Sources: [MacRumors](https://macrumors.com/2026/10/13/new-apple-tv-4k-announced), [EveryMac](https://everymac.com/systems/apple/apple-tv/specs/apple-tv-4k-4th-gen-a19-2026-a3593-specs.html).

## What TVPort does today because of the A15

| Workaround | Where | Why on the A15 | Expected on the A19 |
|---|---|---|---|
| BC textures decoded to RGBA, on the GPU or CPU | `nvn_mtl.m` `no_bc` | The A15 has no BC formats | **Off by itself**: `no_bc()` asks Metal `supportsBCTextureCompression`. Confirm in the log |
| Texture cap 512 px (`DM_BC_MAX_DIM`), default in the Mac app | `backend.py` `--texture-cap` default 512 | Decoded BC textures cost 4-8x memory | Only applies to decoded BC textures. If BC is native, the cap does nothing. Default it per device |
| Handheld mode by default (720p) | `backend.py` `--mode` default `handheld` | GPU time and memory | Docked (1080p) should fit |
| 3D resolution 75% for Tears of the Kingdom | Last-run advice | GPU time | 100% |
| Guest memory in a file (`DM_GUEST_FILE`) | `hle_svc.c` `guest_block` | tvOS kills an app at 2,097 MB of footprint | Measure the A19's limit. The file may still be needed, or only for large games |
| Hold 30 fps when hot (`DM_THERMAL_CAP`) | frame step | The A15 throttles after about 20 minutes | Measure. May never trigger |
| No offline pipeline archive (`--gpu none`), pipelines through `MTLDevice` (`pso_mtl4` off) | `backend.py` `prepare_game`; profile key `pso_mtl4` | Not needed for correctness; only Breath of the Wild was tested with `pso_mtl4` on iOS | **Big win to test**: nxnative can build a `g18p` archive on the Mac, so the TV doesn't compile pipelines at first use (the stutter the first time a scene shows) |
| `-mcpu=apple-a15` | `dm/ios/project_tv.yml` | Oldest supported chip | Keep. One build must still run on the A15. Faster code for the A19 is phase 6 at most |

## Phase 0: set up the Mac (once)

Needs an Apple silicon Mac, macOS 26.6+, Xcode 27.2 with the tvOS platform and the Metal toolchain, an Apple ID in **Xcode > Settings > Accounts**, and the TV in developer mode, paired in **Xcode > Window > Devices and Simulators**.

```bash
git clone https://github.com/Smatjoy/TVPort.git
```

```bash
cd TVPort && git checkout a19
```

Build TVPort.app from this checkout, so it runs this checkout's engine (`tvport/README.md`, Run it):

```bash
cd tvport/app && xcodegen generate && cd ../..
```

```bash
xcodebuild -project tvport/app/TVPort.xcodeproj -scheme TVPort -configuration Release -derivedDataPath tvport/app/build build
```

```bash
open tvport/app/build/Build/Products/Release/TVPort.app
```

Keep the checkout where it is: that TVPort.app runs `tvport/backend.py` from it. Let **Setup** install what it lists.

Keep your games and `prod.keys` outside the repository. Nothing from a game goes into a commit or a log you share: logs name files and addresses, not content, but check before pasting.

## Approach: one run per game, then everything on

Repeating a fixed route per setting takes days, so this plan doesn't. Each game gets **one run with the A15 defaults**: 5 to 10 minutes, any path, the frame-rate counter on, then **Last Run**. That run's log is enough to learn what the A19 is (the facts below), and the code changes in phase 3 then turn on everything the A19 can take at once. A run that goes worse than the A15 gets a second look, setting by setting; one that goes better doesn't.

## Phase 1: one baseline run per game (Odyssey 1.4.1 first)

Odyssey first, because it plays on the A15, so a failure points at the A19. The local update is `v393216`, which is **1.4.1** (display version and all three build IDs match the profile); TVPort's README said 1.3.0. Then Tears of the Kingdom 1.4.2.

Build & Install with the defaults (Handheld, Medium texture detail 512, Frame rate as offered, Hold 30 fps on), play 5 to 10 minutes, press **Last Run**. Claude reads the log from `<title ID>_build` and records:

- **BC:** a log line "this GPU has no BC formats … storing them decompressed" means the A19 isn't detected as BC-capable: a bug to fix first. No such line: BC is native.
- **Memory limit:** `headroom to the system memory limit` + footprint at the same moment. On the A15 it was 2,097 MB.
- **GPU architecture:** the name Metal gives the TV's GPU (is it `g18p`, the iPhone 17's?), which decides the pipeline archive.
- **Frame rate:** the `[nvn] on-screen up to frame` lines (shown, dropped, p99), and what you saw.
- **Heat:** whether a `[nvn] thermal state` line came, and when.
- **Kills:** a `per-process-limit` JetsamEvent in Last Run.

## Phase 2: (folded into phase 3)

The one-at-a-time runs are dropped. Phase 1's facts decide each A15 workaround directly: BC native makes the texture cap do nothing; the memory limit decides Docked, full 3D resolution and the guest file; the GPU architecture decides the archive. If a phase 3 build plays worse than phase 1, its settings come off one at a time until it doesn't.

## Phase 3: code changes for the A19 (work with Claude on the `a19` branch)

Landed together in one build once phase 1's facts are in, and checked with one run per game:

**Status (2026-11-15, after run 3): 60 Hz and items 1, 2 and 3 work on the TV.** Run 2 found the archive built for iOS, which tvOS refuses. With the tvOS archive (`pso_offline.py --platform tvos`), run 3 compiled no base at launch. The memory limit is 2,098 MB, the same as the A15's, so item 4 stays as it is (the guest file is needed).

- **60 Hz** (from phase 1): tvOS's `front_screen_match` in `dm/ios/Sources/front_ios.m` asks for a display mode at the game's rate through `AVDisplayManager.preferredDisplayCriteria` (30 fps asks for 60 Hz). tvOS follows it only with Settings > Video and Audio > Match Content > Match Frame Rate on; the log says which. `DM_SCREEN_MATCH=0` turns it off.
- **Item 1:** `front_ios_device_report` logs `front end: device …`: model, GPU name and architecture, Metal family, BC, RAM, memory limit (available + footprint), thermal state.
- **Item 2:** `backend.py` `TV_MODELS` knows `AppleTV14,1` (A15) and `AppleTV18,1` (A19). A game added with an A19 selected starts at Docked and Full texture detail. 3D resolution already defaults to Full.
- **Item 3:** for an A19, the build sets `DM_PSO_GPUS=g18p` (the archive, made by `build_ipa.sh` with `metal-tt`) and writes `DM_PSO_MTL4=1`. If the TV's architecture isn't `g18p` or tvOS can't open an iOS archive, the log says so (`pipeline archive bundled with the app: …`) and the TV builds the pipelines itself, as before.

1. **Device report at launch.** One log line with the model (`hw.machine`), GPU name and Metal family, BC support, physical RAM, the memory limit (`os_proc_available_memory` + footprint) and the thermal state. Last Run shows it. Makes every later result self-describing.
2. **Per-device defaults in the Mac app.** `backend.py` already lists TVs with `devicectl` (`apple_tvs`, from `hardwareProperties`). Read the model from there, and for the A19 default to Docked, texture cap off and full 3D resolution, keeping today's defaults for the A15. The settings stay changeable.
3. **Offline pipeline archive for the A19.** On iOS and tvOS the archive is read only on the Metal 4 compiler path (`pso_mtl4`, `DM_PSO_MTL4=1`), which every game except Breath of the Wild leaves off on iOS because each was tested without it (`dm/games/README.md`, `pso_mtl4`). For the A19: build with `--gpu <the TV's architecture>` (from phase 1's log; `g18p` if it matches the iPhone 17) and run with `DM_PSO_MTL4=1`, so the TV doesn't compile pipelines the first time a scene shows. Compare first-run stutters with phase 1. This is likely the biggest smoothness win.
4. **Guest file only when needed.** If phase 1 shows a much higher limit, turn `DM_GUEST_FILE` on only when the game's memory pool doesn't fit the room (upstream already does exactly this on iOS: `guest_file_wanted`).
5. **Settings for what phase 1 showed matters** (for example memory-file off, thermal cap) in the Mac app, shown only on the devices where they matter.

## Phase 4: quality on a 4K TV (after phase 3)

- **Output resolution.** Find what size the game's drawable is on the TV (log the `CAMetalLayer` drawable size once). The Switch renders 1080p docked; tvOS scales that to 4K. Options to test:
  - leave it to tvOS (today);
  - MetalFX spatial upscale 1080p→4K on our side (more GPU, better than the TV's scaler?);
  - higher internal resolution where a game has a render-scale stub (above 1.0 would be new work).
- **Game upscalers on MetalFX.** Upstream has `DM_UPSCALER=metalfx` for games that use agl's NDSR upscaler (mostly Switch 2 games): available after phase 5.
- **HDR / frame-rate matching:** tvOS can switch the TV to the content's frame rate (`AVDisplayCriteria`). A 30 fps game on a 60 Hz output looks fine; check whether matching helps judder for 30 fps games.

## Phase 5: move to upstream nxnative (the merge branch)

Upstream brings a lot the A19 can use: partial texture re-uploads, large-pool chunks, MetalFX for game upscalers, 120 fps targets (`fps_max 120`), and Switch 2 games.

1. `git checkout merge-upstream-nxnative` and build TVPort.app from it, as in phase 0. Expect build errors: it has never been compiled. Note each error (file, line, message). The suspects are tvOS-only API gaps: `open_dprotected_np`, `registerForTraitChanges`, UIKit APIs in `front_ios.m` and `DmGamepad.m`.
2. Repeat phase 1's route on the same game and compare with your phase 1 and 2 results.
3. Once it builds and plays: merge it into `a19` (`git merge merge-upstream-nxnative`), and send the fixes to the PR branch too.
4. **Odyssey's bloom flashes** may come back here (the sampler-pin fix was dropped in the merge). Check the moon and snow in Odyssey. If it flashes, porting that fix is the next code task.

## Phase 6: Switch 2 games (after phase 5)

Upstream nxnative runs Switch 2 games on Macs (Mario Kart World, Donkey Kong Bananza, Kirby Air Riders, and others in its README). The A19 with about 8 GB is the first Apple TV where some might fit.

1. TVPort's Mac app takes `.nsp`, `.xci`, `.nsz`, `.xcz` only (`SOURCE_SUFFIXES` in `backend.py`). Add `.nup` files and dump folders, the way upstream's NSP script takes them.
2. Start with the lightest Switch 2 game you own. Watch memory first: Switch 2 games assume 12 GB.
3. Record results per game, as above.

## Phase 7: SideTVstore (optional)

To install without the Mac each time, TVPort needs an **Export IPA** option: an unsigned tvOS IPA with the game files inside (SideTVstore can't copy `romfs.img` into the app afterwards). Caveats: the IPA is as big as the game; a free Apple ID drops the Increased Memory Limit entitlement and expires apps after 7 days; SideTVstore's refresh on large apps is untested.

## Results

### Phase 0 (2026-11-14, `a19` at `3ed49818`)

| Check | Result |
|---|---|
| macOS | 27.2.1, Apple silicon |
| Xcode | 27.2 (27B52) |
| tvOS / iOS platforms | SDK 27.2 / SDK 27.2 |
| Metal toolchain | 32023.922 (27B47), installed |
| xcodegen | 2.46.0 |
| zstd, glslang | zstd present; glslang 16.6.0 installed with Homebrew (was missing) |
| .NET SDK | 10.0.403 (`~/.dotnet`) |
| Apple ID | Free team signed in to Xcode |
| TVPort.app | Built from this checkout, Release, no errors; backend tests 44/44 pass |
| TVPort Python | Made by the app's Setup at first start |
| Apple TV (A19) | Paired and connected: Apple TV 4K (4th generation), `AppleTV18,1`, chip ID 0x8150, tvOS 27.2.1, developer mode on. TVPort's `backend.py devices` lists it |

### Phase 1 findings (Odyssey 1.4.1, 2026-11-15)

Built Docked with the texture cap off (not the A15 defaults), played the first two kingdoms for 8.7 minutes. It played without a kill or a hitch other than the two below.

- **50 fps, not 60:** every on-screen window has p99 exactly 20.00 ms and the display link ticks once per 20 ms: the TV output is 50 Hz, while the game's vsync runs at 60 Hz. TVPort never asks tvOS for a refresh rate. Fix: ask for 60 Hz with `AVDisplayManager.preferredDisplayCriteria` (works when Match Content > Match Frame Rate is on), or set Settings > Video and Audio > Format to a 60 Hz mode.
- **Shader stutter:** 24.8 s of pipeline compiles on the submit thread in 8.7 minutes, worst 2.8 s in one 300-frame window (entering a new kingdom). The pipeline archive (phase 3 item 3) is the fix; the bundled list of pipelines seen (`tvport-shaders.tsv`) helps from the next build on.
- **900p:** the game's own docked resolution (1600×900). Higher is phase 4 work.
- **BC is native**, the texture cap does nothing on the A19. Peak footprint 1,662 MiB with the guest memory in a file; the memory limit isn't logged (phase 3 item 1).
- **Heat:** thermal state 0 for the whole run.

### Phase 3 run 2 findings (Odyssey 1.4.1, 2026-11-15, `cd25cea7`)

- **60 Hz works:** `asked tvOS for a 60 Hz display mode … (the screen runs at 50 Hz)`, then `display mode switch ended, the screen runs at 60 Hz`; p99 16.67 ms in most windows.
- **Device report:** `AppleTV18,1`, `applegpu_g18p`, Metal family Apple10, BC native, RAM 8,055 MB, **memory limit 2,098 MB**, the same as the A15. The guest file stays.
- **Archive refused:** `this system cannot open …/pso/applegpu_g18p.mtl4a (deployment target … is not compatible with current OS)`. The archive was built for iOS (`platform ios 27.0`), and tvOS opens only a tvOS one. The TV then built all 20,740 bases itself in 557 s, reaching thermal state 3 at 108 s and cooling back to 1 by 718 s. Those bases are now in the TV's own archive (`Library/Caches/pso_archive`), so the next launch doesn't do this again. Fixed in `pso_offline.py` (`--platform tvos`) and `build_ipa.sh`; a three-pipeline tvOS archive built from the iOS libraries without complaint ([reference entry](../docs/reference/shaders.md#offline-pipeline-archive-made-for-ios-on-an-apple-tv)).
- **Stutter left in play:** pipelines outside the shader pair list (`pulling base not made yet`, up to 1.6 s in one 300-frame window, 6.4 s in all over 2.75 minutes of play). These are the ones the pair list doesn't predict. The game recorded 343 of them (`pso_user.tsv`, 3,183 lines). The TV warms them at launch, and TVPort puts them into the next build.

### Phase 3 run 3 findings (Odyssey 1.4.1, 2026-11-15, tvOS archive)

- **Archive opens:** `pipeline archive bundled with the app: …/applegpu_g18p.mtl4a, 815.1 MB, opened in 659.4 ms`, and the archive `holds the bases of every shader pair (16 of 16 probed bases found)`. The launch warm-up took 11.9 s for 3,183 lines, 1,488 bases from the archive and **0 compiled**.
- **Stutter:** 1.9 s of pipeline work on the submit thread in 6.6 minutes of play, including a new kingdom, and at most 267 ms in any 300-frame window (run 1: 24.8 s and 2.8 s). The thermal state never changed.

### ExeFS mods (2026-11-15)

Community code patches (`.pchtxt`, `.ips`) are applied on the Mac before the rewrite (`dm/tools/exefs_mods.py`, the NSP script's `--mods`; TVPort passes a game's `mods` folder beside its NSPs and prepares the game again when that folder changes). A cheat can't write code on tvOS; a patch made here becomes part of the signed app. Installed for Odyssey 1.4.1: `[1920x1080 v1.4.1]` (28 patches) and `[Improved Level of Detail v1.4.1]` (4 patches: LOD bias, anisotropic filtering, FXAA off). The modded `main` rewrites to the same fixups and changed words as the original, and differs from it only in the 71 bytes the mods write. Not yet run on the TV. Upstream's proposal for the same feature: seedds/nxnative#2294.

### Runs

One row per run. Keep the logs. Put finished results in the game's note (`dm/games/<title ID>/README.md`) and TVPort's tested-games table (`README.md`).

| Date | Branch / commit | Game, version | Mode | Tex cap | 3D res | FPS target | Avg fps | Long frames on route | Peak footprint MB | Limit MB | BC native? | Minutes to hot | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-15 | `a19` `f4a25afc`+`._` fix | Odyssey 1.4.1 | Docked | Off (0) | (game's own, 1600×900) | 60 (game) | 50 (TV output at 50 Hz; p99 20.00 ms) | 608 frames dropped in 8.7 min; 24.8 s of pipeline compiles on the submit thread, up to 2.8 s per 300 frames | 1,662 | Not logged (no kill) | Yes (no "no BC formats" line) | Never: thermal 0 throughout | Metal device "Apple A19 GPU"; guest memory 4,096 MB in a file; GPU allocated up to 2,697 MB; pipelines through `MTLDevice`, no archive |
| 2026-11-15 | `a19` `cd25cea7` | Odyssey 1.4.1 | Docked | Off (0) | (game's own, 1600×900) | 60 (game) | 60 (screen switched from 50 to 60 Hz; p99 16.67 ms, some windows 33.33) | 90 frames dropped in 2.75 min of play; 6.4 s on the submit thread, up to 1.6 s per 300 frames, all pipelines outside the pair list | 1,386 | 2,098 | Yes | Thermal 3 at 108 s during the TV's own 557 s prebuild (archive refused: made for iOS); back to 1 by 718 s | Rumble works; `pso_mtl4` on; device report logged |
| 2026-11-15 | `a19` + tvOS archive | Odyssey 1.4.1 | Docked | Off (0) | (game's own, 1600×900) | 60 (game) | 60 (72 of 79 windows p99 16.67 ms) | 203 frames dropped in 6.6 min with a new kingdom; 1.9 s on the submit thread, at most 267 ms per 300 frames | 1,838 | 2,098 | Yes | Never: no thermal change | Archive opened (815 MB, 0.66 s); 0 bases compiled at launch |

Device: Apple TV 4K (2026, A19, `AppleTV18,1`), tvOS 27.2.1, RAM 8,055 MB, memory limit 2,098 MB (device report), TV ____ (4K/HDR/refresh rate), controller ____.

## Working with Claude on this

- Claude can push to this fork, but can't build or run anything Apple: it has no Mac or TV.
- After each phase, paste or commit the results table and the relevant log lines (not the whole log). Claude reads them, writes the next code change on `a19`, and pushes it. You pull, build and run.
- Build errors: paste the error lines with file and line.
