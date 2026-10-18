# FlyMotion 0.1

Natural-language motion for small robots such as Microduck (Pollen Robotics): "avance d'un mètre, tourne à droite et frappe le ballon du pied gauche" becomes a motion plan the robot runs.

```sh
ollama pull Tromset/flymotion0.1
git clone https://github.com/Tromset/flymotion && cd flymotion
python3 -m flymotion "fais un carré de 50 cm puis salue"
```

| Base | Size | Runs on | Benchmark |
|---|---|---|---|
| Qwen3.5 4B (Apache 2.0) | 3.3 GB | any computer with 8 GB | BENCH_FULL |

- **Plans in words**: walk forward, backward, left or right; turn; circle; look; the robot's skills (kicks, roll, pick up, sit, bow); shapes with repeat. The bridge turns them into velocities and durations, so the model never gets a sign wrong.
- **Safe by construction**: every plan is validated and clamped to the robot's limits, and always ends with a stop. Requests that are impossible or could hurt someone are refused.
- **French and English**, answers in your language.

Use it through the FlyMotion bridge (it sends the JSON schema and turns thinking off). For a Raspberry Pi 5: [`Tromset/flymotion0.1lite`](https://ollama.com/Tromset/flymotion0.1lite).

Needs Ollama 0.30 or later. Source, benchmark and robot bridge: https://github.com/Tromset/flymotion
