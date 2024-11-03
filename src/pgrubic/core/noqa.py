"""Handling noqa comments."""

import sys
import typing
import dataclasses

from pglast import parser
from colorama import Fore, Style

from pgrubic import PROGRAM_NAME

A_STAR: str = "*"
ASCII_SEMI_COLON: str = "ASCII_59"
SEMI_COLON: str = ";"
NEW_LINE: str = "\n"


class Statement(typing.NamedTuple):
    """Representation of an SQL statement."""

    start_location: int
    end_location: int
    line_number: int
    text: str


def extract_statement_locations(
    *,
    source_file: str,
    source_code: str,
) -> list[Statement]:
    """Build statements start and end locations."""
    locations: list[Statement] = []

    statement_start_location = 0

    tokens = parser.scan(source_code)

    for idx, token in enumerate(tokens):
        if idx == len(tokens) - 1 and token.name != ASCII_SEMI_COLON:
            sys.stderr.write(
                f"{Fore.RED}Error:{source_file}:Missing statement terminator at location {token.end}{Style.RESET_ALL}\n",  # noqa: E501
            )
            sys.exit(1)

        if token.name == ASCII_SEMI_COLON:
            locations.append(
                Statement(
                    start_location=statement_start_location,
                    end_location=token.end,
                    line_number=source_code[: (token.end)].count(NEW_LINE) + 1,
                    text=source_code[statement_start_location : token.end].strip(NEW_LINE)
                    + SEMI_COLON,
                ),
            )
            statement_start_location = token.end + 1

    return locations


def _get_rules_from_inline_comment(
    comment: str,
    location: int,
    section: str = "noqa",
) -> list[str]:
    """Get rules from inline comment."""
    comment_remainder = comment.removeprefix(section)

    if not comment_remainder:
        return [A_STAR]

    rules: list[str] = [
        rule.strip()
        for rule in comment_remainder.removeprefix(":").split(",")
        if rule and comment_remainder.startswith(":")
    ]

    if not rules:
        sys.stderr.write(
            f"{Fore.YELLOW}Warning: Malformed `noqa` directive at location {location}. Expected `noqa: <rules>`{Style.RESET_ALL}\n",  # noqa: E501
        )

    return rules


def _get_statement_locations(
    locations: list[Statement],
    stop: int,
) -> tuple[int, int]:
    """Get statement start and end locations."""
    for statement_start_location, statement_end_location, _, _ in locations:
        if statement_start_location <= stop < statement_end_location:
            break

    return statement_start_location, statement_end_location


@dataclasses.dataclass(kw_only=True)
class NoQaDirective:
    """Representation of a noqa directive."""

    source_file: str | None = None
    location: int
    line_number: int
    column_offset: int
    rule: str
    used: bool = False


def _extract_statement_ignores(
    source_file: str,
    source_code: str,
) -> list[NoQaDirective]:
    """Extract ignores from SQL statements."""
    locations = extract_statement_locations(
        source_file=source_file,
        source_code=source_code,
    )

    inline_ignores: list[NoQaDirective] = []

    for token in parser.scan(source_code):
        if token.name == "SQL_COMMENT":
            statement_start_location, statement_end_location = _get_statement_locations(
                locations,
                token.start,
            )

            line_number = source_code[:statement_end_location].count("\n") + 1

            # Here we extract last possible noqa because we can have a comment followed
            # by another comment e.g -- new table -- noqa: UN005
            comment = source_code[token.start : (token.end + 1)].split("--")[-1].strip()

            if comment.startswith("noqa"):
                rules = _get_rules_from_inline_comment(comment, token.start)

                inline_ignores.extend(
                    NoQaDirective(
                        location=statement_start_location,
                        line_number=line_number,
                        column_offset=(statement_end_location - token.start),
                        rule=rule,
                    )
                    for rule in rules
                )

    return inline_ignores


def _extract_file_ignore(source_file: str, source_code: str) -> list[NoQaDirective]:
    """Extract ignore from the start of a source file."""
    file_ignores: list[NoQaDirective] = []

    for token in parser.scan(source_code):
        if token.name == "SQL_COMMENT":
            if token.start != 0:
                break

            comment = source_code[token.start : (token.end + 1)].split("--")[-1].strip()

            if comment.strip().startswith(f"{PROGRAM_NAME}: noqa"):
                rules = _get_rules_from_inline_comment(
                    comment,
                    token.start,
                    section=f"{PROGRAM_NAME}: noqa",
                )

                file_ignores.extend(
                    NoQaDirective(
                        source_file=source_file,
                        location=token.start,
                        line_number=1,
                        column_offset=0,
                        rule=rule,
                    )
                    for rule in rules
                )

    return file_ignores


def extract_ignores(*, source_file: str, source_code: str) -> list[NoQaDirective]:
    """Extract ignores from source code."""
    return _extract_statement_ignores(
        source_file=source_file,
        source_code=source_code,
    ) + _extract_file_ignore(
        source_file=source_file,
        source_code=source_code,
    )


def extract_format_ignores(source_file: str, source_code: str) -> list[int]:
    """Extract format ignores from SQL statements."""
    locations = extract_statement_locations(
        source_file=source_file,
        source_code=source_code,
    )

    inline_ignores: list[int] = []

    for token in parser.scan(source_code):
        if token.name == "SQL_COMMENT":
            statement_start_location, _ = _get_statement_locations(
                locations,
                token.start,
            )

            comment = source_code[token.start : (token.end + 1)].split("--")[-1].strip()

            if comment.strip().startswith("fmt"):
                inline_ignores.append(
                    statement_start_location,
                )

    return inline_ignores


class Comment(typing.NamedTuple):
    """Representation of an SQL comment."""

    location: int
    text: str
    at_start_of_line: bool
    continue_previous: bool


def extract_comments(*, source_file: str, source_code: str) -> list[Comment]:
    """Extract comments from SQL statements."""
    locations = extract_statement_locations(
        source_file=source_file,
        source_code=source_code,
    )

    comments: list[Comment] = []
    continue_previous = False

    for token in parser.scan(source_code):
        if token.name in ("C_COMMENT", "SQL_COMMENT"):
            statement_start_location, _ = _get_statement_locations(
                locations,
                token.start,
            )

            comment = source_code[token.start : (token.end + 1)]
            at_start_of_line = not source_code[
                : token.start - statement_start_location
            ].strip()
            comment = source_code[token.start : token.end + 1]
            comments.append(
                Comment(
                    statement_start_location,
                    comment,
                    at_start_of_line,
                    continue_previous,
                ),
            )
            continue_previous = True
        else:
            continue_previous = False
    return comments


def report_unused_ignores(
    *,
    source_file: str,
    inline_ignores: list[NoQaDirective],
) -> None:
    """Get unused ignores."""
    for ignore in inline_ignores:
        if not ignore.used:
            sys.stdout.write(
                f"{source_file}:{ignore.line_number}:{ignore.column_offset}:"
                f" {Fore.YELLOW}Unused noqa directive{Style.RESET_ALL}"
                f" (unused: {Fore.RED}{Style.BRIGHT}{ignore.rule}{Style.RESET_ALL})\n",
            )
