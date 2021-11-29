import re
from orderedattrdict import AttrDict
from itertools import chain
from collections.abc import MutableSequence
import yaml
import os
from functools import reduce
from itertools import groupby

from . import config

class HighlightRule(object):

    def __init__(self, subject, group=None, patterns=None):
        self.subject = subject
        self._group = group
        self._patterns = patterns
        self._re = re.compile("|".join(self.patterns))

    @property
    def patterns(self):
        return self._patterns or  [self.subject]

    @property
    def group(self):
        return self._group or self.subject

    def search(self, text, aliases=[]):
        return self._re.search(text) or next(
            (
                match for match in (
                    re.search(a, text)
                    for a in aliases
                )
                if match
            ),
            None
        )

    def __getitem__(self, key):
        return getattr(self, key)

    def __eq__(self, other):
        return self.subject == other.subject

    def __ne__(self, other):
        return self.subject != other.subject

    def __lt__(self, other):
        return self.subject < other.subject

    def __le__(self, other):
        return self.subject <= other.subject

    def __gt__(self, other):
        return self.subject > other.subject

    def __ge__(self, other):
        return self.subject >= other.subject

    def __repr__(self):
        return f"<HighlightRule: {self.subject} ({self.group}) {self.patterns}>"

    def to_dict(self):
        d = AttrDict(subject=self.subject)
        if self._group and self._group != self.subject:
            d["group"] = self._group
        if self._patterns and self._patterns != [self.subject]:
            d["patterns"] = self._patterns
        return d

class HighlightRuleList(MutableSequence):

    def __init__(self, attr, rules):
        self.attr = attr
        self.rules = [
            rule
            if isinstance(rule, HighlightRule)
            else HighlightRule(rule)
            if isinstance(rule, str)
            else HighlightRule(**rule)
            for rule in rules
        ]
        if any([type(r.patterns) != list for r in self.rules ]):
            raise Exception([ r.patterns for r in self.rules ])
        self.pattern = "|".join(
            list(chain.from_iterable(
            rule.patterns
            for rule in self.rules
        )))
        self._re_search = re.compile(self.pattern)
        self._re_apply = re.compile(
            f"\\b({self.pattern})|(?!{self.pattern})(.+?)"
        )

    def __repr__(self):
        return f"<HighlightRuleList: {self.attr}, {self.rules}>"

    def __iter__(self): return iter(self.rules)

    def __len__(self): return len(self.rules)

    def __setitem__(self, i, v): self.rules[i] = v

    def __getitem__(self, i): return self.rules[i]

    def __delitem__(self, i): del self.rules[i]

    def insert(self, i, v): self.rules.insert(i, v)

    def __contains__(self, rule):
        return rule in self.rules

    def __len__(self):
        return len(self.rules)

    def search(self, text):
        return self._re_search.search(text)

    def findall(self, text):
        return self._re_search.findall(text)

    def apply(self, text):
        out = []
        for k, g in groupby(self._re_apply.findall(text), lambda x: not x[0]):
            if k:
                out.append(("".join(item[1] for item in g),))
            else:
                print(list(g))
                out += [(self.attr, list(g)[0][0])]
        return out

    def rule_for_token(self, token):
        return next(
            (
                r for r in self.rules
                if r.search(
                    token if isinstance(token, str) else token.get("name"),
                    aliases=[] if isinstance(token, str) else token.get("aliases")
                )
            ),
            None
        )


class HighlightRuleConfig(object):

    # def __init__(self, config):
    def __init__(self, config_file):
        self._config_file = config_file
        self.config = config.Config(
            self._config_file
        )
        self.rules = AttrDict([

            # (label, HighlightRuleList(self.highlight_config.get(label), rule_list))
            # for label, rule_list in self.label_config.items()

            (label,
             HighlightRuleList(
                 self.highlight_config.get(label),
                 [ dict(subject=k, **(v or {})) for k, v in rule_dict.items() ]
             )
             )
            for label, rule_dict in self.label_config.items()
        ])
        self.RE_MAP = dict()

    @property
    def highlight_config(self):
        return self.config.get("highlight", {})

    @property
    def label_config(self):
        return self.config.get("label", {})

    @property
    def labels(self):
        return self.label_config.keys()

    def __getitem__(self, key):
        return self.rules[key]

    def add_rule(self, label, subject, group=None, patterns=None):
        self.remove_rule([subject])
        rule = HighlightRule(subject, group=group, patterns=patterns)
        self.rules[label].append(rule)
        self.save()

    def remove_rule(self, targets):
        if not isinstance(targets, list):
            targets = [targets]
        self.rules = AttrDict([
            (label, HighlightRuleList(
                self.highlight_config.get(label),
                [
                    r for r in self.rules[label]
                    if r.subject not in targets
                    and not any(pattern in targets for pattern in r.patterns)
                ]
            ))
            for label, rule_list in self.rules.items()
        ])

        self.save()

    def save(self):

        self.config.label = {
            label: {
                d.subject: {
                    k: v for k, v in d.items()
                    if k != "subject"
                } or None
                for d in [
                    rule.to_dict()
                    for rule in sorted(rule_list)
                ]
            }
            for label, rule_list in self.rules.items()
        }

        self.config.save()
        # temp_config = config.Config(
        #     self._config_file + ".new.yaml"
        # )
        # temp_config.update(self.config.tree)

        # temp_config.label = {
        #     label: {
        #         d.subject: {
        #             k: v for k, v in d.items()
        #             if k != "subject"
        #         } or None
        #         for d in [
        #             rule.to_dict()
        #             for rule in sorted(rule_list)
        #         ]
        #     }
        #     for label, rule_list in self.rules.items()
        # }
        # temp_config.save()

    def get_regex(self, rules):
        rules_set = frozenset(rules.items())
        if rules_set not in self.RE_MAP:
            pattern = '|'.join(
                f"{rules_list.pattern}"
                for label, rules_list in rules.items()
                if len(rules_list)
            )
            pattern_grouped = '|'.join(
                f"(?P<{label}>{rules_list.pattern})"
                for label, rules_list in rules.items()
                if len(rules_list)
            )
#             pattern_tokens = f"{pattern_grouped}|(?P<none>(?!{pattern})(.+?))"
            pattern_tokens = "|".join([
                p for p in [pattern_grouped, "(?P<none>(?!{pattern})(.+?))"]
                if len(p)
            ])
            self.RE_MAP[rules_set] = tuple(
                re.compile(p)
                for p in [pattern, pattern_grouped, pattern_tokens]
            )
        return self.RE_MAP[rules_set]

    @property
    def pattern_rules(self):
        return self.get_regex(self.rules)[0]

    @property
    def pattern_rules_grouped(self):
        return self.get_regex(self.rules)[1]

    @property
    def pattern_rules_tokens(self):
        return self.get_regex(self.rules)[2]


    def search(self, text):
        return next(
            (
                match for match in (
                    rules.search(text)
                    for rules in self.rules.values()
                )
                if match
            ),
            None
        )

    def tokenize(self, text, candidates=[], aliases={}):

        for subject, alias_list in aliases.items():
            text = re.sub("|".join(
                (
                    # The this lookahead here avoids replacements where the
                    # alias is a prefix of the subject
                    f"(?!{subject}){alias}"
                    for alias in alias_list
                )

            ), subject, text)

        if candidates:
            rules = AttrDict([
                (label, HighlightRuleList(
                    self.highlight_config[label],
                    [
                        r for r in rule_list
                        if r.subject in candidates
                    ]
                ))
                for label, rule_list in self.rules.items()
            ])
        else:
            rules = self.rules

        (pattern, pattern_grouped, pattern_tokens) = self.get_regex(rules)

        tokens = (
            (match.lastgroup, match.group())
            for match in re.finditer(pattern_tokens, text)
        )

        out = []
        for k, g in groupby(tokens, lambda x: x[0] == "none"):
            if k:
                out.append((None, "".join(item[1] for item in g)))
            else:
                out.append(next(g))
        return out


    def apply(self, text, candidates=[], aliases={}):
        return [
            (self.highlight_config.get(label), token) if label else token
            for (label, token) in self.tokenize(text, candidates=candidates, aliases=aliases)
        ]

    def get_tokens(self, text, candidates=[], aliases={}):
        return [
            token
            for (label, token) in self.tokenize(text, candidates=candidates, aliases=aliases)
            if label
        ]

    def rule_for_token(self, token):
        return next(
            (
                rule for rule in (
                    rules.rule_for_token(token)
                    for label, rules in self.rules.items()
                )
                if rule
            ),
            None
        )
