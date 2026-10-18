# FlyMotion 0.1 lite

The light FlyMotion, small enough to plan on a Raspberry Pi 5 next to the robot.

```sh
ollama pull Tromset/flymotion0.1lite
git clone https://github.com/Tromset/flymotion && cd flymotion
python3 -m flymotion --model Tromset/flymotion0.1lite "tourne à droite et regarde en haut"
```

| Base | Size | Runs on | Benchmark |
|---|---|---|---|
| Qwen3.5 2B (Apache 2.0) | 2.7 GB | Raspberry Pi 5, computers with 4 GB | 35/36 (36 French and English requests) |

- **Same plans as FlyMotion 0.1**: walk, turn, circle, look, skills, shapes, in French and English.
- **Safe by construction**: the bridge validates and clamps every plan, and always ends with a stop.
- **Faster**: about 1.6 times the speed of `Tromset/flymotion0.1`.

Use it through the FlyMotion bridge. Larger base: [`Tromset/flymotion0.1`](https://ollama.com/Tromset/flymotion0.1).

Needs Ollama 0.30 or later. Source, benchmark and robot bridge: https://github.com/Tromset/flymotion
