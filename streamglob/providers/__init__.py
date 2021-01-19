import logging
logger = logging.getLogger(__name__)

import abc
import re
from functools import wraps
import traceback

from stevedore import extension
from orderedattrdict import AttrDict

# from .. import session
from .. import config
from ..exceptions import *

PROVIDERS = AttrDict()
DEFAULT_PROVIDER=None

# FOO=1
def get(provider, *args, **kwargs):
    # global FOO
    # FOO+=1
    # if FOO > 5:
    #     raise Exception("get")
    try:
        return PROVIDERS.get(provider)
    except TypeError:
        raise Exception(provider, PROVIDERS)

URI_SPEC_RE=re.compile(r"(?:(\w+)://)?([^:/]*)(.*)")

def parse_uri(uri):

    options = AttrDict()

    if not uri:
        uri = DEFAULT_PROVIDER

    (action, provider, spec) = URI_SPEC_RE.search(uri).groups()
    if not action:
        action = "play"

    if not provider:
        provider = DEFAULT_PROVIDER

    p = get(provider)

    if not p:
        raise Exception(f"provider {provider} not found")

    selection, options = p.parse_spec(spec)
    return (action, p, selection, options)


def load_config():

    global DEFAULT_PROVIDER

    for p in PROVIDERS.values():
        p.init_config()

    if len(config.settings.profile.providers):
        # first listed in config
        DEFAULT_PROVIDER = list(config.settings.profile.providers.keys())[0]
    else:
        # first loaded
        DEFAULT_PROVIDER = list(PROVIDERS.keys())[0]

def log_plugin_exception(manager, entrypoint, exception):
    logger.error("Failed to load {entrypoint}")
    logger.error("".join(traceback.format_exc(exception)))

def load():
    global PROVIDERS
    global DEFAULT_PROVIDER
    mgr = extension.ExtensionManager(
        namespace='streamglob.providers',
        on_load_failure_callback=log_plugin_exception,
    )
    PROVIDERS = AttrDict(
        (x.name, x.plugin())
        for x in mgr
    )

    # load_config()
