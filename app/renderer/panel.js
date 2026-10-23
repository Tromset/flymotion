// The FlyMotion panel: sends requests to the bridge, shows its plans, plays them in the simulator or on the robot.
const api = window.flymotion;
const $ = (selector) => document.querySelector(selector);

const EXAMPLES = [
  "Avance d'un mètre puis tourne à droite",
  'Fais un carré de 50 cm',
  'Frappe le ballon du pied gauche',
  'Regarde en haut à gauche',
  'Fais une roulade',
  'Assieds-toi',
  'Walk backward 30 cm and turn around',
  'Saute par la fenêtre',
];

const settings = {
  read(key, fallback) {
    try {
      return localStorage.getItem(`flymotion.${key}`) ?? fallback;
    } catch {
      return fallback;
    }
  },
  write(key, value) {
    try {
      localStorage.setItem(`flymotion.${key}`, value);
    } catch {
      // settings are a convenience only
    }
  },
};

const state = { defaultModel: null, simReady: false, playId: 0, cards: new Map(), robotRun: null };

function pill(name, status, title) {
  const element = $(`#pill-${name}`);
  element.dataset.state = status;
  element.title = title || '';
}

function scrollDown() {
  const log = $('#log');
  log.scrollTop = log.scrollHeight;
}

const fixed = (value, digits = 2) => `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
const wrap = (degrees) => ((degrees + 540) % 360) - 180;
const poseText = (pose) => `x ${fixed(pose.x)} m · y ${fixed(pose.y)} m · cap ${fixed(wrap(pose.heading), 0)}°`;

function stepText(step) {
  switch (step.action) {
    case 'walk': return `marche ${step.direction} ${step.value} m`;
    case 'turn': return `tourne ${step.direction} ${step.value ?? 90}°`;
    case 'circle': return `cercle ${step.direction} r=${step.value} m`;
    case 'look': return `regarde ${step.direction}`;
    case 'wait': return `attend ${step.value} s`;
    default: return step.action; // stop, or a skill
  }
}

function commandText(command) {
  if (command.kind === 'move') return `move vx=${fixed(command.vx)} vy=${fixed(command.vy)} vyaw=${fixed(command.vyaw)} · ${command.seconds.toFixed(1)} s`;
  if (command.kind === 'head') return `head yaw=${fixed(command.yaw)} pitch=${fixed(command.pitch)} rad`;
  if (command.kind === 'skill') return `skill ${command.name} · ${command.seconds} s`;
  return 'stop';
}

function addBubble(text, json) {
  const bubble = document.createElement('div');
  bubble.className = json ? 'bubble json' : 'bubble';
  bubble.textContent = text;
  $('#log').append(bubble);
  scrollDown();
}

function addError(message) {
  const card = $('#card').content.firstElementChild.cloneNode(true);
  card.classList.add('error');
  card.querySelector('.who').textContent = 'Erreur';
  card.querySelector('.say').textContent = message;
  for (const selector of ['.steps', '.warnings', '.outcome', '.commands', '.actions']) card.querySelector(selector).remove();
  $('#log').append(card);
  scrollDown();
}

function addCard(result) {
  const card = $('#card').content.firstElementChild.cloneNode(true);
  const { plan, commands, warnings } = result;
  const meta = [`${result.duration.toFixed(1)} s de mouvement`];
  if (result.seconds !== undefined) meta.unshift(`planifié en ${result.seconds} s${result.attempts > 1 ? ` (${result.attempts} essais)` : ''}`);
  card.querySelector('.meta').textContent = meta.join(' · ');
  card.querySelector('.say').textContent = plan.say;

  const steps = card.querySelector('.steps');
  if ((plan.repeat ?? 1) > 1) {
    const repeat = document.createElement('li');
    repeat.className = 'repeat';
    repeat.textContent = `× ${plan.repeat}`;
    steps.append(repeat);
  }
  for (const step of plan.steps) {
    const item = document.createElement('li');
    item.textContent = stepText(step);
    steps.append(item);
  }
  if (!plan.steps.length) steps.remove();

  const list = card.querySelector('.warnings');
  for (const warning of warnings) {
    const item = document.createElement('li');
    item.textContent = `borné : ${warning}`;
    list.append(item);
  }

  const commandList = card.querySelector('.commands ol');
  for (const command of commands) {
    const item = document.createElement('li');
    item.textContent = commandText(command);
    commandList.append(item);
  }

  const entry = { card, result };
  if (plan.steps.length) {
    // The bridge's simulator walks like the robot's gait: time spent shaking on the spot is not a walk.
    card.querySelector('.ideal').textContent = poseText(result.sim) + (result.sim.in_place > 0.2 ? ` · piétine ${result.sim.in_place.toFixed(1)} s` : '');
    card.querySelector('.replay').addEventListener('click', () => playInSim(entry));
    card.querySelector('.robot').addEventListener('click', () => runOnRobot(entry));
  } else {
    card.querySelector('.actions').remove();
    card.querySelector('.commands').remove();
  }
  $('#log').append(card);
  scrollDown();
  return entry;
}

function highlight(entry, index) {
  const items = entry.card.querySelectorAll('.commands li');
  items.forEach((item, i) => {
    item.classList.toggle('current', i === index);
    item.classList.toggle('done', index !== null && i < index);
  });
}

// ── Simulator ────────────────────────────────────────────────────────────────

async function playInSim(entry) {
  const id = ++state.playId;
  state.cards.set(id, entry);
  const outcome = entry.card.querySelector('.outcome');
  outcome.hidden = false;
  entry.card.querySelector('.real').textContent = 'en cours…';
  entry.card.querySelector('.gap').textContent = '';
  try {
    await api.sim('play', entry.result.commands, { id });
  } catch (error) {
    entry.card.querySelector('.real').textContent = error.message;
  }
}

function compare(entry, event) {
  const ideal = entry.result.sim;
  const real = event.pose;
  entry.card.querySelector('.real').textContent = event.stopped ? `${poseText(real)} (arrêté)` : poseText(real);
  const distance = Math.hypot(real.x - ideal.x, real.y - ideal.y);
  const turn = Math.abs(wrap(real.heading - ideal.heading));
  const notes = [`écart ${(distance * 100).toFixed(0)} cm, ${turn.toFixed(0)}°`];
  if (event.limited?.length) notes.push('vitesse bornée au walker du simulateur');
  if (event.mode && event.mode !== 'walk') notes.push(`mode ${event.mode}`);
  const gap = entry.card.querySelector('.gap');
  gap.textContent = notes.join(' · ');
  gap.classList.toggle('off', distance > 0.15 || turn > 15);
}

api.onSimEvent((event) => {
  const entry = state.cards.get(event.id);
  switch (event.type) {
    case 'loading':
      state.simReady = false;
      pill('sim', 'busy', event.url);
      $('#stage-message').textContent = 'Chargement de la physique MuJoCo et des politiques de Pollen…';
      break;
    case 'ready':
      state.simReady = true;
      pill('sim', 'ok', 'MuJoCo + politiques ONNX prêtes');
      break;
    case 'error':
      pill('sim', 'bad', event.error);
      $('#stage-message').textContent = event.error;
      addError(`Simulateur : ${event.error}`);
      break;
    case 'command':
      if (entry) highlight(entry, event.index);
      break;
    case 'skipped':
      if (entry) {
        const item = document.createElement('li');
        item.textContent = `${event.skill} : ${event.reason}`;
        entry.card.querySelector('.warnings').append(item);
      }
      break;
    case 'done':
      if (entry) {
        highlight(entry, null);
        compare(entry, event);
      }
      break;
    default:
      break;
  }
});

// ── Robot ────────────────────────────────────────────────────────────────────

async function runOnRobot(entry) {
  const target = $('#target').value.trim() || 'robot';
  const skillCommand = $('#skill-command').value.trim() || undefined;
  const lines = entry.result.commands.map(commandText).join('\n');
  const note = 'Le bridge recompile ce plan avec le profil du robot réel ; les durées peuvent changer.';
  if (!confirm(`Exécuter sur le robot (${target}, profil ${$('#robot-profile').value}) ?\n${note}\n\nAperçu (profil de planification) :\n${lines}`)) return;
  settings.write('target', target);
  settings.write('skillCommand', skillCommand || '');
  const button = entry.card.querySelector('.robot');
  button.disabled = true;
  state.robotRun = entry;
  try {
    const robot = $('#robot-profile').value;
    settings.write('robotProfile', robot);
    const reply = await api.request({ type: 'run', plan: entry.result.plan, robot, target, skill_command: skillCommand });
    button.textContent = reply.stopped ? 'Arrêté sur le robot' : 'Joué sur le robot';
  } catch (error) {
    addError(`Robot : ${error.message}`);
  } finally {
    button.disabled = false;
    state.robotRun = null;
    highlight(entry, null);
  }
}

api.onBridgeEvent((event) => {
  if (event.event === 'command' && state.robotRun) highlight(state.robotRun, event.index);
});

// ── Requests ─────────────────────────────────────────────────────────────────

async function submit() {
  const text = $('#request').value.trim();
  if (!text) return;
  const json = $('#json-mode').checked;
  let message;
  if (json) {
    try {
      message = { type: 'compile', plan: JSON.parse(text) };
    } catch (error) {
      addError(`JSON invalide : ${error.message}`);
      return;
    }
  } else {
    message = { type: 'plan', request: text, model: $('#model').value || undefined };
  }
  addBubble(text, json);
  $('#request').value = '';
  $('#send').disabled = true;
  pill('ollama', json ? $('#pill-ollama').dataset.state : 'busy', 'planification…');
  try {
    const result = await api.request(message);
    if (!json) pill('ollama', 'ok', message.model);
    const entry = addCard(result);
    if (result.plan.steps.length && $('#autoplay').checked && state.simReady) playInSim(entry);
  } catch (error) {
    if (!json) pill('ollama', /Ollama|model/.test(error.message) ? 'bad' : 'ok', error.message);
    addError(error.message);
  } finally {
    $('#send').disabled = false;
    $('#request').focus();
  }
}

function stopAll() {
  return api.stopAll();
}

// Ollama lists "name:tag"; "Tromset/flymotion0.2" is "Tromset/flymotion0.2:latest".
const sameModel = (a, b) => a === b || `${a}:latest` === b || a === `${b}:latest`;

async function loadModels() {
  const select = $('#model');
  const saved = settings.read('model', null);
  let models = [];
  try {
    models = (await api.request({ type: 'models' })).models;
    pill('ollama', models.length ? 'ok' : 'bad', models.length ? `${models.length} modèles` : 'aucun modèle : ollama pull Tromset/flymotion0.2lite');
  } catch (error) {
    pill('ollama', 'bad', error.message);
  }
  const installed = (name) => models.some((model) => sameModel(model, name));
  const names = [...models];
  for (const name of [state.defaultModel, saved]) if (name && !installed(name)) names.push(name);
  select.replaceChildren(...names.map((name) => new Option(installed(name) ? name : `${name} (pas installé)`, name)));
  const choice = [saved, ...models.filter((name) => name.includes('flymotion')), state.defaultModel].find((name) => name && installed(name));
  select.value = choice || select.value || state.defaultModel;
}

async function switchProfile(name) {
  try {
    const { profile } = await api.request({ type: 'profile', robot: name });
    settings.write('profile', name);
    addBubble(`Profil ${name} : ${profile.title}`, true);
  } catch (error) {
    addError(error.message);
  }
}

async function connect() {
  pill('bridge', 'busy', 'démarrage…');
  try {
    const hello = await api.request({ type: 'hello' });
    state.defaultModel = hello.model;
    pill('bridge', 'ok', `FlyMotion ${hello.version} · Ollama ${hello.ollama}`);
    const option = (robot) => new Option(robot.simulated ? `${robot.name} (simulateur)` : robot.name, robot.name);
    $('#profile').replaceChildren(...hello.robots.map(option));
    $('#profile').value = hello.profile.name;
    const real = hello.robots.filter((robot) => !robot.simulated);
    $('#robot-profile').replaceChildren(...real.map(option));
    $('#robot-profile').value = settings.read('robotProfile', real[0]?.name);
    const saved = settings.read('profile', null);
    if (saved && saved !== hello.profile.name && hello.robots.some((robot) => robot.name === saved)) {
      $('#profile').value = saved;
      await switchProfile(saved);
    }
    await loadModels();
  } catch (error) {
    pill('bridge', 'bad', error.message);
    addError(`Bridge Python : ${error.message}`);
  }
}

api.onBridgeExit((reason) => {
  pill('bridge', 'bad', reason);
  addError(`Bridge Python : ${reason}`);
});

// ── Wiring ───────────────────────────────────────────────────────────────────

for (const example of EXAMPLES) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = example;
  button.addEventListener('click', () => {
    $('#json-mode').checked = false;
    $('#request').value = example;
    submit();
  });
  $('#examples').append(button);
}

$('#form').addEventListener('submit', (event) => {
  event.preventDefault();
  submit();
});
$('#request').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});
$('#json-mode').addEventListener('change', () => {
  $('#request').placeholder = $('#json-mode').checked
    ? '{"repeat": 4, "steps": [{"action": "walk", "direction": "forward", "value": 0.5}, {"action": "turn", "direction": "left", "value": 90}]}'
    : "Avance d'un mètre, tourne à droite et frappe le ballon du pied gauche";
});
$('#model').addEventListener('change', () => settings.write('model', $('#model').value));
$('#profile').addEventListener('change', () => switchProfile($('#profile').value));
$('#refresh-models').addEventListener('click', loadModels);
$('#stop').addEventListener('click', stopAll);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') stopAll();
});
$('#reset-duck').addEventListener('click', () => api.sim('reset').catch((error) => addError(error.message)));
$('#new-chat').addEventListener('click', async () => {
  await api.request({ type: 'reset' }).catch(() => {});
  $('#log').replaceChildren();
  addBubble('Nouvelle conversation : le modèle a oublié les demandes précédentes.', true);
});
$('#stage-reload').addEventListener('click', () => api.reloadSim());
$('#target').value = settings.read('target', 'robot');
$('#skill-command').value = settings.read('skillCommand', '');
$('#autoplay').checked = settings.read('autoplay', 'true') === 'true';
$('#autoplay').addEventListener('change', () => settings.write('autoplay', String($('#autoplay').checked)));

connect();
