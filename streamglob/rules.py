import re
from orderedattrdict import AttrDict
from itertools import chain
from collections.abc import MutableSequence
import yaml
import os
from functools import reduce
from itertools import groupby

class HighlightRule(object):

    def __init__(self, patterns, subjects=[], group=None):
        if not isinstance(patterns, list):
            patterns = [patterns]
        self.patterns = patterns
        self._re = re.compile("|".join(self.patterns))
        self._subjects = subjects
        self._group = group

    @property
    def subjects(self):
        return self._subjects or self.patterns

    @property
    def group(self):
        return self._group

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

    def __repr__(self):
        return f"<HighlightRule: {self.patterns}, {self.subjects}: {self.group}>"


class HighlightRuleList(MutableSequence):

    def __init__(self, attr, rules):
        self.attr = attr
        self.rules = [ HighlightRule(rule) if isinstance(rule, str) else HighlightRule(**rule) for rule in rules ]
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

    def __iter__(self): return iter(self.rules)

    def __len__(self): return len(selfrules)

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

    def __init__(self, config):
        self.config = config
        self.rules = AttrDict([
            (label, HighlightRuleList(self.config["highlight"][label], rules))
            for label, rules in self.config["label"].items()
        ])
        self.pattern_rules = '|'.join(
            f"{rules.pattern}"
            for label, rules in self.rules.items()
        )
        self.pattern_rules_grouped = '|'.join(
            f"(?P<{label}>{rules.pattern})"
            for label, rules in self.rules.items()
        )
        self.pattern_tokens = f"{self.pattern_rules_grouped}|(?P<none>(?!{self.pattern_rules})(.+?))"
        self._re_tokens = re.compile(self.pattern_tokens)

    def __getitem__(self, key):
        return self.rules[key]

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

    def tokenize(self, text):

        tokens = (
            (match.lastgroup, match.group())
            for match in re.finditer(self._re_tokens, text)
        )

        out = []
        for k, g in groupby(tokens, lambda x: x[0] == "none"):
            if k:
                out.append((None, "".join(item[1] for item in g)))
            else:
                out.append(next(g))
        return out


    def apply(self, text):
        return [
            (self.config["highlight"][t[0]], t[1]) if t[0] else t[1]

            for t in self.tokenize(text)
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
