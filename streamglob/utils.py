import itertools
import re

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

def format_datetime(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")

def format_timedelta(td):
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return (f"{'%sd' % days if days else ''}"
            f"{hours:02}:{minutes:02}:{seconds:02}")

def format_str_truncated(n, s):
    return s[:n-1] + u"\u2026" if len(s) >= n else s


EMOJI_RE = re.compile(
    "["
        u"\U00000080-\U000002AF"
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
        u"\U00003000-\U0000303F"
        u"\U0000A490-\U0000A4CF"
        u"\U0000E000-\U0000F8FF"
        u"\U0000FE00-\U0000FE0F"
        u"\U0000FE30-\U0000FE4F"
        u"\U0001F000-\U0001F02F"
        u"\U0001F0A0-\U0001F0FF"
        u"\U0001F100-\U0001F64F"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F910-\U0001F96B"
        u"\U0001F980-\U0001F9E0"
"]+", flags=re.UNICODE)
#NON_BMP_RE = re.compile(u"[^\U00000000-\U0000d7ff\U0000e000-\U0000ffff]", flags=re.UNICODE)

def strip_emoji(s):
    return EMOJI_RE.sub("", s)
    # return NON_BMP_RE.sub("", s)

__all__ = [
    "classproperty",
    "valid_date",
    "format_datetime",
    "format_timedelta",
    "strip_emoji"
]
