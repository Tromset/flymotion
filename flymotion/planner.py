"""Asks a FlyMotion model served by Ollama for a plan. Standard library only, so it runs on the robot itself."""
import json
import os
import time
import urllib.error
import urllib.request

from .plan import PlanError, parse, schema
from .prompt import language, say, system_prompt

DEFAULT_MODEL = 'Tromset/flymotion0.2'
# The first request loads the model from disk: 2 min 13 s for the 2B on a 4-core VM, longer on a Raspberry Pi.
DEFAULT_TIMEOUT = 600


def ollama_url():
    host = os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')
    if '://' not in host:
        host = 'http://' + host
    return host.rstrip('/')


class Planner:
    """plan(request) -> (plan, info). `chat` can be replaced (tests, other servers): messages, schema -> text.
    The model writes the steps; the plan returned also carries "say", the sentence for the user, written by the bridge."""

    def __init__(self, profile, model=DEFAULT_MODEL, chat=None, retries=1, timeout=DEFAULT_TIMEOUT):
        self.profile, self.model, self.retries, self.timeout = profile, model, retries, timeout
        self.chat = chat or self._ollama
        self.history = []  # the last exchanges, so "do it again" or "the other way" make sense

    def _ollama(self, messages, format_schema):
        body = {'model': self.model, 'messages': messages, 'format': format_schema, 'stream': False,
                'think': False, 'keep_alive': '30m'}
        request = urllib.request.Request(ollama_url() + '/api/chat', data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)['message']['content']
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors='replace')
            if error.code == 404:
                raise PlanError(f'model {self.model} not found: run `ollama pull {self.model}` ({detail.strip()})') from None
            raise PlanError(f'Ollama returned HTTP {error.code}: {detail.strip()}') from None
        except urllib.error.URLError as error:
            raise PlanError(f'cannot reach Ollama at {ollama_url()} ({error.reason}); start it or set OLLAMA_HOST') from None
        except TimeoutError:
            raise PlanError(f'Ollama did not answer within {self.timeout:g} s: the first request loads {self.model} '
                            'from disk; try again, it then stays loaded for 30 minutes') from None

    def plan(self, request):
        messages = [{'role': 'system', 'content': system_prompt(self.profile)}, *self.history[-6:],
                    {'role': 'user', 'content': request}]
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            text = self.chat(messages, schema(self.profile))
            try:
                plan = parse(text, self.profile)
                break
            except PlanError as error:
                if attempt == self.retries:
                    raise
                # One correction round: small models usually fix a named mistake.
                messages += [{'role': 'assistant', 'content': text},
                             {'role': 'user', 'content': f'Invalid plan: {error}. Reply with the corrected JSON only.'}]
        self.history += [{'role': 'user', 'content': request}, {'role': 'assistant', 'content': json.dumps(plan, ensure_ascii=False)}]
        plan = {'say': say(plan, language(request)), **plan}
        return plan, {'seconds': round(time.monotonic() - started, 2), 'attempts': attempt + 1}
