"""Formula DSL 词法分析器"""
import re
from typing import List
from dataclasses import dataclass


@dataclass
class Token:
    type: str
    value: str
    line: int
    col: int


class Lexer:
    KEYWORDS = {'strategy', 'param', 'if', 'else', 'and', 'or', 'not', 'buy', 'sell', 'sell_all'}

    TOKEN_REGEX = [
        ('STRING', r'"[^"\n]*"'),
        ('NUMBER', r'\d+(?:\.\d+)?'),
        ('IDENTIFIER', r'[a-zA-Z_][a-zA-Z0-9_]*'),
        ('OPERATOR', r'[+\-*/%=<>!]+'),
        ('LPAREN', r'\('),
        ('RPAREN', r'\)'),
        ('LBRACE', r'\{'),
        ('RBRACE', r'\}'),
        ('COLON', r':'),
        ('COMMA', r','),
        ('NEWLINE', r'\n'),
        ('SKIP', r'[ \t]+'),
    ]

    def tokenize(self, code: str) -> List[Token]:
        tokens: List[Token] = []
        index = 0
        line = 1
        col = 1

        while index < len(code):
            matched = False

            for token_type, pattern in self.TOKEN_REGEX:
                match = re.compile(pattern).match(code, index)
                if not match:
                    continue

                value = match.group(0)
                actual_type = token_type
                if token_type == 'IDENTIFIER' and value in self.KEYWORDS:
                    actual_type = value.upper()

                if actual_type != 'SKIP':
                    tokens.append(Token(actual_type, value, line, col))

                index = match.end()
                if value == '\n':
                    line += 1
                    col = 1
                else:
                    col += len(value)

                matched = True
                break

            if not matched:
                raise SyntaxError(f"Unknown token at line {line}, col {col}")

        tokens.append(Token('EOF', '', line, col))
        return tokens
