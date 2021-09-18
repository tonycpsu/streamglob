import os
import itertools
import re
import mistune
import html2text
import html.parser
import pathvalidate

# FIXME
BLANK_IMAGE_URI = """\
data://image/png;base64,\
iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA\
AAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=\
"""

MEDIA_URI_RE=re.compile("uri=(.*)=\.")

def uri_from_filename(filename):
    try:
        return MEDIA_URI_RE.search(filename).groups()[0].replace("+", "/")
    except (IndexError, AttributeError):
        return None

def optional_arg_decorator(fn):
    def wrapped_decorator(*args, **kwargs):
        if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
            return fn(args[0])
        else:
            def real_decorator(decoratee):
                return fn(decoratee, *args, **kwargs)
            return real_decorator
    return wrapped_decorator


FORMAT_DATETIME_12H_RE = re.compile("0*(\d+?:\d+\w)")
def format_datetime_12h(dt):
    s = dt.strftime("%I:%M%p").lower()
    return FORMAT_DATETIME_12H_RE.search(s).group(1)

TIME_FORMATS = {
    None: lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S"),
    "12h": format_datetime_12h,
    "24h": lambda dt: dt.strftime("%H:%M"),
}

def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)

def partition(pred, iterable):
    'Use a predicate to partition entries into false entries and true entries'
    # partition(is_odd, range(10)) --> 0 2 4 6 8   and  1 3 5 7 9
    t1, t2 = itertools.tee(iterable)
    return itertools.filterfalse(pred, t1), filter(pred, t2)

class ClassPropertyDescriptor(object):

    def __init__(self, fget, fset=None):
        self.fget = fget
        self.fset = fset

    def __get__(self, obj, klass=None):
        if klass is None:
            klass = type(obj)
        return self.fget.__get__(obj, klass)()

    def __set__(self, obj, value):
        if not self.fset:
            raise AttributeError("can't set attribute")
        type_ = type(obj)
        return self.fset.__get__(obj, type_)(value)

    def setter(self, func):
        if not isinstance(func, (classmethod, staticmethod)):
            func = classmethod(func)
        self.fset = func
        return self

def classproperty(func):
    if not isinstance(func, (classmethod, staticmethod)):
        func = classmethod(func)

    return ClassPropertyDescriptor(func)

class ClassPropertyMetaClass(type):
    def __setattr__(self, key, value):
        if key in self.__dict__:
            obj = self.__dict__.get(key)
        if obj and type(obj) is ClassPropertyDescriptor:
            return obj.__set__(self, value)

        return super(ClassPropertyMetaClass, self).__setattr__(key, value)


def valid_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)

def format_datetime(t, fmt=None):
    return TIME_FORMATS.get(
        fmt, lambda dt: dt.strftime(fmt)
    )(t) if t is not None else ""

def format_timedelta(td):
    if td is None:
        return ""
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return (f"{'%sd' % days if days else ''}"
            f"{hours:02}:{minutes:02}:{seconds:02}")

ELLIPSIS=u"\u2026"
def format_str_truncated(length, s, end_char=ELLIPSIS, encoding=None):
    """
    Truncate a string based on number of characters (default) or the encoded
    length of the string if `encoding` is set.  Returns the string truncated,
    optionally with a character at the end to denote truncation.
    """
    if encoding:
        encoded = s.encode(encoding)
        if len(encoded) > length:
            encoded = encoded[:length - (len(end_char.encode(encoding)) if end_char else 0)]
            s = encoded.decode(encoding, 'ignore') + (end_char or "")
    else:
        if len(s) > n:
            s = s[:length - 1 if end_char else 0] + (end_char or "")
    return s


# FIXME: some of these ranges include things that aren't exactly "emoji", but
# still confuse Urwid as shown in urwid/urwid#225
EMOJI_RE = re.compile(
    "["
        # u"\U00000080-\U000002AF"
        u"\U00000300-\U000003FF"
        u"\U00000600-\U000006FF"
        u"\U00000C00-\U00000C7F"
        u"\U00001DC0-\U00001DFF"
        u"\U00001E00-\U00001EFF"
        u"\U00002000-\U0000209F"
        u"\U000020D0-\U0000214F"
        u"\U00002190-\U000023FF"
        u"\U00002460-\U000025FF"
        u"\U00002600-\U000027EF"
        u"\U00002900-\U000029FF"
        u"\U00002B00-\U00002BFF"
        u"\U00002C60-\U00002C7F"
        u"\U00002E00-\U00002E7F"
        u"\U00003000-\U00003FFF"
        u"\U0000A490-\U0000A4CF"
        u"\U0000E000-\U0000F8FF"
        u"\U0000FE00-\U0000FE0F"
        u"\U0000FE30-\U0000FFFF"
        u"\U0001F000-\U0001F02F"
        u"\U0001F0A0-\U0001F0FF"
        u"\U0001F100-\U0001F64F"
        u"\U0001F680-\U0001F7FF"
        u"\U0001F900-\U0001FA9f"
"]+", flags=re.UNICODE)
#NON_BMP_RE = re.compile(u"[^\U00000000-\U0000d7ff\U0000e000-\U0000ffff]", flags=re.UNICODE)

def strip_emoji(s):
    return EMOJI_RE.sub("", s)
    # return NON_BMP_RE.sub("", s)


class MLStripper(html.parser.HTMLParser):
    def __init__(self):
        self.reset()
        self.strict = False
        self.convert_charrefs= True
        self.fed = []
    def handle_data(self, d):
        self.fed.append(d)
    def get_data(self):
        return ''.join(self.fed)

def strip_html(html):
    s = MLStripper()
    s.feed(html)
    return s.get_data()

CLEAN_NEWLINES_RE = re.compile(r"\s*\n+\s*")

def clean_text_paragraphs(s):

    return CLEAN_NEWLINES_RE.sub(
        "\n\n",
        s
    )

html_to_text = html2text.HTML2Text()
html_to_text.body_width=0

def stripit(x):
    if isinstance(x, str):
        return x.lstrip(" ")
    elif isinstance(x, tuple):
        if len(x) > 1:
            return (x[0], stripit(x[1:]))
        else:
            return stripit(x[0])
    else:
        return [stripit(xx) for xx in x]


class UrwidMarkdownRenderer(mistune.Renderer):

    def placeholder(self):
        return []

    def linebreak(self):
        return ["\n\n"]

    def newline(self):
        return ["\n"]

    def text(self, text):
        return [(text,)]
        # return [stripit(text)]
        # return [text.strip() if len(text.strip()) else None] or None

    def paragraph(self, text):
        # return [ ("paragraph", [ x for x in stripit(text) if x ])]
        return [ x for x in stripit(text) if x ] + ["\n\n"]
        # return [text + ["\n\n"]]

    def emphasis(self, text):
        return [("italics", text)]

    def codespan(self, text):
        return [("foo", text)]
    def double_emphasis(self, text):
        return [("bold", text)]
        # return [ ("bold", [ x for x in stripit(text) if x ])]

    def link(self, link, title, content):
        return [("link", content)]

    def autolink(self, link, is_email=False):
        return [("link", link)]

    def block_quote(self, link):
        return [("blockquote", link)]

    def inline_html(self, html):
        return [("html", html)]

    def block_html(self, html):
        return [("block_html", html)]

def html_to_urwid_text_markup(html, excludes=[]):

    md2urwid = mistune.Markdown(renderer=UrwidMarkdownRenderer())
    markdown = html_to_text.handle(html)

    markup = md2urwid(strip_emoji(markdown))
    if excludes:
        markup = [
            item
            for item in markup
            for exclude in excludes
            if not exclude(item)
        ]

    # filter out any duplicate line breaks
    return [markup[0]] + [
        b for a, b in pairwise(markup)
        if a != "\n\n"
        or a != b
    ]

def sanitize_filename(t):
    return pathvalidate.sanitize_filename(
        t
        # strip newlines
        .replace("\n", " ")
        # forward slash isn't legal in filenames for UNIX or Windows,
        # so use an underscore instead
        .replace("/", "_")
        # let the pathvalidate module handle the rest for the current platform
        , platform=pathvalidate.normalize_platform(os.name).name
    )



_camel_snake_re_1 = re.compile(r'(.)([A-Z][a-z]+)')
_camel_snake_re_2 = re.compile('([a-z0-9])([A-Z])')

def camel_to_snake(s):
    s = _camel_snake_re_1.sub(r'\1_\2', s)
    return _camel_snake_re_2.sub(r'\1_\2', s).lower()

def snake_to_camel(s):
    words = s.split('_')
    return words[0] + ''.join(x.title() for x in words[1:])

# def snake_to_class_name(s):
#     words = s.split('_')
#     return words[0].title() + ''.join(x.title() for x in words[1:])

# def snake_to_friendly_name(s):
#     return s.replace("_", " ").title()


__all__ = [
    "classproperty",
    "valid_date",
    "format_datetime",
    "format_timedelta",
    "strip_emoji",
    "strip_html",
    "html_to_urwid_text_markup",
    "sanitize_filename",
    "camel_to_snake",
    "snake_to_camel"
]
