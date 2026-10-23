# FlyMotion 0.2

Natural-language motion for small robots such as Microduck (Pollen Robotics): "avance d'un mètre, tourne à droite et frappe le ballon du pied gauche" becomes a motion plan the robot runs.

```sh
ollama pull Tromset/flymotion0.2
git clone https://github.com/Tromset/flymotion && cd flymotion
python3 -m flymotion "fais un carré de 50 cm puis salue"
```

| Base | Size | Runs on | Benchmark |
|---|---|---|---|
| Qwen3.5 4B (Apache 2.0) | 3.3 GB | any computer with 8 GB | 35/36 (36 French and English requests) |

- **Actions, values, order, nothing else**: walk forward, backward, left or right; turn; circle; look; the robot's skills (kicks, roll, pick up, sit, bow); shapes with repeat. A short prompt and a short reply make it fast; the bridge writes the sentence and turns the plan into velocities and durations, so the model never gets a sign wrong.
- **Walks for real**: every command is above the speed the robot's gait needs, so the robot never shakes on the spot to "cover" a distance. Every plan is validated, clamped to the robot's limits and ends with a stop. Requests that are impossible or could hurt someone are refused.
- **French and English**: the bridge answers in your language.

Use it through the FlyMotion bridge (it sends the JSON schema and turns thinking off). For a Raspberry Pi 5: [`Tromset/flymotion0.2lite`](https://ollama.com/Tromset/flymotion0.2lite).

Needs Ollama 0.30 or later. Source, benchmark and robot bridge: https://github.com/Tromset/flymotion
