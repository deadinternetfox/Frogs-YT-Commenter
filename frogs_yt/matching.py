"""matching.py — a small, dependency-free query language for comments.

Used by the live harvest (core.list_comments), the spider daemon, and the AI
agent's DB search, so matching behaves identically everywhere. Pure functions +
plain objects; trivially unit-testable.

Grammar (case-insensitive substring matching by default)
--------------------------------------------------------
    where to buy          -> AND of bare terms ("where" AND "to" AND "buy")
    "where to buy"        -> exact phrase (quotes group it)
    price OR cost         -> OR joins groups; matches if EITHER side matches
    -giveaway             -> exclude (must NOT contain)
    likes:>=5             -> numeric filter (>=, >, <=, <, = ; bare N means >=N)
    author:frog           -> author name contains
    after:2026-01-01      -> published on/after a date (also before:)
    is:question           -> heuristic: contains '?'
    is:reply / is:toplevel-> thread position

Top level is an OR of AND-groups: a comment matches when at least one group has
ALL its conditions satisfied. An empty query matches everything.
"""

import shlex

_FIELDS = ("likes", "author", "after", "before", "is")


# --------------------------------------------------------------------------
# Field accessors that tolerate both core's dict shape and db.py rows
# --------------------------------------------------------------------------
def _text(c):
    return (c.get("text") or "")


def _author(c):
    return (c.get("author") or "")


def _likes(c):
    try:
        return int(c.get("likes", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _published(c):
    return (c.get("published") or "")


def _is_reply(c):
    return bool(c.get("isReply") if "isReply" in c else c.get("is_reply"))


# --------------------------------------------------------------------------
# Conditions — each can self-evaluate and (best-effort) emit SQL
# --------------------------------------------------------------------------
class _Cond:
    def matches(self, c, v=None):
        raise NotImplementedError

    def sql(self):
        """Return (clause, params) for a SQL pre-filter, or None if Python-only."""
        return None


class _Include(_Cond):
    def __init__(self, term):
        self.term = term.lower()

    def matches(self, c, v=None):
        return self.term in _text(c).lower()

    def sql(self):
        return ("LOWER(text) LIKE ?", [f"%{self.term}%"])


class _Exclude(_Cond):
    def __init__(self, term):
        self.term = term.lower()

    def matches(self, c, v=None):
        return self.term not in _text(c).lower()

    def sql(self):
        return ("(text IS NULL OR LOWER(text) NOT LIKE ?)", [f"%{self.term}%"])


class _Likes(_Cond):
    def __init__(self, op, n):
        self.op, self.n = op, n

    def matches(self, c, v=None):
        x = _likes(c)
        return {">=": x >= self.n, ">": x > self.n, "<=": x <= self.n,
                "<": x < self.n, "=": x == self.n}[self.op]

    def sql(self):
        return (f"likes {self.op} ?", [self.n])


class _Author(_Cond):
    def __init__(self, term):
        self.term = term.lower()

    def matches(self, c, v=None):
        return self.term in _author(c).lower()

    def sql(self):
        return ("LOWER(author) LIKE ?", [f"%{self.term}%"])


class _After(_Cond):
    def __init__(self, date):
        self.date = date

    def matches(self, c, v=None):
        return _published(c) >= self.date

    def sql(self):
        return ("published >= ?", [self.date])


class _Before(_Cond):
    def __init__(self, date):
        self.date = date

    def matches(self, c, v=None):
        return _published(c) < self.date

    def sql(self):
        return ("published < ?", [self.date])


class _IsFlag(_Cond):
    def __init__(self, flag):
        self.flag = flag

    def matches(self, c, v=None):
        if self.flag == "question":
            return "?" in _text(c)
        if self.flag == "reply":
            return _is_reply(c)
        if self.flag == "toplevel":
            return not _is_reply(c)
        return True

    def sql(self):
        if self.flag == "reply":
            return ("is_reply = 1", [])
        if self.flag == "toplevel":
            return ("is_reply = 0", [])
        if self.flag == "question":
            return ("text LIKE ?", ["%?%"])
        return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _parse_field(token):
    """token like 'likes:>=5' / 'author:foo' / 'is:reply' -> a _Cond, or None."""
    field, _, value = token.partition(":")
    field = field.lower()
    if field not in _FIELDS or not value:
        return None
    if field == "likes":
        op = "="
        for cand in (">=", "<=", ">", "<", "="):
            if value.startswith(cand):
                op, value = cand, value[len(cand):]
                break
        else:
            op = ">="  # bare number means "at least"
        try:
            return _Likes(op, int(value))
        except ValueError:
            return None
    if field == "author":
        return _Author(value)
    if field == "after":
        return _After(value)
    if field == "before":
        return _Before(value)
    if field == "is":
        return _IsFlag(value.lower())
    return None


class Matcher:
    """Compiled query: an OR of AND-groups of conditions."""

    def __init__(self, groups):
        self.groups = groups  # list[list[_Cond]]

    @property
    def is_empty(self):
        return not any(self.groups)

    def matches(self, comment, video=None):
        if self.is_empty:
            return True
        return any(all(cond.matches(comment, video) for cond in group)
                   for group in self.groups if group)

    def score(self, comment, video=None):
        """Rough relevance: satisfied include-terms + light likes/recency weight."""
        if not self.matches(comment, video):
            return 0.0
        hits = 0
        for group in self.groups:
            for cond in group:
                if isinstance(cond, (_Include, _Author)) and cond.matches(comment, video):
                    hits += 1
        return hits + min(_likes(comment), 1000) * 0.001

    def to_sql(self):
        """Best-effort (where_clause, params) pre-filter for db.query_comments.

        Python matches() still runs on the candidates for full precision, so the
        SQL only needs to be a non-over-filtering superset: conditions it can't
        express are simply omitted (treated as always-true).
        """
        if self.is_empty:
            return ("1=1", [])
        or_parts, params = [], []
        for group in self.groups:
            and_parts = []
            for cond in group:
                s = cond.sql()
                if s:
                    clause, p = s
                    and_parts.append(clause)
                    params.extend(p)
            or_parts.append("(" + " AND ".join(and_parts) + ")" if and_parts else "1=1")
        return (" OR ".join(or_parts) if or_parts else "1=1", params)


def compile_query(query_str):
    """Parse a query string into a Matcher. Robust to malformed quoting."""
    query_str = (query_str or "").strip()
    if not query_str:
        return Matcher([])
    try:
        tokens = shlex.split(query_str)
    except ValueError:
        tokens = query_str.split()
    groups, current = [], []
    for tok in tokens:
        if tok == "OR":
            groups.append(current)
            current = []
            continue
        if tok.startswith("-") and len(tok) > 1:
            current.append(_Exclude(tok[1:]))
            continue
        field_cond = _parse_field(tok)
        if field_cond is not None:
            current.append(field_cond)
            continue
        current.append(_Include(tok))
    groups.append(current)
    return Matcher([g for g in groups if g])


def from_words(words):
    """Compile a legacy match_words list to an OR-of-words matcher.

    Preserves the old core.list_comments behavior: keep a comment if it contains
    ANY of the words.
    """
    words = [w for w in (words or []) if w and w.strip()]
    if not words:
        return Matcher([])
    return Matcher([[_Include(w)] for w in words])


def filter_comments(comments, query_str, video=None):
    m = compile_query(query_str)
    return [c for c in comments if m.matches(c, video)]
