# FlyMotion 0.2

FlyMotion turns what you say into motion for a small robot. "Move 3 feet ahead and shoot the ball with your right foot." becomes a plan, the plan becomes velocity commands, and [Microduck](https://github.com/pollen-robotics/microduck) walks. It runs locally with [Ollama](https://ollama.com), on a laptop or on a Raspberry Pi 5.

```sh
ollama pull Tromset/flymotion0.2lite
python3 -m flymotion "avance d'un mètre, tourne à droite puis frappe le ballon du pied gauche"
# FlyMotion: J'avance de 1 m, puis je tourne à droite, puis je lance kick_left.
#   (3 steps, 11.0 s of motion, planned in 8.58 s, the first request after loading)
#   sim: x=+1.00 m, y=+0.00 m, heading=-90 degrees, skills: kick_left
```

## To launch the app :

````sh
cd ~
````

````sh
git clone https://github.com/Tromset/flymotion
````

````sh
cd flymotion
````

````sh
npm install
````

````sh
npm start
````

````sh
npm run shortcut
````

````sh
# From any directory
flymotion
````

FlyMotion is built like [FlyCoder](https://github.com/Tromset/flycoder): an open Qwen base, the right settings, a short dedicated system prompt, a benchmark that measures the result, and a CI that publishes everything.

## The two models

| Model | Base | Weights | Runs on | Benchmark |
|---|---|---|---|---|
| [`Tromset/flymotion0.2`](https://ollama.com/Tromset/flymotion0.2) | Qwen3.5 4B | 3.3 GB | any computer with 8 GB | 36/36 |
| [`Tromset/flymotion0.2lite`](https://ollama.com/Tromset/flymotion0.2lite) | Qwen3.5 2B | 2.7 GB | Raspberry Pi 5, 4 GB computers | 35/36 |

Benchmark: 36 French and English requests, each planned, played in the simulator and checked on what the robot would actually do (final position, heading, head, skills, refusals, and that it really walks). Details below.

## How it works

FlyMotion has two layers, like the robot itself:

```
"tourne à droite et regarde en haut"
        |
        v
  FlyMotion model (Ollama)            the actions, their values and their order, nothing else:
        |                             {"safe": true, "steps": [{"action": "turn", "direction": "right", "value": 90},
        |                                                      {"action": "look", "direction": "up"}], "repeat": 1}
        v
  FlyMotion bridge (python -m flymotion)
        |  validates the plan against the robot profile (robots/microduck.json)
        |  turns words into velocities and durations, clamps every limit, keeps every speed above the gait threshold
        |  writes the sentence for the user ("Je tourne à droite, puis je regarde en haut.")
        |  re-sends robot.move at 10 Hz so the robot's deadman never fires, always ends with robot.stop
        v
  robotd on the robot (JSON-RPC, /run/robotd.sock)
        |
        v
  Pollen's walking policy (ONNX, 50 Hz)  keeps the robot balanced and tracks the velocity
```

The language model never touches the motors. It does not need to: Pollen already trains and publishes the walking, kicking and recovery policies (`pollen-robotics/microduck-policies` on Hugging Face), and FlyMotion drives them through the same commands as the gamepad.

Why plans are written in words: the first version had the model write signed numbers (`"degrees": -90` for right). Both models confused left and right in a third of the requests. With `"direction": "right"` and positive amounts, the bridge does the signs and the 2B model went from 22/32 to 31/32.

### Actions, values, order: the model does nothing more (0.2)

A request holds three things: the actions, their values (how far, how much, how long) and their order. The model writes exactly that, after one yes or no (`"safe"`: can the robot do it without hurting anyone), and nothing else. Everything else is the bridge's job: the sentence shown to the user, the signs, the speeds, the durations, the limits, the way each action is walked.

```json
{"safe": true, "steps": [{"action": "walk", "direction": "forward", "value": 1}, {"action": "turn", "direction": "right"}, {"action": "kick_left"}], "repeat": 1}
```

In 0.1 the model also wrote a sentence (`"say"`) and could send raw velocities. In 0.2:

- The reply is shorter (about 34 tokens instead of 43 for "avance de 50 cm", less again for skills and refusals): no sentence, one `value` for meters, degrees and seconds, and a skill is its name alone (`{"action": "kick_left"}`), which the JSON schema enforces. The prompt is shorter too (3,200 characters instead of 4,100).
- The steps come before `repeat`: a 2B model that decides `repeat` first tends to also write the repeated steps out ("three times" played nine times).
- `safe` comes first: without a sentence to write, the 2B model turned "kick the cat" into `kick_left`; with this one decision it refuses.
- The Modelfiles set `num_batch 32`. Qwen3.5 is a hybrid model, and Ollama can only reuse its cached system prompt up to one batch before the end of the previous prompt: with the default batch of 512 it read 500 tokens of prompt again on every request (11 s on a 4-core CPU), with 32 it reads about 30 (1 s).

| Action | Example | What the bridge does |
|---|---|---|
| `walk` | `{"action": "walk", "direction": "left", "value": 0.3}` | forward, backward, left or right (meters) at cruise speed, for the time the distance needs |
| `turn` | `{"action": "turn", "direction": "right", "value": 90}` | turns on the spot (degrees; 90 when no value is given) |
| `circle` | `{"action": "circle", "direction": "left", "value": 0.5}` | one full circle of that radius (meters) |
| `look` | `{"action": "look", "direction": "down-left"}` | head angles from the profile |
| a skill | `{"action": "kick_left"}` | stops, then `robotctl robot do kick_left` |
| `wait`, `stop` | `{"action": "wait", "value": 2}` | stands still (seconds), or stops |
| `repeat` | `{"repeat": 4, "steps": [walk, turn]}` | a square, a triangle, "three times" |

Requests the robot cannot do or that could hurt someone ("saute par la fenêtre", "kick the cat") get an empty plan, and the bridge says it will not do it. Microduck has no jumping policy, so "saute" is refused; a robot whose profile lists a `jump` skill gets it as one more action.

### Walking for real

A walking policy has a threshold: commanded below it, the robot does not walk, it shakes on the spot. Pollen's simulator caps backward walking at 0.2 m/s while its walker needs about 0.22 m/s to start, so in 0.1 "recule de 30 cm" made the duck step in place, and the bridge's own simulator still counted the 30 cm as walked: the plan passed the benchmark without the robot ever taking a step.

In 0.2 the bridge only sends what the gait really walks (`gait` in the robot profile):

- The model cannot write velocities any more (the `move` step is gone); every speed comes from the profile and stays above `gait.min_speed`.
- A direction the robot cannot walk fast enough is walked the right way: the robot turns to face it, walks forward, and turns back (backward walking in the simulator).
- Every start from standing includes the start-up time (`gait.start_seconds`, 1.5 s in the simulator), so short walks are not cut before the first real step. A walk under 5 cm (`gait.min_meters`) is refused: it would only be a shuffle.
- A circle too tight to be walked above the threshold is widened, with a warning.
- The bridge's simulator walks like the gait: nothing moves during the start-up, an axis under the threshold steps in place, and that time is counted (`in_place`). The benchmark fails any plan that steps in place, and the app shows it ("piétine").

## Run it on a Microduck

1. Install Ollama on your computer (or on a Raspberry Pi 5) and pull a model:
   ```sh
   ollama pull Tromset/flymotion0.2        # or flymotion0.2lite
   ```
2. Copy this repository to the robot (Python 3.9 or later, standard library only), start the robot with the gamepad (Start), and point the bridge at your Ollama:
   ```sh
   OLLAMA_HOST=192.168.1.20:11434 python3 -m flymotion --transport robot
   ```
   (on the computer, start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve` so the robot can reach it). Type a request per line. The bridge shows the commands and asks before moving; `--yes` skips the question.

Without installing anything on the robot, from the computer:

```sh
python3 -m flymotion --transport print --yes "fais un carré de 50 cm" \
  | ssh microduck@microduck.local socat - UNIX-CONNECT:/run/robotd.sock
```

Other options: `--transport tcp:host:port` (behind `socat TCP-LISTEN:7000,fork UNIX-CONNECT:/run/robotd.sock`), `--skill-command "ssh microduck@microduck.local robotctl robot do {skill}"`, `--plan-only` to print the JSON, `-v` to see every command.

Ctrl-C at any time sends `robot.stop`. The limits (0.4 m/s forward, 0.3 m/s sideways, 1 rad/s turning, the trained ranges of the Microduck walking policies; 3 m and 60 s per plan) are enforced by the bridge, whatever the model writes.

### Calibrate your robot

A walking policy rarely tracks the command exactly (one published Microduck policy turns at 0.29 rad/s when asked for 1). Measure each axis once:

```sh
python3 -m flymotion calibrate vx      # walks 5 s, asks how far it went, prints the value for robots/microduck.json
python3 -m flymotion calibrate vyaw
```

Distances and angles are then right on your robot. If an axis only shakes on the spot below some speed, or takes time to start walking, put it in `gait` (`min_speed`, `start_seconds`): the bridge then never commands it below that speed.

### Other robots

A robot is a JSON profile in `robots/`: its limits, cruise speeds, head angles, skills and transport. Copy `robots/microduck.json`, change it, and pass `--robot my_robot`. The bridge builds the system prompt from the profile, so the model only proposes skills the robot has.

## Test app: FlyMotion in Pollen's simulator

`app/` is an Electron app that puts the model in a simulated Microduck. On the left, Pollen's [Microduck simulator](https://huggingface.co/spaces/pollen-robotics/microduck-simulator): MuJoCo physics and the real ONNX walking, kicking, sitting and rolling policies, running in the page. On the right, the FlyMotion panel: type a request, the model plans, the bridge validates, clamps and compiles it, and the duck plays the commands in real time.

```sh
ollama pull Tromset/flymotion0.2lite
npm install
npm start               # from the repository root
npm run shortcut        # once: a FlyMotion icon on the desktop, and the `flymotion` command
flymotion               # then from any folder, or double-click the icon
```

The icon is `FlyMotion.app` on macOS, `flymotion.desktop` on Linux (also in the applications menu) and `FlyMotion.lnk` on Windows. It remembers where the repository, Electron and Python are: run `npm run shortcut` again after moving the folder. `npm run unshortcut` removes the icon and the command. npm only runs its own commands (`start`, `test`...) without `run`, so the shortcut is `npm run shortcut`. If `npm link` is refused, your global npm folder needs administrator rights: use `sudo npm run shortcut`, or a Node installed with nvm.

It needs Python 3.9 or later (the bridge runs as `python3 -m flymotion serve`; set `FLYMOTION_PYTHON` to use another interpreter), Ollama (`OLLAMA_HOST` is passed through), and a network connection: the simulator loads from its Hugging Face Space, and MuJoCo and onnxruntime from their CDN. `FLYMOTION_SIM_URL` points the app at another build, for example `http://localhost:5173/?boot=1` from the Space's `npm run dev`.

- FlyMotion is one more input source of the simulator, like its keyboard or gamepad: velocities go to the walking policy, head angles to its head command, `kick_left`, `kick_right`, `roulade`, `ground_pick` and `sit_toggle` to the matching policies (a kick gets a ball first). `polite-bow` has no policy in the simulator and is reported as skipped.
- Each plan shows where the bridge expects the duck to end (its kinematic simulator) and where the physics put it, with the gap.
- "Plan JSON" compiles a plan you write, without the model. Escape, or Stop, stops the duck and any run on the robot.
- The arrows, WASD and the mouse still drive the simulator; they take over from FlyMotion while held.

The app plans with the `microduck-sim` profile, calibrated in the simulator: its walker does not start below about 0.22 m/s and takes about 1.5 s to start, reaches 0.11 m/s when asked for 0.25, turns on the spot at about 0.26 rad/s to the left, and cannot walk backward (capped at 0.2 m/s): with this profile the bridge turns around and walks forward instead. In-place right turns barely respond; that gap shows in the app, it is the simulator's, the keyboard gets the same.

"Robot réel" sends a plan to a Microduck (`robot` on the robot itself, or `tcp:host:port` behind socat). The bridge compiles it again with the robot's own profile, `microduck`, and refuses simulator profiles, so the simulator's speeds never reach the motors.

## Benchmark

```sh
ollama create flymotion0.2lite -f Modelfile.lite
python3 bench/run.py --model flymotion0.2lite                       # or --robot microduck-sim, the app's gait
```

| Model | Passed | Median planning time | Missed |
|---|---|---|---|
| `flymotion0.2` (Qwen3.5 4B) | 36/36 | 8.3 s | |
| `flymotion0.2lite` (Qwen3.5 2B) | 35/36 | 5.3 s | "Quelle heure est-il ?": wrote a wait of 0 s instead of refusing, twice |
| `flymotion0.2lite`, `--robot microduck-sim` (the simulator's gait) | 35/36 | 5.0 s | "Turn 45 degrees to the right, three times": three quarter turns written out |
| `flymotion0.1lite`, same machine | 33/36 | 16.7 s | the 50 cm square (alternated left and right turns), "turn left again" and "three times" (steps written out and repeated) |
| `flymotion0.1lite` with `num_batch 32` | 33/36 | 6.7 s | the same |
| First version, signed numbers (2B, 32 requests) | 22/32 | 10.4 s | left and right swapped in 7 requests |

Most of the speed comes from `num_batch 32` (16.7 s to 6.7 s for 0.1lite), the rest from the shorter reply (6.7 s to 5.3 s); the plan format gives the accuracy back (33 to 35). The first request after loading the model reads the whole prompt: about 25 s for the 2B on this CPU, then 5 s. No plan of 0.2 steps in place, on either profile. 0.1 recorded 35/36 for both models on an earlier run (`docs/bench/flymotion0.1*.json`); at temperature 0.2 a run varies by a case or two.

Results in `docs/bench/`. Planning times are on a 4-core server without a GPU, all on the same machine; on an Apple Silicon Mac they are several times shorter.

## Publishing

- **Ollama**: every change to `main` that touches a Modelfile is published by [publish-ollama](.github/workflows/publish-ollama.yml). Secret: `OLLAMA_KEY` (the `~/.ollama/id_ed25519` of a machine signed in to the Tromset account). By hand: `sh scripts/publish-ollama.sh Tromset`. The model pages are not published by the workflow: paste `docs/ollama/<model>.md` with the Edit button of each page on ollama.com.
- **Hugging Face**: [publish-hf](.github/workflows/publish-hf.yml) publishes the dataset [`Tromset/flymotion-commands`](https://huggingface.co/datasets/Tromset/flymotion-commands) and the model repository [`Tromset/flymotion`](https://huggingface.co/Tromset/flymotion) (Modelfiles, system prompt, JSON schema, robot profiles, benchmark). Secret: `HF_TOKEN`, a write token from https://huggingface.co/settings/tokens. By hand: `HF_TOKEN=... python3 scripts/publish_hf.py`.

## Fine-tuning (path to 0.3)

0.1 and 0.2, like FlyCoder 0.3, do not retrain the weights: the gains come from the plan format, the prompt and the bridge. The dataset and the scripts for the next step are here:

```sh
python3 training/build_dataset.py                 # 3,000 French and English examples, none from the benchmark
pip install -r training/requirements.txt
python3 training/train.py --base Qwen/Qwen3.5-2B  # LoRA, a free Colab T4 is enough
sh training/export_gguf.sh training/out           # GGUF + Modelfile for Ollama
python3 scripts/publish_hf.py --what model --weights training/out/gguf
```

The dataset already uses the 0.2 format (actions, values, order). The goal of 0.3: the same score without the prompt, so a Raspberry Pi plans in a couple of seconds.

## Repository contents

- `flymotion/`: the bridge. `plan.py` (plan format, validation, gait, compilation), `prompt.py` (system prompt, the sentence for the user), `planner.py` (Ollama client), `robot.py` (robotd transport, simulator, executor), `serve.py` (JSON lines protocol for the app), `__main__.py` (command line).
- `app/`: the Electron test app (`package.json` at the root). `main.js` (windows), `cli.js` (the `flymotion` command), `shortcut.js` (desktop icon), `assets/` (icon, drawn by `scripts/gen_icon.py`), `bridge.js` (runs `flymotion serve`), `sim/driver.js` (FlyMotion input source for Pollen's simulator), `renderer/` (panel). Tests: `npm test`.
- `robots/`: robot profiles (`microduck`, and `microduck-sim` for the app's simulator). `Modelfile`, `Modelfile.lite`: generated by `scripts/gen_modelfiles.py`.
- `bench/`: benchmark. `tests/`: `python3 -m unittest discover -s tests`.
- `training/`: dataset, fine-tuning, GGUF export. `scripts/`: publishing. `docs/`: Ollama and Hugging Face pages, benchmark results.

## License

Code: MIT, see [license.md](license.md). The weights keep the license of their base: Apache 2.0 for Qwen3.5. Microduck and its policies belong to Pollen Robotics (Apache 2.0 code); FlyMotion is an independent project.
