"""SPDX license expressions, checked against the licenses plugins are likely to use.

The list is not the full SPDX list. Add an identifier from https://spdx.org/licenses/ when a plugin
needs one; `LicenseRef-<name>` is accepted for anything else.
"""

import re

LICENSES = frozenset(
    """
    0BSD AFL-3.0 AGPL-3.0-only AGPL-3.0-or-later Apache-2.0 Artistic-2.0 BlueOak-1.0.0
    BSD-1-Clause BSD-2-Clause BSD-2-Clause-Patent BSD-3-Clause BSD-3-Clause-Clear BSD-4-Clause
    BSL-1.0 CC-BY-4.0 CC-BY-SA-4.0 CC0-1.0 CDDL-1.0 ECL-2.0 EPL-1.0 EPL-2.0 EUPL-1.1 EUPL-1.2
    GPL-2.0-only GPL-2.0-or-later GPL-3.0-only GPL-3.0-or-later ISC LGPL-2.0-only LGPL-2.0-or-later
    LGPL-2.1-only LGPL-2.1-or-later LGPL-3.0-only LGPL-3.0-or-later MIT MIT-0 MPL-1.1 MPL-2.0
    MPL-2.0-no-copyleft-exception MS-PL MS-RL NCSA ODbL-1.0 OFL-1.1 OSL-3.0 PostgreSQL Python-2.0
    Unicode-3.0 Unicode-DFS-2016 Unlicense UPL-1.0 W3C WTFPL Zlib
    """.split()
)

EXCEPTIONS = frozenset(
    """
    Classpath-exception-2.0 GCC-exception-3.1 LLVM-exception OCaml-LGPL-linking-exception
    """.split()
)

_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+-]+")


def is_valid(expression: str) -> bool:
    """True for an identifier, or identifiers joined by AND, OR and WITH, with parentheses."""
    if not isinstance(expression, str) or not expression.strip():
        return False
    tokens = _TOKEN.findall(expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    pos = 0

    def license_term() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            return False
        token = tokens[pos]
        if token == "(":
            pos += 1
            if not expr() or pos >= len(tokens) or tokens[pos] != ")":
                return False
            pos += 1
            return True
        ident = token[:-1] if token.endswith("+") else token
        if ident not in LICENSES and not re.fullmatch(r"LicenseRef-[A-Za-z0-9.-]+", ident):
            return False
        pos += 1
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            if pos >= len(tokens) or tokens[pos] not in EXCEPTIONS:
                return False
            pos += 1
        return True

    def expr() -> bool:
        nonlocal pos
        if not license_term():
            return False
        while pos < len(tokens) and tokens[pos] in ("AND", "OR"):
            pos += 1
            if not license_term():
                return False
        return True

    return expr() and pos == len(tokens)
