import logging
logger = logging.getLogger(__name__)

import abc
import re
from functools import wraps
import traceback

from stevedore import extension
from orderedattrdict import AttrDict, Tree

# from .. import session
from .. import config
from ..exceptions import *

PROVIDERS = AttrDict()
DEFAULT_PROVIDER = None

# FOO=1
def get(provider, *args, **kwargs):
    # global FOO
    # FOO+=1
    # if FOO > 5:
    #     raise Exception("get")
    try:
        return PROVIDERS.get(provider)
    except TypeError:
        return None
        # raise Exception(provider, PROVIDERS)

URI_SPEC_RE=re.compile(r"(?:(\w+)://)?([^:/]*)(.*)")

def parse_uri(uri):

    options = AttrDict()

    if not uri:
        uri = DEFAULT_PROVIDER

    (action, provider, spec) = URI_SPEC_RE.search(uri).groups()
    if not action:
        action = "play"

    p = get(provider) or get(next(iter(PROVIDERS.keys())))

    if not p:
        raise Exception(f"provider {provider} not found")

    selection, options = p.parse_spec(spec)
    return (action, p, selection, options)


def load_config(default=None):

    global DEFAULT_PROVIDER

    if default:
        DEFAULT_PROVIDER = default
    elif len(config.settings.profile.providers):
        # first listed in config
        DEFAULT_PROVIDER = list(config.settings.profile.providers.keys())[0]
    else:
        # first loaded
        DEFAULT_PROVIDER = list(PROVIDERS.keys())[0]

    for p in PROVIDERS.values():
        p.init_config()

def apply_settings():
    for p in PROVIDERS.values():
        p.apply_settings()


def log_plugin_exception(manager, entrypoint, exception):
    logger.error("Failed to load {entrypoint}")
    logger.error(
        "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
    )


DISABLED_PROVIDERS = ["twitch"] # Broken

def load(providers):
    global PROVIDERS

    logger.info("loading providers")

    mgr = extension.ExtensionManager(
        namespace='streamglob.providers',
        on_load_failure_callback=log_plugin_exception,
    )
    PROVIDERS = AttrDict(
        (x.name, x.plugin())
        for x in mgr
        if x.name not in DISABLED_PROVIDERS
        and (providers is None or x.name in providers)
    )

