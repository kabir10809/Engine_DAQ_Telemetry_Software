"""Safe evaluator for user formulas in derived channels and cross-channel rules.

A small parser and a function whitelist, never eval, so a setup file cannot
execute arbitrary code. The grammar is shared with the MATLAB build.
"""
from __future__ import annotations

import math
import re

import numpy as np


class ExpressionError(ValueError):
    pass


def _if(condition, a, b):
    return np.where(np.asarray(condition) != 0, a, b)[()]


FUNCTIONS = {
    "sin": (np.sin, 1), "cos": (np.cos, 1), "tan": (np.tan, 1), "asin": (np.arcsin, 1), "acos": (np.arccos, 1),
    "atan": (np.arctan, 1), "atan2": (np.arctan2, 2), "sqrt": (np.sqrt, 1), "abs": (np.abs, 1), "exp": (np.exp, 1),
    "log": (np.log, 1), "log10": (np.log10, 1), "floor": (np.floor, 1), "ceil": (np.ceil, 1), "round": (np.round, 1),
    "sign": (np.sign, 1), "min": (np.minimum, 2), "max": (np.maximum, 2), "pow": (np.power, 2), "if": (_if, 3),
    "clamp": (lambda x, lo, hi: np.minimum(np.maximum(x, lo), hi), 3),
}
CONSTANTS = {"pi": math.pi, "e": math.e}
_TOKEN = re.compile(r"\s*(?:(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|(?P<id>[A-Za-z_]\w*)"
                    r"|(?P<op><=|>=|==|!=|&&|\|\||[-+*/%^<>!(),]))")


def _tokenize(source: str):
    tokens, position = [], 0
    source = source.rstrip()
    while position < len(source):
        match = _TOKEN.match(source, position)
        if not match:
            raise ExpressionError(f'Illegal character "{source[position:].lstrip()[:1]}" in expression "{source}".')
        tokens.append((match.lastgroup, match[match.lastgroup]))
        position = match.end()
    tokens.append(("end", "<end of expression>"))
    return tokens


class _Parser:
    def __init__(self, source):
        self.tokens = _tokenize(source)
        self.position = 0

    def peek_op(self, *ops) -> bool:
        kind, text = self.tokens[self.position]
        return kind == "op" and text in ops

    def advance(self):
        token = self.tokens[self.position]
        self.position += 1
        return token

    def chain(self, ops, next_level):
        node = next_level()
        while self.peek_op(*ops):
            op = self.advance()[1]
            node = ("bin", op, node, next_level())
        return node

    def parse_or(self):
        return self.chain(("||",), self.parse_and)

    def parse_and(self):
        return self.chain(("&&",), self.parse_compare)

    def parse_compare(self):
        return self.chain(("<", "<=", ">", ">=", "==", "!="), self.parse_add)

    def parse_add(self):
        return self.chain(("+", "-"), self.parse_mul)

    def parse_mul(self):
        return self.chain(("*", "/", "%"), self.parse_unary)

    def parse_unary(self):
        if self.peek_op("-", "!", "+"):
            op = self.advance()[1]
            return ("un", op, self.parse_unary())
        return self.parse_power()

    def parse_power(self):
        node = self.parse_atom()
        if self.peek_op("^"):
            self.advance()
            node = ("bin", "^", node, self.parse_unary())    # right associative
        return node

    def parse_atom(self):
        kind, text = self.advance()
        if kind == "num":
            return ("num", float(text))
        if kind == "id":
            if self.peek_op("("):
                return self.parse_call(text)
            if text in CONSTANTS:
                return ("num", CONSTANTS[text])
            return ("var", text)
        if kind == "op" and text == "(":
            node = self.parse_or()
            if not self.peek_op(")"):
                raise ExpressionError("Missing closing parenthesis.")
            self.advance()
            return node
        raise ExpressionError(f'Unexpected "{text}".')

    def parse_call(self, name):
        if name not in FUNCTIONS:
            raise ExpressionError(f'Function "{name}" is not allowed.')
        self.advance()
        args = []
        if not self.peek_op(")"):
            while True:
                args.append(self.parse_or())
                if not self.peek_op(","):
                    break
                self.advance()
        if not self.peek_op(")"):
            raise ExpressionError(f'Missing ")" after arguments of {name}.')
        self.advance()
        if len(args) != FUNCTIONS[name][1]:
            raise ExpressionError(f"{name} expects {FUNCTIONS[name][1]} argument(s), got {len(args)}.")
        return ("call", name, args)


_BINARY = {
    "+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
    "%": np.mod, "^": np.power, "<": lambda a, b: np.float64(a < b), "<=": lambda a, b: np.float64(a <= b),
    ">": lambda a, b: np.float64(a > b), ">=": lambda a, b: np.float64(a >= b),
    "==": lambda a, b: np.float64(a == b), "!=": lambda a, b: np.float64(a != b),
    "&&": lambda a, b: np.float64((a != 0) & (b != 0)), "||": lambda a, b: np.float64((a != 0) | (b != 0)),
}


class Expression:
    def __init__(self, source: str):
        self.source = str(source)
        parser = _Parser(self.source)
        self._ast = parser.parse_or()
        if parser.tokens[parser.position][0] != "end":
            raise ExpressionError(f'Unexpected "{parser.tokens[parser.position][1]}" in expression "{source}".')
        names: list[str] = []
        self._collect(self._ast, names)
        self.inputs = list(dict.fromkeys(names))

    def evaluate(self, variables: dict):
        with np.errstate(all="ignore"):
            return self._eval(self._ast, variables)

    def _collect(self, node, names):
        kind = node[0]
        if kind == "var":
            names.append(node[1])
        elif kind == "un":
            self._collect(node[2], names)
        elif kind == "bin":
            self._collect(node[2], names)
            self._collect(node[3], names)
        elif kind == "call":
            for arg in node[2]:
                self._collect(arg, names)

    def _eval(self, node, variables):
        kind = node[0]
        if kind == "num":
            return np.float64(node[1])
        if kind == "var":
            if node[1] not in variables:
                raise ExpressionError(f'Unknown input "{node[1]}".')
            return np.float64(variables[node[1]])
        if kind == "un":
            value = self._eval(node[2], variables)
            return {"-": -value, "+": value, "!": np.float64(value == 0)}[node[1]]
        if kind == "bin":
            return _BINARY[node[1]](self._eval(node[2], variables), self._eval(node[3], variables))
        return FUNCTIONS[node[1]][0](*[self._eval(arg, variables) for arg in node[2]])
