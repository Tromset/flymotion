// `flymotion update`, with git, npm and Ollama replaced by a recorder.
const test = require('node:test');
const assert = require('node:assert/strict');
const { update, publishedModels } = require('../update.js');

const LIST = [
  'NAME                               ID              SIZE      MODIFIED',
  'Tromset/flymotion0.2lite:latest    aaaaaaaaaaaa    1.9 GB    2 days ago',
  'flymotion0.2:latest                bbbbbbbbbbbb    3.4 GB    3 days ago',
  'qwen3.5:4b                         cccccccccccc    3.4 GB    3 days ago',
].join('\n');

// A fake machine: `answers` maps the start of a command line to { status, output }.
function machine(answers = {}, { git = true, modules = true } = {}) {
  const calls = [];
  const heads = ['1111111aaaa', answers.newHead || '1111111aaaa'];
  const run = (command, args) => {
    const line = [command, ...args].join(' ');
    calls.push(line);
    if (line === 'git rev-parse HEAD') return { status: 0, output: heads.shift() };
    for (const [start, answer] of Object.entries(answers)) if (line.startsWith(start)) return { output: '', ...answer };
    if (line === 'ollama list') return { status: 0, output: LIST };
    return { status: 0, output: '' };
  };
  const lines = [];
  const exists = (file) => (file.endsWith('.git') ? git : modules);
  return { calls, lines, options: { root: '/clone', run, log: (line) => lines.push(line), exists } };
}

test('only published FlyMotion models are pulled again', () => {
  assert.deepEqual(publishedModels(LIST), ['Tromset/flymotion0.2lite:latest']);
  assert.deepEqual(publishedModels(''), []);
});

test('an up-to-date clone pulls the code and the model, and leaves the dependencies alone', () => {
  const { calls, lines, options } = machine();
  assert.equal(update(options), 0);
  assert.deepEqual(calls, [
    'git status --porcelain -- package-lock.json',
    'git rev-parse HEAD',
    'git pull --ff-only',
    'git rev-parse HEAD',
    'ollama list',
    'ollama pull Tromset/flymotion0.2lite:latest',
  ]);
  assert.match(lines.at(-1), /already up to date/);
});

test('new code with a new lockfile reinstalls the dependencies', () => {
  const { calls, lines, options } = machine({ newHead: '2222222bbbb', 'git diff --quiet': { status: 1 } });
  assert.equal(update(options), 0);
  assert.ok(calls.includes('npm ci --no-audit --no-fund'));
  assert.match(lines.at(-1), /1111111 -> 2222222/);
});

test('new code with the same lockfile does not reinstall', () => {
  const { calls, options } = machine({ newHead: '2222222bbbb' });
  assert.equal(update(options), 0);
  assert.ok(!calls.some((line) => line.startsWith('npm')));
});

test('missing node_modules are installed', () => {
  const { calls, options } = machine({}, { modules: false });
  assert.equal(update(options), 0);
  assert.ok(calls.includes('npm ci --no-audit --no-fund'));
});

test('a lockfile npm rewrote is put back before the pull', () => {
  const { calls, options } = machine({ 'git status --porcelain': { status: 0, output: ' M package-lock.json' } });
  assert.equal(update(options), 0);
  assert.ok(calls.indexOf('git checkout -- package-lock.json') < calls.indexOf('git pull --ff-only'));
  assert.ok(calls.includes('git checkout -- package-lock.json'));
});

test('a refused pull stops everything', () => {
  const { calls, options } = machine({ 'git pull': { status: 1 } });
  assert.equal(update(options), 1);
  assert.ok(!calls.some((line) => line.startsWith('ollama') || line.startsWith('npm')));
});

test('without Ollama the code is still updated', () => {
  const { calls, lines, options } = machine({ 'ollama list': { status: null } });
  assert.equal(update(options), 0);
  assert.ok(!calls.some((line) => line.startsWith('ollama pull')));
  assert.ok(lines.some((line) => /models were not updated/.test(line)));
});

test('a copy that is not a git clone says how to install one', () => {
  const { calls, lines, options } = machine({}, { git: false });
  assert.equal(update(options), 1);
  assert.deepEqual(calls, []);
  assert.match(lines[0], /git clone/);
});
