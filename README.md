# FlyMotion 0.1

FlyMotion turns what you say into motion for a small robot. "Avance d'un mètre, tourne à droite et frappe le ballon du pied gauche" becomes a plan, the plan becomes velocity commands, and [Microduck](https://github.com/pollen-robotics/microduck) walks. It runs locally with [Ollama](https://ollama.com), on a laptop or on a Raspberry Pi 5.

```sh
ollama pull Tromset/flymotion0.1lite
python3 -m flymotion "avance d'un mètre, tourne à droite puis frappe le ballon du pied gauche"
# FlyMotion: J'avance d'un mètre, je tourne à droite puis je frappe le ballon du pied gauche.
#   sim: x=+1.00 m, y=+0.00 m, heading=-90 degrees, skills: kick_left
```

FlyMotion is built like [FlyCoder](https://github.com/Tromset/flycoder): an open Qwen base, the right settings, a short dedicated system prompt, a benchmark that measures the result, and a CI that publishes everything.

## The two models

| Model | Base | Weights | Runs on | Benchmark |
|---|---|---|---|---|
| [`Tromset/flymotion0.1`](https://ollama.com/Tromset/flymotion0.1) | Qwen3.5 4B | 3.3 GB | any computer with 8 GB | BENCH_FULL |
| [`Tromset/flymotion0.1lite`](https://ollama.com/Tromset/flymotion0.1lite) | Qwen3.5 2B | 2.7 GB | Raspberry Pi 5, 4 GB computers | BENCH_LITE |

Benchmark: 32 French and English requests, each planned, played in the simulator and checked on what the robot would actually do (final position, heading, head, skills, refusals). Details below.

## How it works

FlyMotion has two layers, like the robot itself:

```
"tourne à droite et regarde en haut"
        |
        v
  FlyMotion model (Ollama)            plans in words, no numbers to get wrong:
        |                             {"steps": [{"action": "turn", "direction": "right", "degrees": 90},
        |                                        {"action": "head", "look": "up"}]}
        v
  FlyMotion bridge (python -m flymotion)
        |  validates the plan against the robot profile (robots/microduck.json)
        |  turns words into velocities and durations, clamps every limit
        |  re-sends robot.move at 10 Hz so the robot's deadman never fires, always ends with robot.stop
        v
  robotd on the robot (JSON-RPC, /run/robotd.sock)
        |
        v
  Pollen's walking policy (ONNX, 50 Hz)  keeps the robot balanced and tracks the velocity
```

The language model never touches the motors. It does not need to: Pollen already trains and publishes the walking, kicking and recovery policies (`pollen-robotics/microduck-policies` on Hugging Face), and FlyMotion drives them through the same commands as the gamepad.

Why plans are written in words: the first version had the model write signed numbers (`"degrees": -90` for right). Both models confused left and right in a third of the requests. With `"direction": "right"` and positive amounts, the bridge does the signs and the 2B model went from 22/32 to 31/32.

What the model can ask for:

| Step | Example | What the bridge does |
|---|---|---|
| `walk` | `{"action": "walk", "direction": "left", "meters": 0.3}` | forward, backward, left or right at cruise speed, for the time the distance needs |
| `turn` | `{"action": "turn", "direction": "right", "degrees": 90}` | turns on the spot (90 degrees when no angle is given) |
| `circle` | `{"action": "circle", "direction": "left", "meters": 0.5}` | one full circle of that radius |
| `head` | `{"action": "head", "look": "down-left"}` | head angles from the profile |
| `skill` | `{"action": "skill", "skill": "kick_left"}` | stops, then `robotctl robot do kick_left` |
| `wait`, `stop` | `{"action": "wait", "seconds": 2}` | stands still, or stops |
| `repeat` | `{"repeat": 4, "steps": [walk, turn]}` | a square, a triangle, "three times" |

Requests the robot cannot do or that could hurt someone ("saute par la fenêtre", "kick the cat") get an empty plan and an explanation.

## Run it on a Microduck

1. Install Ollama on your computer (or on a Raspberry Pi 5) and pull a model:
   ```sh
   ollama pull Tromset/flymotion0.1        # or flymotion0.1lite
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

Distances and angles are then right on your robot.

### Other robots

A robot is a JSON profile in `robots/`: its limits, cruise speeds, head angles, skills and transport. Copy `robots/microduck.json`, change it, and pass `--robot my_robot`. The bridge builds the system prompt from the profile, so the model only proposes skills the robot has.

## Benchmark

```sh
ollama create flymotion0.1lite -f Modelfile.lite
python3 bench/run.py --model flymotion0.1lite
```

BENCH_TABLE

Results in `docs/bench/`. Planning times are on a 4-core server without a GPU; on an Apple Silicon Mac they are several times shorter.

## Publishing

- **Ollama**: every change to `main` that touches a Modelfile or a page in `docs/ollama/` is published by [publish-ollama](.github/workflows/publish-ollama.yml). Secrets: `OLLAMA_KEY` (the `~/.ollama/id_ed25519` of a machine signed in to the Tromset account) and `OLLAMA_API_KEY` (https://ollama.com/settings/keys). By hand: `sh scripts/publish-ollama.sh Tromset`.
- **Hugging Face**: [publish-hf](.github/workflows/publish-hf.yml) publishes the dataset [`Tromset/flymotion-commands`](https://huggingface.co/datasets/Tromset/flymotion-commands) and the model repository [`Tromset/flymotion`](https://huggingface.co/Tromset/flymotion) (Modelfiles, system prompt, JSON schema, robot profiles, benchmark). Secret: `HF_TOKEN`, a write token from https://huggingface.co/settings/tokens. By hand: `HF_TOKEN=... python3 scripts/publish_hf.py`.

## Fine-tuning (path to 0.2)

0.1, like FlyCoder 0.3, does not retrain the weights: the gains come from the plan format, the prompt and the bridge. The dataset and the scripts for the next step are here:

```sh
python3 training/build_dataset.py                 # 3,000 French and English examples, none from the benchmark
pip install -r training/requirements.txt
python3 training/train.py --base Qwen/Qwen3.5-2B  # LoRA, a free Colab T4 is enough
sh training/export_gguf.sh training/out           # GGUF + Modelfile for Ollama
python3 scripts/publish_hf.py --what model --weights training/out/gguf
```

The goal of 0.2: the same score with a much shorter prompt, so a Raspberry Pi plans in a couple of seconds.

## Repository contents

- `flymotion/`: the bridge. `plan.py` (plan format, validation, compilation), `prompt.py` (system prompt), `planner.py` (Ollama client), `robot.py` (robotd transport, simulator, executor), `__main__.py` (command line).
- `robots/`: robot profiles. `Modelfile`, `Modelfile.lite`: generated by `scripts/gen_modelfiles.py`.
- `bench/`: benchmark. `tests/`: `python3 -m unittest discover -s tests`.
- `training/`: dataset, fine-tuning, GGUF export. `scripts/`: publishing. `docs/`: Ollama and Hugging Face pages, benchmark results.

## License

Code: MIT, see [license.md](license.md). The weights keep the license of their base: Apache 2.0 for Qwen3.5. Microduck and its policies belong to Pollen Robotics (Apache 2.0 code); FlyMotion is an independent project.
