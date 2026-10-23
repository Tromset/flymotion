---
license: apache-2.0
language:
- fr
- en
base_model:
- Qwen/Qwen3.5-4B
- Qwen/Qwen3.5-2B
pipeline_tag: text-generation
library_name: ollama
tags:
- robotics
- motion-planning
- microduck
- ollama
- function-calling
datasets:
- Tromset/flymotion-commands
---

# FlyMotion 0.2

FlyMotion turns what you say into motion for a small robot such as [Microduck](https://github.com/pollen-robotics/microduck) (Pollen Robotics). "Avance d'un mètre, tourne à droite et frappe le ballon du pied gauche" becomes a plan that the FlyMotion bridge turns into velocity commands for the robot's walking policy. It runs locally with Ollama, on a laptop or a Raspberry Pi 5.

```sh
ollama pull Tromset/flymotion0.2        # Qwen3.5 4B, computers with 8 GB
ollama pull Tromset/flymotion0.2lite    # Qwen3.5 2B, Raspberry Pi 5
git clone https://github.com/Tromset/flymotion && cd flymotion
python3 -m flymotion "fais un carré de 50 cm puis salue"       # simulator
python3 -m flymotion --transport robot                          # on the robot
```

| Model | Base | Weights | Benchmark (36 requests) | Median planning time, 4-core CPU |
|---|---|---|---|---|
| `Tromset/flymotion0.2` | Qwen3.5 4B | 3.3 GB | 35/36 | 18.8 s |
| `Tromset/flymotion0.2lite` | Qwen3.5 2B | 2.7 GB | 35/36 | 11.7 s |

## What is in this repository

- `Modelfile`, `Modelfile.lite`: the two Ollama models (base, settings, system prompt).
- `system_prompt.txt`: the system prompt, built from the Microduck profile.
- `plan.schema.json`: the JSON schema of a plan, passed to Ollama's structured outputs so every reply parses.
- `robots/`: robot profiles (limits, cruise speeds, head angles, skills, transport).
- `bench/`: benchmark results, request by request.

Version 0.2, like 0.1, does not retrain the weights: the gains come from the plan format, the prompt and the bridge, measured by the benchmark. The fine-tuned GGUF weights will be published here; the training data is [`Tromset/flymotion-commands`](https://huggingface.co/datasets/Tromset/flymotion-commands).

## The plan format

The model reads three things in the request and writes nothing else: the actions, their values and their order, after one yes or no (`safe`: can the robot do it without hurting anyone).

```json
{"safe": true, "steps": [{"action": "turn", "direction": "right", "value": 90}, {"action": "look", "direction": "up"}], "repeat": 1}
```

Actions: `walk` (forward, backward, left, right; meters), `turn` (left, right; degrees), `circle` (left, right; radius), `look` (ahead, left, right, up, down and the diagonals), `wait` (seconds), `stop`, and each skill of the robot by its name (`kick_left`, `ground_pick`...); `repeat` for shapes. Values are always positive. With signed numbers instead (`"degrees": -90` for right), the 2B base confused left and right in a third of the requests: 22/32 on the first benchmark against 31/32 with words. In 0.2 the sentence for the user is written by the bridge, not the model, the reply is half as long, and the Modelfiles set `num_batch 32` so Ollama keeps the system prompt of this hybrid model cached between requests: the plan comes about twice as fast.

The model cannot send raw velocities any more. The bridge only sends commands the walking policy really walks: every speed is above the gait threshold of the robot profile, a start from standing includes the start-up time, a walk shorter than 5 cm is refused, and a direction the robot cannot walk fast enough (backward in Pollen's simulator) is walked forward after turning to face it. The benchmark's simulator counts the time a plan would make the robot shake on the spot, and fails it.

The bridge validates every plan, clamps it to the robot's limits (Microduck: 0.4 m/s forward, 0.3 m/s sideways, 1 rad/s turning, 3 m and 60 s per plan), re-sends `robot.move` at 10 Hz, and always ends with `robot.stop`. The language model never drives the motors: Pollen's walking policies do, at 50 Hz.

## Benchmark

36 French and English requests: distances, turns, sideways steps, head, the six Microduck skills, shapes, combinations, a stop, and four requests to refuse (jump over a table, kick the cat, fly, a question). Each plan is played in a kinematic simulator that walks like the robot's gait, and checked on the final position (10 cm), heading (10 degrees), head direction, skills, refusals, and that the robot really walks instead of stepping in place.

## Limits

- Plans are open loop: the robot does not see. Distances are as accurate as the calibration of your walking policy (`python3 -m flymotion calibrate vx`).
- The simulator is flat-ground kinematics with the gait's threshold and start-up; it checks the plan, not the balance.
- Independent project, not affiliated with Pollen Robotics or the Qwen team.

Code: MIT, https://github.com/Tromset/flymotion. Weights: Apache 2.0, from Qwen3.5 by the Qwen team at Alibaba.
