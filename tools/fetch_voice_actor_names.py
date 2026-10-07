"""Fetch reviewable name aliases from a Moegirl voice actor infobox.

Example: python tools/fetch_voice_actor_names.py --name "伊達 さゆり" --title "伊达小百合"
Only prints a proposal; never changes the catalogue, images or birthdays.
"""
import argparse
import html
import json
import re
import unicodedata
from urllib.parse import quote

import httpx


def clean_cell(value):
    value = re.sub(r'<(rt|rp|s|del|sup)\b[^>]*>.*?</\1>', '', value, flags=re.S | re.I)
    value = re.sub(r'<span\b[^>]*class="[^"]*template-ruby-hidden[^"]*"[^>]*>.*?</span>', '', value, flags=re.S)
    value = re.sub(r'\s+', ' ', value)
    value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
    return html.unescape(re.sub(r'<[^>]+>', '', value)).strip()


def name_key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def parse_names(markup, expected_name, title):
    table = re.search(r'<table\b[^>]*class="[^"]*\bmoe-infobox\b[^"]*"[^>]*>(.*?)</table>', markup, re.S | re.I)
    if not table:
        raise ValueError('No public voice actor infobox; keep existing data')
    fields = {}
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table[1], re.S | re.I):
        cells = re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, re.S | re.I)
        if len(cells) == 2:
            fields[clean_cell(cells[0])] = clean_cell(cells[1])
    names = [fields.get(key, '') for key in ('姓名', '本名', '日文名', '日文原名')]
    japanese = [re.sub(r'[（(].*?[）)]', '', line).strip() for value in names for line in value.splitlines()]
    if name_key(expected_name) not in {name_key(value) for value in japanese}:
        raise ValueError('Japanese name does not match the existing actor; manual review required')
    romanized = []
    for value in [*names, *(fields.get(key, '') for key in ('罗马字', '罗马音', '英文名'))]:
        for part in re.split(r'[\n（）()]', value):
            part = part.strip()
            if re.fullmatch(r"[A-Za-zÀ-ž]+(?:[ '-][A-Za-zÀ-ž]+){1,3}", part):
                romanized.append(part)
    aliases = [title, *japanese]
    aliases = [value for value in aliases if value and not value.isascii()]
    try:
        from opencc import OpenCC
        for mode in ('s2t', 't2s'):
            aliases.extend(OpenCC(mode).convert(value) for value in list(aliases))
    except ImportError:
        pass
    return {'aliases': list(dict.fromkeys(aliases)), 'romanized_names': list(dict.fromkeys(romanized)),
            'sources': ['https://zh.moegirl.org.cn/' + quote(title)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True, help='Existing official Japanese name for identity verification')
    parser.add_argument('--title', required=True, help='Exact Moegirl article title')
    args = parser.parse_args()
    response = httpx.get('https://zh.moegirl.org.cn/' + quote(args.title), timeout=25, follow_redirects=True)
    response.raise_for_status()
    print(json.dumps(parse_names(response.text, args.name, args.title), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
