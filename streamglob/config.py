import logging
logger = logging.getLogger(__name__)
import os
import errno
import pytz
try:
    from collections.abc import Mapping, MutableMapping
except ImportError:
    from collections import Mapping, MutableMapping
import yaml
from yamlinclude import YamlIncludeConstructor
import functools
from orderedattrdict import Tree
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import distutils.spawn
import tzlocal

import getpass
import xdg

PACKAGE_NAME="streamglob"

settings = None

def from_yaml_for_type(dict_type, loader, node):
    'Load mapping as AttrDict, preserving order'
    # Based on yaml.constructor.SafeConstructor.construct_mapping()
    d = dict_type()
    yield d
    if not isinstance(node, yaml.MappingNode):
        raise ConstructorError(
            None, None, 'expected a mapping node, but found %s' % node.id, node.start_mark)
    loader.flatten_mapping(node)
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        try:
            hash(key)
        except TypeError as exc:
            raise ConstructorError(
                'while constructing a mapping', node.start_mark,
                'found unacceptable key (%s)' % exc, key_node.start_mark)
        d[key] = loader.construct_object(value_node, deep=False)

class StringTag(str):

  def __init__(self, content):
    self.content = content

  def __repr__(self):
    return self.content

  def __str__(self):
    return self.content

class Folder(StringTag):
    pass

class Union(StringTag):
    pass

def yaml_loader(node_type, base_dir=None):

    from_yaml = functools.partial(from_yaml_for_type, node_type)

    cls_name = f"{node_type.__name__}YAMLLoader"

    def __init__(self, *args, **kwargs):
        super(cls, self).__init__(*args, **kwargs)
        def yaml_join(loader, node):
            seq = loader.construct_sequence(node)
            return ' '.join([str(i) for i in seq])

        def yaml_remove_arg(loader, node):
            orig, remove = node.value
            args = loader.construct_scalar(orig)
            return " ".join(a for a in args.split() if a != remove.value)

        def folder_constructor(loader, node):
            return Folder(node.value)

        def union_constructor(loader, node):
            return Union(node.value)

        self.add_constructor(u'tag:yaml.org,2002:map', from_yaml)
        self.add_constructor(u'tag:yaml.org,2002:omap', from_yaml)
        self.add_constructor('!join', yaml_join)
        self.add_constructor('!remove_arg', yaml_remove_arg)
        self.add_constructor("!folder", folder_constructor)
        self.add_constructor("!union", union_constructor)
        self.add_constructor('!include', YamlIncludeConstructor(base_dir=base_dir))

    d = {"__init__": __init__}

    cls = type(cls_name, (yaml.FullLoader,), d)
    return cls

def yaml_dumper():

    def __init__(self, *args, **kwargs):
        super(cls, self).__init__(*args, **kwargs)

        def folder_representer(dumper, data):
            return dumper.represent_scalar('!folder', data.content)

        def union_representer(dumper, data):
            return dumper.represent_scalar('!union', data.content)

        self.add_representer(Folder, folder_representer)
        self.add_representer(Union, union_representer)

    d = {"__init__": __init__}

    cls = type("ConfigDumper", (yaml.Dumper,), d)
    return cls

class ConfigTree(Tree):

    def get_path(self, keys, default=None):
        return functools.reduce(
            lambda d, key: d.get(key, default)
            if isinstance(d, dict) else default,
            keys.split("."),
            self
        )


def dict_merge(dct, merge_dct):
    # via https://gist.github.com/angstwad/bf22d1822c38a92ec0a9
    dct = dct.copy()
    for k, v in merge_dct.items():
        if (k in dct and isinstance(dct[k], dict)
                and isinstance(merge_dct[k], Mapping)):
            dct[k] = dict_merge(dct[k], merge_dct[k])
        else:
            dct[k] = merge_dct[k]
    return dct

class ProfileTree(ConfigTree):

    DEFAULT_PROFILE_NAME = "default"

    def __init__(self, profile=DEFAULT_PROFILE_NAME, merge_default = False,
                 *args, **kwargs):
        self._merge_default = merge_default
        self._default_profile_name = profile
        self._profile_names = [self._default_profile_name]
        self.__exclude_keys__ |= {"profile_names", "foo", "_default_profile_name",
                                  "_merge_default", "profile"}
        self.include_profile(self._default_profile_name)
        super().__init__(*args, **kwargs)

    @property
    def profile(self):
        d = ConfigTree()
        for pn in self._profile_names:
            d = dict_merge(d, self[pn])
        return d

    @property
    def profile_names(self):
        return self._profile_names

    def include_profile(self, profile):
        if profile == self._default_profile_name:
            return
        logger.debug(f"include_profile: {profile}")
        if not profile in self._profile_names:
            self._profile_names.append(profile)
        logger.debug(f"profiles: {self.profile_names}")

    def exclude_profile(self, profile):
        if profile == self._default_profile_name:
            return
        logger.debug(f"exclude_profile: {profile}")
        try:
            self.profile_names.remove(profile)
        except ValueError:
            pass
        logger.debug(f"profiles: {self.profile_names}")

    def toggle_profile(self, profile):
        if profile in self._profile_names:
            self.exclude_profile(profile)
        else:
            self.include_profile(profile)

    def reset_profiles(self):
        self._profile_names = [ self._default_profile_name ]

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            self[self._profile_name][name] = value
        else:
            object.__setattr__(self, name, value)

    def __getitem__(self, name):
        if isinstance(name, tuple):
            return functools.reduce(
                lambda a, b: ProfileTree(a, **{ k: v for k, v in b.items() if k not in a}),
                [ self[p] for p in reversed(name) ]
            )

        else:
            return super(ProfileTree, self).__getitem__(name)

class Config(ConfigTree):

    DEFAULT_PROFILE = "default"

    DEFAULT_CONFIG_FILE = "config.yaml"

    PACKAGE_HOME = os.path.join(xdg.xdg_config_home(), PACKAGE_NAME)

    DEFAULT_CONFIG_PATH = os.path.expanduser(
        os.path.join(
            PACKAGE_HOME,
            DEFAULT_CONFIG_FILE
        )
    )

    def __init__(self, config_file=None,
                 merge_default = False, *args, **kwargs):
        super(Config, self).__init__(*args, **kwargs)
        self.__exclude_keys__ |= {
            "_config_file", "_config_dir", "include_profile", "_profile_tree",
        }
        self._config_file = config_file or self.DEFAULT_CONFIG_PATH
        self._config_dir = os.path.dirname(self._config_file) or "."
        self.load()
        if "profiles" in self:
            self._profile_tree = ProfileTree(
                **self.profiles,
                merge_default=merge_default
            )

    @property
    def config_file(self):
        return self._config_file

    @property
    def CONFIG_DIR(self):
        return self._config_dir

    @property
    def LOG_FILE(self):
        return os.path.join(self.CONFIG_DIR, f"{PACKAGE_NAME}.log")

    @property
    def profile(self):
        return self._profile_tree.profile

    @property
    def profiles(self):
        return self._profile_tree

    @property
    def profile_names(self):
        return self._profile_tree.profile_names

    def include_profile(self, profile):
        self._profile_tree.include_profile(profile)

    def exclude_profile(self, profile):
        self._profile_tree.exclude_profile(profile)

    def toggle_profile(self, profile):
        self._profile_tree.toggle_profile(profile)

    def load(self):
        if not os.path.exists(self.config_file):
            raise Exception(f"config file {self.config_file} not found")
        loader = yaml_loader(ConfigTree, self._config_dir)

        config = yaml.load(
            open(self.config_file),
            Loader=loader
        )
        self.update(config.items())

    def save(self):

        d = Tree([ (k, v) for k, v in self.items() if k != "profiles"] )
        if "profiles" in self:
            d.update({"profiles": self._profile_tree})
        dumper = yaml_dumper()
        with open(self._config_file, 'w') as outfile:
            yaml.dump(
                d, outfile,
                Dumper=dumper,
                allow_unicode=True,
                default_flow_style=False, indent=4
            )


def load(config_file=None, merge_default=False):
    global settings
    settings = Config(
        config_file = (
            os.path.expanduser(config_file)
            if config_file
            else Config.DEFAULT_CONFIG_PATH
        ),
        merge_default=merge_default
    )

__all__ = [
    "CONFIG_DIR",
    "settings"
]

def main():
    test_settings = Config(
        os.path.expanduser("~/.config/streamglob.feeds"),
        merge_default=True
    )
    # print(test_settings)
    # print(list(test_settings.profile.providers.keys()))
    # test_settings.include_profile("proxy")
    test_settings.profile_names
    print(test_settings.profile.players)
    test_settings.include_profile("small")
    print(test_settings.profile_names)
    print(test_settings.profile.players)
    # print(test_settings.profiles["default"])
    # print(test_settings.profiles[("default")].get("env"))
    # print(test_settings.profiles[("default", "540p")].get("env"))
    # print(test_settings.profiles[("default", "540p")].get("env"))
    # print(test_settings.profiles[("default", "540p", "proxy")].get("env"))

if __name__ == "__main__":
    main()
