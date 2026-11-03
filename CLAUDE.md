# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FlyMotion turns a natural-language request (French or English) into motion for Pollen Robotics' Microduck. A small Qwen model served by Ollama writes a plan (actions, values, order); a Python bridge validates, clamps and compiles it into velocity commands for `robotd`. An Electron test app plays those commands in Pollen's browser simulator (MuJoCo + ONNX policies). There are no fine-tuned weights yet: the gains come from the plan format, the system prompt, the Modelfile parameters and the bridge.

## Commands

```sh
python3 -m unittest discover -s tests -v                          # all Python tests (no Ollama needed)
python3 -m unittest tests.test_flymotion.GaitTest                 # one class
python3 -m unittest tests.test_flymotion.GaitTest.test_a_tight_circle_widens_to_stay_above_the_threshold
npm ci --ignore-scripts && npm test                               # app tests (node --test, no Electron binary needed)
node --test app/test/driver.test.js                               # one app test file
python3 scripts/gen_modelfiles.py                                 # regenerate Modelfile / Modelfile.lite
python3 scripts/gen_modelfiles.py --check                         # what the tests run
sh -n scripts/publish-ollama.sh && sh -n scripts/update-ollama-pages.sh && sh -n training/export_gguf.sh   # CI syntax check
```

Running things (need Ollama and a pulled model):

```sh
python3 -m flymotion "fais un carré de 50 cm"                    # plan + bridge simulator (default --transport sim)
python3 -m flymotion --plan-only "..."                            # just the JSON plan
python3 -m flymotion serve --robot microduck-sim                  # JSON-lines protocol used by the app
python3 bench/run.py --model flymotion0.2lite [--robot microduck-sim] [--only 3,8] [--out docs/bench/x.json]
npm start                                                         # Electron app
```

CI (`.github/workflows/test.yml`) runs the Python tests on 3.11 (Raspberry Pi OS) and 3.13, and the app tests on Node 22. The `flymotion/` package must stay standard-library only and Python 3.9+ compatible: it is copied onto the robot as is.

## Architecture

**The model only writes `{"safe", "steps", "repeat"}`** (key order matters: `safe` first, steps before `repeat`, both measured to help the 2B model). Directions are words with positive values; the bridge does every sign, speed, duration and limit. Never reintroduce signed numbers, raw velocities or a model-written sentence: each was tried and removed (see README "Actions, values, order").

**Robot profiles drive everything** (`robots/*.json`): limits, cruise and measured (`achieved`) speeds, `gait` thresholds, head angles, skills, transport. From one profile the bridge derives the JSON schema sent to Ollama (`plan.schema`), the system prompt (`prompt.system_prompt`), validation and compilation. Each skill in a profile becomes an action name.
- `microduck`: the real robot. `microduck-sim`: Pollen's simulator gait (backward too slow to walk, so the bridge turns around and walks forward). A simulated profile (`"simulated": true`) must never drive a real robot: `serve.py` recompiles with the robot's own profile for a real run.

**Bridge (`flymotion/`)**
- `plan.py`: validate/parse the plan, `compile_plan` turns steps into timed `robot.move` / skill / head commands, honouring `gait.min_speed`, `start_seconds`, `min_meters`, widening tight circles, enforcing per-step and per-plan limits, and returning warnings.
- `prompt.py`: system prompt built from the profile, and `say()`, the user-facing sentence in the request's language.
- `planner.py`: Ollama `/api/chat` client with the schema, thinking off, one correction round on invalid output, a short history. `OLLAMA_HOST` selects the server.
- `robot.py`: transports (`unix:`, `tcp:`, `print`, `sim`). `SimTransport` walks like the gait (nothing moves during start-up, sub-threshold axes count as `in_place`). `execute` re-sends `robot.move` at `rate_hz` for the robot's deadman and always ends with `robot.stop`, even on interruption.
- `serve.py`: JSON-lines protocol (documented in its docstring) for front ends; front ends may only pick profiles by name from `robots/`.

**App (`app/`, Electron, root `package.json`)**: `main.js` hosts the remote simulator page (`FLYMOTION_SIM_URL`) and the panel; `bridge.js` spawns `python -m flymotion serve` (`FLYMOTION_PYTHON`, `FLYMOTION_ROBOT`, `FLYMOTION_MODEL`); `sim/driver.js` is injected into the simulator page and becomes a Controller input source, playing the compiled commands in simulated seconds via the page's `window.rl` debug surface (written against a specific Space revision, noted at its top). The app does not plan or compile; it only displays and plays the bridge's output. User-facing app strings are in French.

**Generated / coupled files**
- `Modelfile` and `Modelfile.lite` are generated from `robots/microduck.json` + `flymotion/prompt.py`: edit those, then run `scripts/gen_modelfiles.py`. A test fails when they drift. The version string lives in `VARIANTS`/`VERSION` there and in `flymotion/__init__.py`, `package.json`, `planner.DEFAULT_MODEL`.
- Prompt examples must be valid plans that walk for real on every profile; dataset examples (`training/build_dataset.py`) must be valid plans and must not overlap `bench/cases.json`. Tests check both.
- The benchmark scores what the simulated robot ends up doing (pose, heading, head, skills, refusals, no stepping in place), not the exact JSON. Results go in `docs/bench/`, and the README tables quote them.

## Publishing

On `main`, a Modelfile change publishes to Ollama (`publish-ollama.yml`, secret `OLLAMA_KEY`); `publish-hf.yml` pushes the dataset and model repo (secret `HF_TOKEN`). Ollama model pages (`docs/ollama/*.md`) are pasted by hand on ollama.com, not published by CI.
