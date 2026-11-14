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
| No offline pipeline archive (`--gpu none`) | `backend.py` `prepare_game` | Not needed for correctness | **Big win to test**: nxnative can build a `g18p` archive on the Mac, so the TV doesn't compile pipelines at first use (the stutter the first time a scene shows) |
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

## Phase 1: baseline, A15 settings on the A19 (half a day)

Pick a game known to play on the A15, so a failure points at the A19 and not the game: **Super Mario Odyssey 1.3.0** first, then **Tears of the Kingdom** (1.4.2 or 1.4.3).

1. Build & Install with the defaults (Handheld, texture cap 512, Frame rate as offered, Hold 30 fps on).
2. Play 10 minutes of a fixed route: the same save, the same path, the same camera moves. Write the route down; every later test repeats it.
3. Open **Last Run**, and keep the log: it's in the game's `<title ID>_build` folder, or copy it from the TV with **Last Run**.

Record (template in "Results" below):

- **Device line:** search the log for `BC textures`. If it says "this GPU has no BC formats … storing them decompressed", the A19 is not detected as BC-capable: that's a bug to fix first. If the line is missing, BC is native.
- **Memory:** the `memory up to frame` lines (resident MB, GPU allocated) and `headroom to the system memory limit`. Headroom + footprint at the same moment is the A19's per-app limit. On the A15 it was 2,097 MB.
- **Guest file:** `guest memory: … MB backed by a file`.
- **Frame rate:** the `[nvn] on-screen up to frame` lines: frames shown, dropped, p99 frame time, and footprint. Plus the fps counter, and the stutters you notice on the route.
- **Heat:** minutes until the first `[nvn] thermal state` line, if any (the frame step then holds 30 fps).
- **tvOS kills:** Last Run names a `per-process-limit` JetsamEvent if tvOS ended the game.

Pass: the game plays as on the A15 or better, and you know the A19's memory limit and BC state.

## Phase 2: take the A15 workarounds off, one at a time (1 to 2 days)

Change one thing per run, on the same route, and record each.

| Run | Change | How | Watch |
|---|---|---|---|
| 2a | Texture cap off | Texture detail: highest (or `DM_BC_MAX_DIM=0`) | Sharper textures; GPU allocated and footprint |
| 2b | Docked | Mode: Docked | 1080p; fps; memory |
| 2c | Full 3D resolution | 3D resolution: Full (TotK, BotW) | fps in heavy areas |
| 2d | 60 fps | Frame rate: 60 (games with a frame step) | Holds 60? Long frames? |
| 2e | Thermal cap off | Hold 30 fps when hot: off | Clocks drop when hot regardless; does the game stay smooth over 30 minutes? |
| 2f | Guest memory anonymous | `DM_GUEST_FILE=0` (see below) | Only if phase 1's limit is far above the game's need: faster loads if the file was paging |

How to set a switch the Mac app has no control for: add it to the env file the build writes (`<title ID>_build/tvport-env.txt`, from `write_env` in `backend.py`). The app reads it at launch. Phase 3 turns the useful ones into settings.

Pass: a table of which workarounds the A19 still needs, per game.

## Phase 3: code changes for the A19 (work with Claude on the `a19` branch)

Each item is a small change, landed and tested one at a time:

1. **Device report at launch.** One log line with the model (`hw.machine`), GPU name and Metal family, BC support, physical RAM, the memory limit (`os_proc_available_memory` + footprint) and the thermal state. Last Run shows it. Makes every later result self-describing.
2. **Per-device defaults in the Mac app.** `backend.py` already lists TVs with `devicectl` (`apple_tvs`, from `hardwareProperties`). Read the model from there, and for the A19 default to Docked, texture cap off and full 3D resolution, keeping today's defaults for the A15. The settings stay changeable.
3. **Offline pipeline archive for the A19.** Pass `--gpu g18p` instead of `--gpu none` when the TV is an A19 (`prepare_game`). Check that tvOS loads an archive made for iOS (the Metal libraries already are iOS ones). Measure stutters on the first run of the route with and without it. This is likely the biggest smoothness win.
4. **Guest file only when needed.** If phase 1 shows a much higher limit, turn `DM_GUEST_FILE` on only when the game's memory pool doesn't fit the room (upstream already does exactly this on iOS: `guest_file_wanted`).
5. **Settings for what phase 2 found useful** (for example memory-file off, thermal cap) in the Mac app, shown only on the devices where they matter.

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

One row per run. Keep the logs. Put finished results in the game's note (`dm/games/<title ID>/README.md`) and TVPort's tested-games table (`README.md`).

| Date | Branch / commit | Game, version | Mode | Tex cap | 3D res | FPS target | Avg fps | Long frames on route | Peak footprint MB | Limit MB | BC native? | Minutes to hot | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | | |

Device: Apple TV 4K (2026, A19), tvOS ____, RAM ____ (from the device report), TV ____ (4K/HDR/refresh rate), controller ____.

## Working with Claude on this

- Claude can push to this fork, but can't build or run anything Apple: it has no Mac or TV.
- After each phase, paste or commit the results table and the relevant log lines (not the whole log). Claude reads them, writes the next code change on `a19`, and pushes it. You pull, build and run.
- Build errors: paste the error lines with file and line.
