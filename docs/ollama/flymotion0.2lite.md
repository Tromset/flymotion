# FlyMotion 0.2 lite

The light FlyMotion, small enough to plan on a Raspberry Pi 5 next to the robot.

```sh
ollama pull Tromset/flymotion0.2lite
git clone https://github.com/Tromset/flymotion && cd flymotion
python3 -m flymotion --model Tromset/flymotion0.2lite "tourne à droite et regarde en haut"
```

| Base | Size | Runs on | Benchmark |
|---|---|---|---|
| Qwen3.5 2B (Apache 2.0) | 2.7 GB | Raspberry Pi 5, computers with 4 GB | 35/36 (36 French and English requests) |

- **Same plans as FlyMotion 0.2**: walk, turn, circle, look, skills, shapes, in French and English: only the actions, their values and their order.
- **Walks for real**: the bridge keeps every command above the robot's gait threshold, validates and clamps every plan, and always ends with a stop.
- **Faster**: about 1.6 times the speed of `Tromset/flymotion0.2`.

Use it through the FlyMotion bridge. Larger base: [`Tromset/flymotion0.2`](https://ollama.com/Tromset/flymotion0.2).

Needs Ollama 0.30 or later. Source, benchmark and robot bridge: https://github.com/Tromset/flymotion
